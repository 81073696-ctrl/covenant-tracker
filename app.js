/* 卫戍协议 · 记牌器 —— 前端逻辑（无框架、无构建） */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const TIER_CLASS = (t) => `t${Math.max(1, Math.min(6, Number(t) || 1))}`;

let PACK = { operators: {}, bonds: {}, bands: [], summary: {} };
let SNAP = null;
let SNAP_ETAG = null;
let STATS = null;
let VIEW = "match";
let editingShop = false;
let pollTimer = null;

/* ---------------------------------------------------------------- 网络 */

async function api(path, data) {
  const opt = data
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }
    : {};
  const res = await fetch(path, opt);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j && j.error) msg = j.error;
    } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

async function act(type, args = {}) {
  try {
    const res = await api("/api/action", { type, args });
    if (!res.ok) toast(res.error || "操作失败", true);
    await refresh();
    return res;
  } catch (err) {
    toast(String(err.message || err), true);
  }
}

let toastTimer = null;
function toast(message, bad = false) {
  const t = $("#toast");
  t.textContent = message;
  t.classList.toggle("bad", !!bad);
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 2600);
}

/* ---------------------------------------------------------------- 启动 */

async function boot() {
  try {
    const [pack, snap] = await Promise.all([api("/api/pack"), api("/api/snapshot")]);
    PACK = { ...PACK, ...pack };
    SNAP = snap;
    fillModeSelect();
    render();
    if (location.hash === "#stats") switchView("stats");
    if (location.hash === "#settings") openSettings();
    setConn(true);
  } catch (err) {
    setConn(false, String(err.message || err));
  }
  pollTimer = setInterval(() => {
    if (!document.hidden) refresh();
  }, 1400);
}

function setConn(ok, detail = "") {
  $("#conn-dot").classList.toggle("ok", ok);
  $("#conn-text").textContent = ok ? "已连接" : "连接失败";
  $("#conn-text").title = detail;
}

async function refresh() {
  try {
    const opt = SNAP_ETAG ? { headers: { "If-None-Match": SNAP_ETAG } } : {};
    const [res] = await Promise.all([fetch("/api/snapshot", opt), refreshCapture()]);
    if (res.status === 304) {
      setConn(true);
      return;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    SNAP_ETAG = res.headers.get("ETag") || null;
    SNAP = await res.json();
    renderLedgerWarn();
    if (VIEW === "match") render();
    setConn(true);
  } catch (err) {
    setConn(false, String(err.message || err));
  }
}

let CAPTURE = { on: false, interval: 2, reads: 0, changed: 0, error: null, source: null };

async function refreshCapture() {
  try {
    CAPTURE = await api("/api/capture");
  } catch {
    /* 老版本服务没有这个接口时忽略 */
  }
  renderCapture();
}

function renderCapture() {
  const btn = $("#btn-auto");
  if (!btn) return;
  const following = !!(CAPTURE.on && CAPTURE.source);
  document.body.classList.toggle("follow-active", following);
  btn.classList.toggle("on", !!CAPTURE.on);
  btn.textContent = CAPTURE.on ? "暂停跟随" : "跟随游戏";
  const bits = [];
  if (CAPTURE.source) bits.push(`源：${CAPTURE.source}`);
  bits.push(`已读 ${CAPTURE.reads || 0} 帧`, `更新 ${CAPTURE.changed || 0} 次`);
  if (CAPTURE.error) bits.push(`错误：${CAPTURE.error}`);
  else if (!CAPTURE.on) bits.push("已暂停；可在手动纠错区录入");
  btn.title = bits.join(" · ");

  const labels = {
    disconnected: "未连接",
    stabilizing: "等待稳定画面",
    following: "正在跟随游戏",
    needs_confirmation: "有内容需要确认",
    paused: "已暂停",
  };
  const state = !CAPTURE.source ? "disconnected" : (CAPTURE.state || (CAPTURE.on ? "following" : "paused"));
  $("#follow-status").textContent = CAPTURE.error ? "识别异常" : (labels[state] || state);
  const details = [];
  if (CAPTURE.source) details.push(CAPTURE.source);
  if (CAPTURE.last_sync) details.push(`最近同步 ${new Date(CAPTURE.last_sync * 1000).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`);
  if (!details.length) details.push("连接游戏窗口后自动同步");
  $("#follow-detail").textContent = details.join(" · ");
  const pending = CAPTURE.pending || [];
  const pendingBox = $("#follow-pending");
  pendingBox.classList.toggle("hidden", !pending.length);
  const kindNames = { gold: "资金", round: "回合", shop_level: "等级", hp: "生命", shop: "商店", bench: "整备区", board: "战场", refresh: "可能的刷新" };
  pendingBox.textContent = pending.length
    ? `暂未写入：${pending.map((item) => kindNames[item.kind] || item.kind).join("、")}。请保持画面稳定，仍不清楚再手动纠错。`
    : "";
  if (!following) $("#manual-actions").open = true;
}

function fillModeSelect() {
  const sel = $("#mode");
  const modes = PACK.summary?.modes || [];
  sel.innerHTML = modes
    .map((m) => `<option value="${esc(m.id)}">${esc(m.name)}${m.type === "MULTI" ? "（多人）" : ""}</option>`)
    .join("");
  if (SNAP) sel.value = SNAP.state.mode_id;
}

/* ---------------------------------------------------------------- 渲染 */


function renderLedgerWarn() {
  const box = $("#ledger-warn");
  const err = (SNAP && SNAP.ledger && SNAP.ledger.error) || "";
  box.classList.toggle("hidden", !err);
  if (err) $("#ledger-warn-text").textContent = err;
}

function render() {
  if (!SNAP) return;
  const st = SNAP.state;
  $("#ver").textContent = "v1.0";
  const sourceNames = { community_replica: "非官方复刻", community_mixed: "社区混合来源" };
  const sourceLabel = sourceNames[SNAP.pack.source_type] || "来源见说明";
  $("#pack-info").textContent = `${SNAP.pack.name} · ${SNAP.pack.rules_version || SNAP.pack.season} · ${sourceLabel}`;
  $("#pack-info").title = [
    `数据日期：${SNAP.pack.updated || "未知"}`,
    ...(SNAP.pack.limitations || []),
    ...(SNAP.pack.warnings || []),
  ].join("\n");

  renderLedgerWarn();
  renderHud(st);
  renderActions(st);
  renderShop(st);
  renderUnits(st);
  renderOdds();
  renderRefreshAnalysis();
  renderBonds();
  renderEconomy();
  renderLog(st);
  renderCapture();
  $("#rules-note").textContent = buildNote();
  renderResultHint();
}

function renderResultHint() {
  const hint = SNAP?.state?.rules?.result_hint;
  const box = $("#result-hint");
  if (!hint || !hint.ts || !hint.result) {
    box.classList.add("hidden");
    return;
  }
  const age = Date.now() / 1000 - Number(hint.ts);
  if (age > 180) {
    box.classList.add("hidden");
    return;
  }
  const ok = hint.result === "clear";
  $("#result-hint-text").textContent =
    `识别到结算画面：${ok ? "通关" : "失败"}（置信度 ${Math.round((hint.confidence || 0) * 100)}%）`;
  box.dataset.result = hint.result;
  box.classList.remove("hidden");
}

function renderHud(st) {
  $("#round").textContent = st.round_no;
  $("#rounds-max").textContent = SNAP.mode.rounds ? `/ ${SNAP.mode.rounds}` : "";
  $("#gold").textContent = st.gold;
  $("#level").textContent = st.shop_level;
  $("#hp").textContent = st.hp == null ? "—" : st.hp;
  const phaseMap = { prep: "休整", battle: "作战", result: "结算", menu: "菜单" };
  $("#phase").textContent = phaseMap[st.phase] || st.phase;
}

function renderActions(st) {
  const eco = SNAP.economy;
  const refreshType = $("#refresh-type")?.value || "normal";
  const refreshCost = refreshType === "free" ? 0 : (eco.refresh_cost ?? 1);
  $("#cost-refresh").textContent = refreshCost ? `-${refreshCost}` : "免费";
  const next = eco.level_up_now;
  $("#cost-level").textContent = next ? `-${next.cost_now}` : "满级";
  $("#btn-levelup").disabled = !next || next.cost_now > st.gold;
  $("#btn-refresh").disabled = st.gold < refreshCost;
  $("#gain-next").textContent = `+${eco.income_next}`;
  const frozen = st.frozen;
  $("#btn-freeze").classList.toggle("on", frozen);
  $("#btn-freeze").textContent = frozen ? "已锁定" : "锁定";
}

function renderShop(st) {
  const box = $("#shop");
  const offers = st.shop || [];
  $("#shop-hint").textContent = editingShop
    ? "编辑模式：点格子选人，右键清空"
    : "点击购买 · Shift+点击编辑该格";
  box.innerHTML = "";
  offers.forEach((o, i) => {
    const op = o.op_id ? PACK.operators[o.op_id] : null;
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.slot = i;
    if (!o.op_id) {
      card.classList.add("unknown");
      card.innerHTML = editingShop ? `<div class="name">＋</div><div class="sub">点此录入</div>` : `<div class="name">?</div><div class="sub">未识别 / 空</div>`;
    } else {
      const tier = o.tier || op?.tier || 0;
      card.innerHTML = `
        <div class="tier ${TIER_CLASS(tier)}">${"I".repeat(1)}${tier}</div>
        <div>
          <div class="name ${TIER_CLASS(tier)}">${esc(op?.name || o.op_id)}</div>
          <div class="sub">${esc(op?.profession || "")} ${esc(op?.sub || "")}</div>
        </div>
        <div class="price">${o.kind === "item" ? "装备" : `◈ ${SNAP.shop_prices?.[i] ?? op?.price ?? "?"}`}</div>`;
      card.title = (op?.bonds || []).map((b) => PACK.bonds[b]?.name || b).join(" / ");
    }
    box.appendChild(card);
  });
}

function renderUnits(st) {
  const bench = $("#bench");
  const board = $("#board");
  bench.innerHTML = "";
  board.innerHTML = "";
  const mk = (zone, u, idx) => {
    const op = PACK.operators[u.op_id];
    const node = document.createElement("div");
    node.className = "unit" + (u.star >= 2 ? " golden" : "");
    node.dataset.zone = zone;
    node.dataset.slot = idx;
    const stars = "★".repeat(Math.max(1, Math.min(3, u.star)));
    node.innerHTML = `<span class="star">${stars}</span><span>${esc(op?.name || u.op_id)}</span>
      <span class="tag">${op ? "T" + op.tier : ""}</span>`;
    node.title = (op?.bonds || []).map((b) => PACK.bonds[b]?.name || b).join(" / ");
    (zone === "bench" ? bench : board).appendChild(node);
  };
  // 固定槽位：空的也画出来，点一下就能手摆（识别不出来时的兜底）
  const fill = (zone, list, max, host) => {
    list.forEach((u, i) => mk(zone, u, i));
    for (let i = list.length; i < max; i += 1) {
      const node = document.createElement("div");
      node.className = "unit empty";
      node.dataset.zone = zone;
      node.dataset.empty = "1";
      node.innerHTML = '<span class="plus">＋</span>';
      node.title = "点一下手动摆放（识别不出来时用）";
      host.appendChild(node);
    }
  };
  fill("bench", st.bench || [], 10, bench);
  fill("board", st.board || [], 8, board);
  $("#bench-count").textContent = `${st.bench.length} / 10`;
  $("#board-count").textContent = `${st.board.length} / 8`;
}

function renderOdds() {
  const odds = SNAP.odds;
  const st = SNAP.state;
  $("#odds-meta").textContent = `Lv.${odds.shop_level} · ${odds.slots} 格 · 池内 ${odds.eligible_copies} 份`;
  $("#odds-note").textContent =
    "单格/本刷均为独立格、按剩余份数加权的估计，非官方概率；实测只统计人工确认的刷新。";

  const tiers = $("#tiers");
  const maxP = Math.max(0.0001, ...Object.values(odds.per_tier || {}).map((t) => t.slot_prob));
  tiers.innerHTML = Object.values(odds.per_tier || {})
    .sort((a, b) => a.tier - b.tier)
    .map(
      (t) => `
      <div class="tier-row">
        <span class="${TIER_CLASS(t.tier)}">${t.tier} 阶</span>
        <div class="track"><div class="fill bar-${TIER_CLASS(t.tier)}" style="width:${((t.slot_prob / maxP) * 100).toFixed(1)}%"></div></div>
        <span class="num">${(t.slot_prob * 100).toFixed(1)}%</span>
      </div>`
    )
    .join("");

  const q = ($("#search").value || "").trim().toLowerCase();
  const onlyOwned = $("#only-owned").checked;
  const ownedSet = new Set((SNAP.focus || []).map((f) => f.op_id));
  const shopSet = new Set((st.shop || []).filter((o) => o.op_id).map((o) => o.op_id));

  let rows = Object.values(odds.per_op || {});
  if (q) rows = rows.filter((r) => (r.name || "").toLowerCase().includes(q));
  if (onlyOwned) rows = rows.filter((r) => ownedSet.has(r.op_id));
  rows.sort((a, b) => a.tier - b.tier || b.slot_prob - a.slot_prob);

  $("#odds").innerHTML =
    rows
      .slice(0, 200)
      .map((r) => {
        const cls = [shopSet.has(r.op_id) ? "in-shop" : "", ownedSet.has(r.op_id) ? "owned" : ""].join(" ");
        const observed = SNAP.refresh_analysis?.by_op?.[r.op_id];
        const hitText = observed ? `${observed.hits}/${observed.refreshes} · ${pct(observed.hit_rate)}` : "—";
        const costText = observed?.avg_cost_per_hit == null ? "—" : `${observed.avg_cost_per_hit.toFixed(1)} 金`;
        const estimateTitle = `独立格估计，非官方概率；50% 把握约 ${r.refreshes_50 ?? "无解"} 刷，80% 把握约 ${r.refreshes_80 ?? "无解"} 刷`;
        return `<tr class="${cls}" data-op="${esc(r.op_id)}">
          <td>${esc(r.name)}</td>
          <td class="${TIER_CLASS(r.tier)}">T${r.tier}</td>
          <td class="num">${r.remaining}/${r.total ?? "?"}</td>
          <td class="num odds-detail" title="${esc(estimateTitle)}">${(r.slot_prob * 100).toFixed(2)}%</td>
          <td class="num" title="${esc(estimateTitle)}">${(r.at_least_one * 100).toFixed(1)}%</td>
          <td class="num odds-detail ${observed && !observed.sample_ok ? "dim" : ""}">${hitText}</td>
          <td class="num odds-detail">${costText}</td>
        </tr>`;
      })
      .join("") || `<tr><td colspan="7" class="hint">没有匹配的干员</td></tr>`;
}

function renderRefreshAnalysis() {
  const analysis = SNAP.refresh_analysis || { totals: {}, history: [], notes: [] };
  const totals = analysis.totals || {};
  $("#refresh-note").textContent = `已确认 ${totals.refreshes || 0} 刷 · 未确认 ${totals.unconfirmed || 0} 刷 · 未归因 ${totals.unattributed || 0} 次；${(analysis.notes || []).join("；")}`;
  const typeNames = { normal: "普通", free: "免费", special: "特殊", unknown: "未归因" };
  $("#refresh-history").innerHTML = (analysis.history || [])
    .slice(-10)
    .reverse()
    .map((item) => {
      const names = (item.shop || []).filter(Boolean).map((id) => PACK.operators[id]?.name || id).join("、");
      const gold = item.gold_before == null ? "—" : `${item.gold_before}→${item.gold_after}`;
      const type = typeNames[item.refresh_type] || item.refresh_type || "普通";
      const source = item.refresh_source && item.refresh_source !== "standard" ? ` · ${item.refresh_source}` : "";
      return `<tr class="${item.confirmed ? "" : "low"}">
        <td>R${item.round} · L${item.shop_level ?? "?"} · ${esc(type)}${esc(source)}</td>
        <td>${esc(names || (item.confirmed ? "未识别" : "未确认"))}</td>
        <td class="num">${esc(gold)}</td>
      </tr>`;
    })
    .join("") || `<tr><td class="hint">还没有刷新记录</td></tr>`;
}

function renderBonds() {
  const items = SNAP.bonds.items || [];
  $("#bond-meta").textContent = `${SNAP.bonds.active_count} / ${SNAP.bonds.total} 已激活`;
  $("#bonds").innerHTML = items
    .map((b) => {
      const cand = b.bench_candidates?.length
        ? `<div class="cand">替补：<b>${b.bench_candidates.map(esc).join("、")}</b></div>`
        : "";
      return `<div class="bond ${b.active ? "active" : ""}" title="${esc(b.desc || "")}">
        <span class="bond-name">${esc(b.name)}${b.layers ? `<span class="layers">${b.layers} 层</span>` : ""}</span>
        <span class="cnt">${b.count} / ${b.activate_at}${b.next_threshold ? ` → ${b.next_threshold}` : ""}</span>
        ${cand}
      </div>`;
    })
    .join("");
}

function renderEconomy() {
  const eco = SNAP.economy;
  const next = eco.level_up_now;
  const rows = [];
  rows.push(row("本回合收入", `+${eco.income_now}`));
  rows.push(row("下回合收入", `+${eco.income_next}`));
  if (next) {
    rows.push(row(`升到 ${next.level} 级`, `${next.cost_now} 金`));
    rows.push(row("再等一回合", `${next.cost_next_round} 金`));
    rows.push(
      row(
        "攒够还要",
        next.rounds_to_afford == null ? "—" : next.rounds_to_afford === 0 ? "现在就行" : `${next.rounds_to_afford} 回合`
      )
    );
  }
  const last = eco.plans[eco.plans.length - 1];
  if (last && eco.plans.length > 1) rows.push(row("连升到满级", `${last.cumulative_cost} 金`));

  const curve = eco.income_curve || [];
  const maxInc = Math.max(1, ...curve.map((c) => c.income));
  const spark = `<div class="spark">${curve
    .map(
      (c) =>
        `<div class="b ${c.round === eco.round_no + 1 ? "on" : ""}" style="height:${(c.income / maxInc) * 100}%" title="R${c.round} +${c.income}"><span>${c.income}</span></div>`
    )
    .join("")}</div>`;

  const carry = eco.gold_carry_over ?? false;
  $("#eco-meta").textContent = eco.free_refresh_chance_percent == null
    ? ""
    : `奇迹免费刷新概率：${Number(eco.free_refresh_chance_percent).toFixed(1)}%（仅展示）`;
  $("#economy").innerHTML =
    rows.join("") +
    spark +
    `<label class="mini" style="margin-top:8px;display:flex;gap:6px;align-items:center">
       <input type="checkbox" id="carry-toggle" ${carry ? "checked" : ""}> 资金跨回合保留
     </label>
     <div class="footnote">${(eco.notes || []).map(esc).join("；")}</div>`;
}

function row(k, v) {
  return `<div class="eco-row"><span class="k">${esc(k)}</span><b>${esc(v)}</b></div>`;
}

function renderLog(st) {
  const logs = (st.log || []).slice(-40).reverse();
  $("#log").innerHTML = logs
    .map((l) => {
      const time = new Date(l.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });
      return `<div class="line ${esc(l.kind)}"><span class="t">${time}</span>${esc(l.text)}</div>`;
    })
    .join("");
}

function buildNote() {
  const bits = [];
  const conf = SNAP.pack.confidence || {};
  bits.push(`规则版本：${SNAP.pack.rules_version || "未标注"}`);
  bits.push(`抽卡模型：${conf["shop.odds_model"] === "assumed" ? "按份数均匀（假设）" : "按份数加权（社区模型）"}`);
  bits.push(...(SNAP.pack.limitations || ["装备、机变与多人联防未纳入概率模型"]));
  if (SNAP.state.rules?.scene) bits.push(`场景：${SNAP.state.rules.scene}`);
  if (editingShop) bits.push("商店为手工录入状态");
  return bits.join(" · ");
}

/* ---------------------------------------------------------------- 交互 */

function openPicker({ title, onPick, filter }) {
  window.CovenantDesktop?.ensureFull();
  const modal = $("#picker");
  $("#picker-title").textContent = title || "选择干员";
  const list = $("#picker-list");
  const search = $("#picker-search");
  search.value = "";
  const renderList = () => {
    const q = search.value.trim().toLowerCase();
    const items = Object.entries(PACK.operators)
      .filter(([id, op]) => (!filter || filter(id, op)) && (!q || op.name.toLowerCase().includes(q)))
      .sort((a, b) => a[1].tier - b[1].tier || a[1].name.localeCompare(b[1].name, "zh"));
    list.innerHTML = items
      .map(
        ([id, op]) => `<div class="picker-item" data-op="${esc(id)}">
          <span class="${TIER_CLASS(op.tier)}">${esc(op.name)}</span>
          <span class="tier">T${op.tier} ◈${op.price}</span>
        </div>`
      )
      .join("") || `<div class="hint">没有匹配项</div>`;
  };
  renderList();
  search.oninput = renderList;
  list.onclick = (e) => {
    const item = e.target.closest(".picker-item");
    if (!item) return;
    modal.classList.add("hidden");
    onPick(item.dataset.op);
  };
  modal.classList.remove("hidden");
  setTimeout(() => search.focus(), 30);
}

function closeMenus() {
  $("#menu").classList.add("hidden");
}

function showMenu(x, y, title, items) {
  const menu = $("#menu");
  menu.innerHTML =
    `<div class="menu-title">${esc(title)}</div>` +
    items.map((it, i) => `<button data-i="${i}" class="${it.danger ? "danger" : ""}">${esc(it.label)}</button>`).join("");
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
  menu.classList.remove("hidden");
  menu.onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    closeMenus();
    items[Number(b.dataset.i)].fn();
  };
}

/* ---------------------------------------------------------------- 统计 */

const pct = (x) => `${((Number(x) || 0) * 100).toFixed(1)}%`;
const deltaText = (d) => {
  const v = (Number(d) || 0) * 100;
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}`;
};

function switchView(view) {
  if (view === "stats") window.CovenantDesktop?.ensureFull();
  VIEW = view === "stats" ? "stats" : "match";
  if (location.hash !== `#${VIEW}`) history.replaceState(null, "", `#${VIEW}`);
  $("#match-main").classList.toggle("hidden", VIEW !== "match");
  $("#stats-main").classList.toggle("hidden", VIEW !== "stats");
  $$(".tab").forEach((b) => b.classList.toggle("on", b.dataset.view === VIEW));
  if (VIEW === "stats") loadStats();
  else if (SNAP) render();
}

function statsQuery() {
  const parts = [];
  const mode = $("#st-mode").value;
  const band = $("#st-band").value;
  const patch = $("#st-patch").value;
  const min = Number($("#st-min").value) || 5;
  if (mode) parts.push(`mode=${encodeURIComponent(mode)}`);
  if (band) parts.push(`band=${encodeURIComponent(band)}`);
  if (patch) parts.push(`patch=${encodeURIComponent(patch)}`);
  parts.push(`min=${min}`);
  return parts.join("&");
}

async function loadStats() {
  try {
    STATS = await api(`/api/stats?${statsQuery()}`);
    renderStats();
  } catch (err) {
    $("#st-notes").textContent = `读取统计失败：${err.message || err}`;
  }
}

function fillStatsFilters() {
  if (!STATS) return;
  const keep = (sel) => sel.value;
  const modeSel = $("#st-mode");
  const bandSel = $("#st-band");
  const patchSel = $("#st-patch");
  const modeNow = keep(modeSel);
  const bandNow = keep(bandSel);
  const patchNow = keep(patchSel);

  modeSel.innerHTML =
    `<option value="">全部模式</option>` +
    (STATS.modes_available || [])
      .map(([id, name]) => `<option value="${esc(id)}">${esc(name || id)}</option>`)
      .join("");
  bandSel.innerHTML =
    `<option value="">全部策略</option>` +
    (PACK.bands || [])
      .map((b) => `<option value="${esc(b.id)}">${esc(b.name)}</option>`)
      .join("");
  patchSel.innerHTML =
    `<option value="">全部赛季</option>` +
    (STATS.patches || []).map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join("");

  modeSel.value = modeNow;
  bandSel.value = bandNow;
  patchSel.value = patchNow;
}

function renderStats() {
  if (!STATS) return;
  fillStatsFilters();
  const t = STATS.totals || {};
  const filters = STATS.filters || {};
  const label = [
    filters.mode_id ? `模式 ${filters.mode_id}` : "全部模式",
    filters.band_id ? `策略 ${filters.band_id}` : "全部策略",
    filters.patch || "全部赛季",
  ].join(" · ");
  $("#st-range").textContent = `${label} · 原始 ${t.records_total || 0} 条 · 当前筛选 ${t.raw_matches || 0} 条 · 排除第一回合前退出 ${t.excluded_early_quit || 0} 条`;

  const kpis = [
    ["对局", t.matches ?? 0, t.clears ? `通关 ${t.clears}` : "—"],
    ["通关率", pct(t.clear_rate), t.matches ? `${t.clears}/${t.matches}` : "还没有样本"],
    ["平均回合", t.avg_rounds ? Number(t.avg_rounds).toFixed(1) : "—", ""],
    ["平均剩余生命", t.avg_hp == null ? "—" : Number(t.avg_hp).toFixed(1), ""],
    ["平均杯数", t.avg_cups == null ? "—" : Number(t.avg_cups).toFixed(1), ""],
  ];
  $("#st-kpis").innerHTML = kpis
    .map(
      ([k, v, s]) => `<div class="kpi"><span class="k">${esc(k)}</span>
        <b>${esc(v)}</b><small>${esc(s)}</small></div>`
    )
    .join("");

  const empty = (cols, text) => `<tr><td colspan="${cols}" class="hint">${esc(text)}</td></tr>`;

  $("#st-ops").innerHTML =
    (STATS.operators || [])
      .map(
        (r) => `<tr class="${r.sample_ok ? "" : "low"}">
          <td class="${TIER_CLASS(r.tier)}">${esc(r.name)}</td>
          <td>T${r.tier}</td>
          <td class="num">${r.picks}</td>
          <td class="num">${pct(r.pick_rate)}</td>
          <td class="num ${r.sample_ok ? "" : "dim"}">${pct(r.win_rate)}</td>
          <td class="num ${r.delta >= 0 ? "pos" : "neg"}">${deltaText(r.delta)}</td>
          <td class="num dim">${Math.round((r.ci_low || 0) * 100)}–${Math.round((r.ci_high || 0) * 100)}%</td>
        </tr>`
      )
      .join("") || empty(7, "还没有记录。打一局，点「记录本局」。");

  $("#st-bonds").innerHTML =
    (STATS.bonds || [])
      .map(
        (r) => `<tr class="${r.sample_ok ? "" : "low"}">
          <td>${esc(r.name)}</td>
          <td class="num">${r.picks}</td>
          <td class="num ${r.sample_ok ? "" : "dim"}">${pct(r.win_rate)}</td>
        </tr>`
      )
      .join("") || empty(3, "还没有数据");

  $("#st-comps").innerHTML =
    (STATS.comps || [])
      .map(
        (r) => `<tr class="${r.sample_ok ? "" : "low"}">
          <td>${esc(r.name)}</td>
          <td class="num">${r.matches}</td>
          <td class="num ${r.sample_ok ? "" : "dim"}">${pct(r.win_rate)}</td>
        </tr>`
      )
      .join("") || empty(3, "至少要有 2 条盟约的终局才会成组");

  $("#st-bands").innerHTML =
    (STATS.bands || [])
      .map(
        (r) => `<tr class="${r.sample_ok ? "" : "low"}">
          <td>${esc(r.name)}</td>
          <td class="num">${r.matches}</td>
          <td class="num ${r.sample_ok ? "" : "dim"}">${pct(r.win_rate)}</td>
        </tr>`
      )
      .join("") || empty(3, "还没有数据");

  $("#st-notes").innerHTML = (STATS.notes || []).map(esc).join("；");
  renderRounds();
}

function renderRounds() {
  const rows = STATS.by_round || [];
  const tbl = $("#st-rounds");
  if (!rows.length) {
    tbl.innerHTML = `<tr><td colspan="5" class="hint">${esc("还没有回合数据：过一回合就会记一条。")}</td></tr>`;
    $("#st-round").innerHTML = "";
    $("#st-round-ops").innerHTML = `<tr><td colspan="4" class="hint">—</td></tr>`;
    return;
  }
  tbl.innerHTML =
    `<tr><th>回合</th><th class="num">样本</th><th class="num">平均等级</th><th class="num">平均资金</th><th class="num">平均上场</th></tr>` +
    rows
      .map(
        (r) => `<tr>
          <td>第 ${r.round} 回合</td>
          <td class="num">${r.records}</td>
          <td class="num">${r.avg_shop_level == null ? "—" : "L" + Number(r.avg_shop_level).toFixed(1)}</td>
          <td class="num">${r.avg_gold == null ? "—" : Number(r.avg_gold).toFixed(1)}</td>
          <td class="num">${Number(r.avg_board || 0).toFixed(1)}</td>
        </tr>`
      )
      .join("");

  const sel = $("#st-round");
  const prev = sel.value;
  sel.innerHTML = rows
    .map((r) => `<option value="${r.round}">第 ${r.round} 回合 · ${r.records} 场</option>`)
    .join("");
  const keep = rows.some((r) => String(r.round) === prev) ? prev : String(rows[rows.length - 1].round);
  sel.value = keep;
  renderRoundOps();
}

function renderRoundOps() {
  const rows = STATS?.by_round || [];
  const rd = Number($("#st-round").value || 0);
  const bucket = rows.find((r) => r.round === rd);
  const tbl = $("#st-round-ops");
  if (!bucket) {
    tbl.innerHTML = `<tr><td colspan="4" class="hint">没有这一回合的数据</td></tr>`;
    return;
  }
  tbl.innerHTML =
    (bucket.ops || [])
      .map(
        (o) => `<tr class="${o.sample_ok ? "" : "low"}">
          <td class="${TIER_CLASS(o.tier)}">${esc(o.name)}</td>
          <td>T${o.tier}</td>
          <td class="num">${o.picks}</td>
          <td class="num">${pct(o.pick_rate)}</td>
        </tr>`
      )
      .join("") || `<tr><td colspan="4" class="hint">这一回合还没有上场记录</td></tr>`;
}

function openRecord(result) {
  window.CovenantDesktop?.ensureFull();
  const st = SNAP?.state;
  const activeBonds = (SNAP?.bonds?.items || []).filter((b) => b.active).length;
  $("#rec-preview").textContent = st
    ? `将记录：第 ${st.round_no} 回合 · 战场 ${st.board.length} 人 · 盟约 ${activeBonds} 条 · 剩余生命 ${st.hp ?? "—"}`
    : "还没有对局数据";
  if (result === "clear" || result === "fail" || result === "quit") $("#rec-result").value = result;
  $("#rec-cups").value = "";
  $("#rec-note").value = "";
  $("#record").classList.remove("hidden");
}

async function saveRecord() {
  const payload = {
    result: $("#rec-result").value,
    cups: $("#rec-cups").value === "" ? null : Number($("#rec-cups").value),
    note: $("#rec-note").value.trim(),
  };
  try {
    const res = await api("/api/match", payload);
    if (!res.ok) throw new Error(res.error || "保存失败");
    $("#record").classList.add("hidden");
    toast(`已记录（共 ${res.count} 局）`);
    await loadStats();
  } catch (err) {
    toast(String(err.message || err), true);
  }
}

async function importMatches() {
  const text = $("#imp-text").value.trim();
  if (!text) {
    toast("先粘贴内容或选一个文件", true);
    return;
  }
  try {
    const res = await api("/api/match/import", { text });
    if (!res.ok) throw new Error(res.error || "导入失败");
    toast(`导入完成：新增 ${res.added}，跳过重复 ${res.skipped}，坏行 ${res.bad}`);
    $("#imp-text").value = "";
    $("#importer").classList.add("hidden");
    await loadStats();
  } catch (err) {
    toast(String(err.message || err), true);
  }
}

/* 设置：只放少量持久项（间隔、自动识别），路径只读展示 */
async function openSettings() {
  window.CovenantDesktop?.ensureFull();
  $("#settings").classList.remove("hidden");
  $("#set-meta").textContent = "正在读取…";
  try {
    const info = await api("/api/settings");
    const st = info.settings || {};
    $("#set-interval").value = st.capture_interval ?? 2;
    $("#set-auto").checked = !!st.auto_capture;
    $("#set-source").textContent = info.frame_source || "未连接";
    const pack = info.paths && info.paths.pack ? info.paths.pack : "—";
    $("#set-meta").textContent = `v${info.version} · 数据包 ${pack} · 数据目录 ${info.paths ? info.paths.var : "—"}`;
  } catch (err) {
    $("#set-meta").textContent = `读取失败：${err.message || err}`;
  }
}

async function saveSettings() {
  const interval = Number($("#set-interval").value || 2);
  const auto = $("#set-auto").checked;
  const btn = $("#set-save");
  btn.disabled = true;
  try {
    const res = await api("/api/settings", { capture_interval: interval, auto_capture: auto });
    const st = res.settings || {};
    SNAP = null;
    await refresh();
    renderCapture(res.capture);
    toast(`已保存：每 ${st.capture_interval} 秒识别一次${st.auto_capture ? "，自动开启" : ""}`);
    $("#settings").classList.add("hidden");
  } catch (err) {
    toast(String(err.message || err), true);
  } finally {
    btn.disabled = false;
  }
}


async function openUploader() {
  window.CovenantDesktop?.ensureFull();
  $("#uploader").classList.remove("hidden");
  $("#up-preview").textContent = "正在读取本机记录…";
  try {
    const info = await api("/api/community");
    $("#up-url").value = info.url || "http://127.0.0.1:8900";
    $("#up-token").value = "";
    $("#up-token").placeholder = info.token_set ? "已保存令牌，留空则沿用" : "服务端没设 token 就留空";
    $("#up-preview").textContent = info.records
      ? `本机共 ${info.records} 条记录，将全部上传（服务端按 id 去重）。`
      : "本机还没有记录；先打一局并点「记录本局」。";
  } catch (err) {
    $("#up-preview").textContent = `读取失败：${err.message || err}`;
  }
}

async function uploadMatches() {
  const url = $("#up-url").value.trim();
  const token = $("#up-token").value.trim();
  if (!url) {
    toast("先填社区地址", true);
    return;
  }
  const btn = $("#up-go");
  btn.disabled = true;
  btn.textContent = "上传中…";
  try {
    const res = await api("/api/community/upload", { url, token });
    if (!res.ok) throw new Error(res.error || "上传失败");
    toast(`已上传：新增 ${res.added ?? 0}，重复 ${res.skipped ?? 0}，坏行 ${res.bad ?? 0}`);
    $("#uploader").classList.add("hidden");
  } catch (err) {
    toast(String(err.message || err), true);
  } finally {
    btn.disabled = false;
    btn.textContent = "上传";
  }
}

/* 事件绑定 */
document.addEventListener("click", (e) => {
  if (!e.target.closest("#menu")) closeMenus();

  const card = e.target.closest(".card");
  if (card) {
    const slot = Number(card.dataset.slot);
    if (editingShop || e.shiftKey) {
      openPicker({
        title: `录入第 ${slot + 1} 格`,
        onPick: (opId) => {
          const offers = (SNAP.state.shop || []).map((o) => (o.op_id ? { op_id: o.op_id, kind: o.kind } : null));
          while (offers.length <= slot) offers.push(null);
          offers[slot] = opId ? { op_id: opId } : null;
          act("set_shop", { offers });
        },
      });
      return;
    }
    const offer = (SNAP.state.shop || [])[slot];
    if (offer && offer.op_id) act("buy", { slot });
    else toast("这一格没有可买的干员");
  }

  const unit = e.target.closest(".unit");
  if (unit) {
    const zone = unit.dataset.zone;
    if (unit.dataset.empty) {
      openPicker({
        title: zone === "bench" ? "摆一张到整备区" : "摆一张到战场",
        onPick: (opId) => act("add_unit", { zone, op_id: opId }),
      });
      return;
    }
    const slot = Number(unit.dataset.slot);
    const u = (zone === "bench" ? SNAP.state.bench : SNAP.state.board)[slot];
    if (!u) return;
    const name = PACK.operators[u.op_id]?.name || u.op_id;
    const rect = unit.getBoundingClientRect();
    const items = [];
    if (zone === "bench") {
      items.push({ label: "移到战场", fn: () => act("move", { from_zone: "bench", from_slot: slot, to_zone: "board" }) });
    } else {
      items.push({ label: "移回整备区", fn: () => act("move", { from_zone: "board", from_slot: slot, to_zone: "bench" }) });
    }
    items.push({ label: `出售（+${PACK.operators[u.op_id]?.sell ?? 1}）`, fn: () => act("sell", { zone, slot }) });
    items.push({ label: "移除（不结算金币）", danger: true, fn: () => act("remove_unit", { zone, slot }) });
    showMenu(rect.left, rect.bottom + 4, name, items);
  }

  const stat = e.target.closest(".stat[data-edit]");
  if (stat) {
    const field = stat.dataset.edit;
    const cur = SNAP.state[field];
    const val = prompt(`设置 ${field}（当前 ${cur ?? "空"}）`, cur ?? "");
    if (val !== null) act("set", { path: field, value: Number(val) });
  }

  if (e.target.id === "carry-toggle") {
    act("set", { path: "rules.gold_carry_over", value: e.target.checked });
  }
});

async function refreshShop() {
  const refresh_type = $("#refresh-type").value || "normal";
  const res = await act("refresh", { refresh_type });
  if (res?.ok) {
    editingShop = true;
    $("#btn-edit").classList.add("on");
    $("#btn-edit").textContent = "退出编辑";
    render();
    toast("请录入新商店并确认；未确认不会进入实测");
  }
}

$("#btn-refresh").onclick = refreshShop;
$("#refresh-type").onchange = () => { if (SNAP) renderActions(SNAP.state); };
$("#btn-confirm-shop").onclick = async () => {
  const history = SNAP.refresh_analysis?.history || [];
  const pending = history.length > 0 && !history[history.length - 1].confirmed;
  const offers = (SNAP.state.shop || []).map((o) =>
    o.op_id ? { op_id: o.op_id, kind: o.kind } : null
  );
  const res = await act("set_shop", { offers });
  if (res?.ok) {
    editingShop = false;
    $("#btn-edit").classList.remove("on");
    $("#btn-edit").textContent = "编辑商店";
    render();
    toast(pending ? "商店已确认，本次刷新已计入实测" : "商店已保存；没有待确认的刷新");
  }
};
$("#btn-levelup").onclick = () => act("level_up");
$("#btn-next").onclick = () => act("next_round");
$("#btn-freeze").onclick = () => act("freeze");
$("#btn-add").onclick = () =>
  openPicker({
    title: "摆一张到整备区",
    onPick: (opId) => act("add_unit", { zone: "bench", op_id: opId }),
  });

$("#btn-edit").onclick = () => {
  editingShop = !editingShop;
  $("#btn-edit").classList.toggle("on", editingShop);
  $("#btn-edit").textContent = editingShop ? "退出编辑" : "编辑商店";
  render();
};

$("#btn-auto").onclick = async () => {
  const res = await api("/api/capture", { on: !CAPTURE.on, interval: CAPTURE.interval || 2 });
  if (!res || res.ok === false) {
    toast(res?.error || "切换失败：先接采集源（serve --window Arknights）");
    return;
  }
  CAPTURE = res;
  renderCapture();
  toast(CAPTURE.on ? `自动识别已开：每 ${CAPTURE.interval}s 抓一帧` : "自动识别已停");
};

$("#search").oninput = () => renderOdds();
$("#only-owned").onchange = () => renderOdds();
$("#btn-odds-detail").onclick = (event) => {
  const button = event.currentTarget;
  const expanded = button.closest(".odds-panel").classList.toggle("show-odds-detail");
  button.setAttribute("aria-pressed", String(expanded));
  button.textContent = expanded ? "收起细节" : "详细数据";
};

$("#mode").onchange = (e) => {
  const id = e.target.value;
  if (!confirm(`切换到「${e.target.selectedOptions[0].textContent}」会开新局，当前进度会清空。继续？`)) {
    e.target.value = SNAP.state.mode_id;
    return;
  }
  act("reset", { mode_id: id });
};

/* 新局 */
$("#btn-new").onclick = () => {
  const modes = PACK.summary?.modes || [];
  $("#ng-mode").innerHTML = modes
    .map((m) => `<option value="${esc(m.id)}">${esc(m.name)}${m.type === "MULTI" ? "（多人）" : ""}</option>`)
    .join("");
  $("#ng-mode").value = SNAP.state.mode_id;
  const bands = (PACK.bands || []).slice().sort((a, b) => a.hp - b.hp);
  $("#ng-band").innerHTML =
    `<option value="">（不选）</option>` +
    bands.map((b) => `<option value="${esc(b.id)}" data-hp="${b.hp}">${esc(b.name)} · ${b.hp} 生命 · ${esc(b.effect_name)}</option>`).join("");
  const groups = PACK.core_bond_groups || [];
  const gf = $("#ng-group-field");
  if (groups.length > 1) {
    const bname = (id) => (PACK.bonds && PACK.bonds[id] && PACK.bonds[id].name) || id;
    $("#ng-group").innerHTML = groups
      .map((g, i) => `<option value="${i}">第 ${i + 1} 组：${g.map(bname).join("、")}</option>`)
      .join("");
    if (SNAP.state.choices && SNAP.state.choices.bond_group != null) {
      $("#ng-group").value = String(SNAP.state.choices.bond_group);
    }
    gf.classList.remove("hidden");
  } else {
    gf.classList.add("hidden");
  }
  $("#ng-hp").value = SNAP.state.hp ?? "";
  $("#newgame").classList.remove("hidden");
};
$("#ng-close").onclick = () => $("#newgame").classList.add("hidden");
$("#ng-band").onchange = (e) => {
  const opt = e.target.selectedOptions[0];
  if (opt?.dataset.hp) $("#ng-hp").value = opt.dataset.hp;
};
$("#ng-start").onclick = () => {
  const mode_id = $("#ng-mode").value;
  const band_id = $("#ng-band").value || null;
  const hpRaw = $("#ng-hp").value;
  const hp = hpRaw === "" ? null : Number(hpRaw);
  const groupEl = $("#ng-group-field");
  const bond_group = groupEl.classList.contains("hidden") ? null : Number($("#ng-group").value);
  $("#newgame").classList.add("hidden");
  act("reset", { mode_id, band_id, hp, bond_group });
};

$("#picker-close").onclick = () => $("#picker").classList.add("hidden");

/* 统计视图 */
$$(".tab").forEach((b) => (b.onclick = () => switchView(b.dataset.view)));
["#st-mode", "#st-band", "#st-patch", "#st-min"].forEach((sel) => {
  $(sel).onchange = () => loadStats();
});
$("#st-refresh").onclick = () => loadStats();
$("#st-round").onchange = () => renderRoundOps();
$("#st-record").onclick = () => {
  if (!SNAP) return toast("还没有对局数据", true);
  openRecord();
};
$("#result-record").onclick = () => openRecord($("#result-hint").dataset.result);
$("#result-dismiss").onclick = () => act("set", { path: "rules.result_hint", value: null });
$("#st-import").onclick = () => $("#importer").classList.remove("hidden");
$("#st-upload").onclick = () => openUploader();
$("#up-close").onclick = () => $("#uploader").classList.add("hidden");
$("#up-go").onclick = () => uploadMatches();
$("#st-export").onclick = () => {
  const a = document.createElement("a");
  a.href = "/api/match/export?fmt=csv";
  a.download = "covenant-matches.csv";
  document.body.appendChild(a);
  a.click();
  a.remove();
};
$("#rec-close").onclick = () => $("#record").classList.add("hidden");
$("#rec-save").onclick = () => saveRecord();
$("#imp-close").onclick = () => $("#importer").classList.add("hidden");
$("#imp-go").onclick = () => importMatches();
$("#imp-file").onchange = (e) => {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    $("#imp-text").value = String(reader.result || "");
    toast(`已载入 ${file.name}`);
  };
  reader.readAsText(file, "utf-8");
};

$("#btn-settings").onclick = () => openSettings();
$("#set-close").onclick = () => $("#settings").classList.add("hidden");
$("#set-save").onclick = () => saveSettings();

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    $("#picker").classList.add("hidden");
    $("#newgame").classList.add("hidden");
    $("#record").classList.add("hidden");
    $("#importer").classList.add("hidden");
    $("#uploader").classList.add("hidden");
    $("#settings").classList.add("hidden");
    closeMenus();
  }
  if (e.target.matches("input, select, textarea")) return;
  if (VIEW !== "match") return;
  if (CAPTURE.on && CAPTURE.source && !$("#manual-actions").open) return;
  if (e.key === "r") refreshShop();
  if (e.key === "e") act("level_up");
  if (e.key === "n") act("next_round");
});

boot();
