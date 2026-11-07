// 识别工作台：把 /api/vision/inspect 的结果画出来，并支持就地教它。
(() => {
  const $ = (sel) => document.querySelector(sel);
  const state = {
    report: null,
    snap: null,
    pack: null,
    selected: null,
    follow: false,
    timer: 0,
    pick: null, // {region, index, kind, star}
    pickOp: null,
  };

  async function api(path, body) {
    const opt = body
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
      : undefined;
    const res = await fetch(path, opt);
    const data = await res.json().catch(() => ({ ok: false, error: `HTTP ${res.status}` }));
    if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove("show"), 2200);
  }

  const OP = () => (state.pack && state.pack.operators) || {};
  function opLabel(id) {
    const o = OP()[id];
    if (!o) return id;
    return `${o.name || id} · T${o.tier || "?"} · ${o.profession || ""}${o.sub ? "/" + o.sub : ""}`;
  }

  function chip(status) {
    const map = { ok: "ok", warn: "warn", fail: "bad", skip: "skip" };
    const text = { ok: "识别", warn: "部分", fail: "失败", skip: "跳过" }[status] || status;
    return `<span class="pill ${map[status] || ""}">${text}</span>`;
  }

  function drawBoxes(rep) {
    const wrap = $("#boxes");
    const img = $("#shot");
    if (!rep || !img.naturalWidth || !img.clientWidth) return;
    const w = img.clientWidth, h = img.clientHeight;
    wrap.style.width = w + "px";
    wrap.style.height = h + "px";
    const pct = (v, total) => (v / total) * 100;
    const html = [];
    for (const r of rep.regions) {
      const [x, y, rw, rh] = r.rect;
      const sel = state.selected === r.name ? " sel" : "";
      html.push(
        `<div class="box${sel}" style="left:${pct(x, rep.width)}%;top:${pct(y, rep.height)}%;width:${pct(rw, rep.width)}%;height:${pct(rh, rep.height)}%"><span>${r.name}</span></div>`
      );
      for (const s of r.slots || []) {
        const [sx, sy, sw, sh] = s.rect;
        html.push(
          `<div class="cbox${s.accepted ? "" : " miss"}" style="left:${pct(sx, rep.width)}%;top:${pct(sy, rep.height)}%;width:${pct(sw, rep.width)}%;height:${pct(sh, rep.height)}%"></div>`
        );
      }
    }
    wrap.innerHTML = html.join("");
    $("#frame-hint").textContent = `框：${rep.regions.length} 个区域 · ${Math.round(w)}×${Math.round(h)}`;
  }

  const KIND_LABEL = {
    gold: "资金",
    round: "回合",
    hp: "生命",
    shop_level: "等级",
    shop: "商店",
    board: "战场",
    bench: "整备区",
    result: "结算",
    bonds: "盟约",
    scene: "场景",
    mode: "模式",
    phase: "阶段",
    freeze: "锁定",
  };

  function stateValue(kind) {
    const st = state.snap && state.snap.state;
    if (!st) return null;
    if (kind === "gold") return st.gold;
    if (kind === "round" || kind === "round_no") return st.round_no;
    if (kind === "hp") return st.hp;
    if (kind === "shop_level" || kind === "level") return st.shop_level;
    if (kind === "shop") return Array.isArray(st.shop) ? st.shop.length : null;
    if (kind === "result") return (st.rules && st.rules.result_hint && st.rules.result_hint.result) || null;
    return null;
  }

  function renderCov(rep) {
    const cov = rep && rep.coverage;
    if (!cov) {
      $("#cov").innerHTML = "";
      $("#cov-note").textContent = "";
      return;
    }
    const counts = cov.digits.counts || {};
    const cells = [];
    for (let d = 0; d <= 9; d++) {
      const have = counts[String(d)] || 0;
      cells.push(
        `<div class="d ${have ? "have" : "miss"}" title="${have ? "已学 " + have + " 个样本" : "还没学"}"><b>${d}</b><span>${have || "缺"}</span></div>`
      );
    }
    $("#cov").innerHTML = cells.join("");
    $("#cov-note").textContent = `卡 ${cov.icons} · 单位 ${cov.units} · 星 ${cov.stars} · 结算 ${cov.results}`;
  }

  function renderObs(rep) {
    const list = (rep && rep.observations) || [];
    if (!list.length) {
      $("#obs").innerHTML = `<div class="muted">这一帧没有产出任何观测（可能全被阈值挡住，原因见下）。</div>`;
      return;
    }
    $("#obs").innerHTML = list
      .map((o) => {
        const cur = stateValue(o.kind);
        let val = o.value;
        if (o.kind === "result" && val && typeof val === "object") val = val.result;
        if (o.kind === "shop" && Array.isArray(val)) val = `${val.length} 格`;
        const diff = cur !== null && String(cur) !== String(val);
        return `<div class="o${diff ? " diff" : ""}">
          <span class="k">${KIND_LABEL[o.kind] || o.kind}</span>
          <span class="v">${val}</span>
          ${diff ? `<span class="c">面板 ${cur}</span>` : `<span class="c">${o.confidence}</span>`}
        </div>`;
      })
      .join("");
  }

  function renderRegion(r) {
    const sel = state.selected === r.name ? " sel" : "";
    let body = "";
    if (r.kind === "digits") {
      const gs = (r.glyphs || [])
        .map((g) => {
          const cls = !g.ok ? "bad" : g.best.score < 0.8 ? "weak" : "";
          const cands = (g.top || []).slice(1, 3).map((t) => t.key).join("/") || "—";
          return `<div class="gchip ${cls}"><img src="${g.crop}"><b>${g.best.key || "?"}</b><span>${g.best.score}</span><span>次选 ${cands}</span></div>`;
        })
        .join("");
      body = `
        <div class="row">${r.crop ? `<img class="crop" src="${r.crop}">` : ""}
          <div><div class="meta">读到：<b style="color:${r.text ? "var(--ok)" : "var(--bad)"}">${r.text ?? "（失败）"}</b>
          ${r.partial ? ` · 部分：${r.partial}` : ""}</div>
          <div class="meta">平均分 ${r.confidence}</div></div></div>
        <div class="gchips">${gs}</div>
        <div class="row"><input type="text" placeholder="这一格实际是 ${(r.glyphs || []).length} 位数字" id="d-${r.name}" style="width:130px;padding:4px 6px;background:#0e1116;border:1px solid var(--line);color:var(--text);border-radius:4px">
          <button data-learn-digit="${r.name}">教数字</button></div>`;
    } else if (r.kind === "shop" || r.kind === "units") {
      const slots = (r.slots || [])
        .map((s) => {
          const top = (s.top || []).map((t) => `${t.key}:${t.score}`).join(" / ");
          const cands = (s.top || [])
            .slice(0, 3)
            .map((t) => {
              const label = t.key === s.accepted ? "✓" : (OP()[t.key] && OP()[t.key].name) || t.key;
              return `<button data-cand="${r.name}:${s.index}:${r.kind}:${t.key}" title="${t.key} ${t.score}">${label}</button>`;
            })
            .join("");
          return `<div class="slot" data-slot="${r.name}:${s.index}:${r.kind}">
            <img src="${s.crop}">
            <div class="t">${s.accepted ? "<b>" + opLabel(s.accepted) + "</b>" : (s.best.key ? opLabel(s.best.key) + "?" : "未知")}</div>
            <div class="s">${s.accepted ? "已识别" : "最高 " + s.best.score}${s.star ? " · " + s.star + "★" : ""}</div>
            <div class="cands">${cands}</div>
            <div class="t" title="${top}">${top}</div>
          </div>`;
        })
        .join("");
      body = `<div class="row">${r.crop ? `<img class="crop" src="${r.crop}">` : ""}
          <div class="meta">${r.reason || `${r.known || 0}/${r.total || 0} 格认出`}</div></div>
        <div class="slots">${slots}</div>`;
    } else if (r.kind === "result") {
      body = `<div class="row">${r.crop ? `<img class="crop" src="${r.crop}">` : ""}
        <div class="meta">最高 ${(r.top || []).map((t) => t.key + ":" + t.score).join(" / ") || "无模板"}</div></div>`;
    } else {
      body = `<div class="meta">${r.reason || "未标定"}</div>`;
    }
    return `<div class="region${sel}" data-region="${r.name}">
      <div class="top">${chip(r.status)}<span class="name">${r.name}</span>
        <span class="meta">${r.kind}${r.target ? " → " + r.target : ""}</span></div>
      ${body}</div>`;
  }

  function render() {
    const rep = state.report;
    $("#src").textContent = rep ? `${rep.width}×${rep.height}` : "未连接";
    const cov = rep && rep.coverage;
    $("#prof").textContent = cov
      ? `模板：数字 ${cov.digits.present.length}/10 · 卡 ${cov.icons} · 单位 ${cov.units} · 星 ${cov.stars} · 结算 ${cov.results}`
      : "";
    $("#regions").innerHTML = rep ? rep.regions.map(renderRegion).join("") : "";
    renderCov(rep);
    renderObs(rep);
    $("#suggestions").innerHTML = rep
      ? (rep.suggestions.length ? rep.suggestions.map((s) => `<li>${s}</li>`).join("") : "<li>暂时没发现硬缺口。</li>")
      : "";
    if (rep) {
      const t = rep.thresholds;
      for (const [k, v] of [["digit", t.digit], ["icon", t.icon], ["unit", t.unit], ["star", t.star]]) {
        const el = $("#t-" + k);
        el.value = v;
        $("#o-" + k).textContent = Number(v).toFixed(2);
      }
    }
    drawBoxes(rep);
  }

  async function refresh(force = false) {
    try {
      const advance = force || state.follow;
      const [rep, snap] = await Promise.all([
        api("/api/vision/inspect" + (advance ? "?next=1" : "")),
        api("/api/snapshot").catch(() => null),
      ]);
      state.report = rep;
      state.snap = snap;
      render();
      $("#shot").src = "/api/frame?t=" + Date.now();
      if (force) toast(`已抓一帧（${rep.elapsed_ms} ms）`);
    } catch (err) {
      toast(err.message);
      if (String(err.message).includes("没有接入")) $("#src").textContent = "未接采集源";
    }
  }

  function bindThresholds() {
    for (const k of ["digit", "icon", "unit", "star"]) {
      $("#t-" + k).addEventListener("change", async (e) => {
        try {
          const out = await api("/api/vision/config", { [k + "_min_sim"]: Number(e.target.value) });
          $("#o-" + k).textContent = Number(out.config[k + "_min_sim"]).toFixed(2);
          toast("阈值已保存");
          refresh();
        } catch (err) {
          toast(err.message);
        }
      });
      $("#t-" + k).addEventListener("input", (e) => {
        $("#o-" + k).textContent = Number(e.target.value).toFixed(2);
      });
    }
  }

  async function learnDigit(region) {
    const input = document.querySelector(`#d-${region}`);
    const text = (input && input.value || "").trim();
    if (!/^\d+$/.test(text)) return toast("先填这一格实际数字");
    try {
      const out = await api("/api/vision/learn-digit", { region, text });
      toast(`学了 ${out.learned} 个字`);
      refresh();
    } catch (err) {
      toast(err.message);
    }
  }

  function openPicker(region, index, kind, star) {
    state.pick = { region, index, kind, star };
    state.pickOp = null;
    $("#pick-title").textContent = star ? `${region} 第 ${index + 1} 格：这是几星？` : `${region} 第 ${index + 1} 格：这是谁？`;
    $("#pick-search").value = "";
    $("#pick-ops").innerHTML = "";
    if (star) {
      $("#pick-ops").innerHTML = [1, 2, 3]
        .map((n) => `<div class="op" data-star="${n}">${n} 星</div>`)
        .join("");
    } else {
      renderOps("");
    }
    $("#picker").classList.add("open");
  }

  function renderOps(q) {
    const ops = Object.entries(OP())
      .filter(([id, o]) => !q || (o.name || "").includes(q) || id.includes(q))
      .slice(0, 300)
      .map(([id, o]) => `<div class="op" data-op="${id}">${o.name || id}<span class="m">T${o.tier} ${o.profession || ""} ${id}</span></div>`)
      .join("");
    $("#pick-ops").innerHTML = ops || `<div class="op">没有匹配</div>`;
  }

  async function learnCandidate(region, index, kind, opId) {
    const url = kind === "units" ? "/api/vision/learn-unit" : "/api/vision/learn-slot";
    try {
      await api(url, { region, index, op_id: opId });
      toast(`已学：${opLabel(opId)}`);
      refresh();
    } catch (err) {
      toast(err.message);
    }
  }

  function summaryText() {
    const rep = state.report;
    if (!rep) return "";
    const cov = rep.coverage;
    const lines = [
      `识别工作台 ${rep.width}×${rep.height} · ${rep.elapsed_ms} ms`,
      `数字：${cov.digits.present.join("") || "无"}；缺 ${cov.digits.missing.join("/") || "无"}`,
      `模板：卡 ${cov.icons} · 单位 ${cov.units} · 星 ${cov.stars} · 结算 ${cov.results}`,
      `观测：${(rep.observations || []).map((o) => `${o.kind}=${JSON.stringify(o.value)}`).join(" | ") || "无"}`,
    ];
    for (const s of rep.suggestions || []) lines.push("· " + s);
    return lines.join("\n");
  }

  async function copySummary() {
    const text = summaryText();
    if (!text) return toast("先抓一帧");
    try {
      await navigator.clipboard.writeText(text);
      toast("摘要已复制");
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
      toast("摘要已复制");
    }
  }

  function bindEvents() {
    $("#btn-grab").addEventListener("click", () => refresh(true));
    $("#btn-copy").addEventListener("click", copySummary);
    $("#btn-report").addEventListener("click", async () => {
      try {
        const out = await api("/api/vision/save-report", {});
        toast(`已存到 ${out.path}`);
      } catch (err) {
        toast(err.message);
      }
    });
    $("#btn-follow").addEventListener("click", () => {
      state.follow = !state.follow;
      $("#btn-follow").classList.toggle("on", state.follow);
      if (state.follow) {
        refresh();
        state.timer = setInterval(refresh, 2000);
      } else {
        clearInterval(state.timer);
      }
    });
    $("#btn-window").addEventListener("click", async () => {
      const title = window.prompt("窗口标题包含哪些字？（例如 卫戍协议 / Edge）", "");
      if (!title) return;
      try {
        const r = await api("/api/frame-source", { title });
        toast("已接窗口：" + (r.title || title));
        $("#src").textContent = r.title || title;
        refresh();
      } catch (err) {
        toast(err.message);
      }
    });

    // 画区域：在图上拖一个框 → 填名字和类型 → 存进 layout
    let drawing = false;
    let dx = 0;
    let dy = 0;
    $("#btn-draw").addEventListener("click", () => {
      drawing = !drawing;
      $("#btn-draw").classList.toggle("on", drawing);
      $("#frame-hint").textContent = drawing
        ? "在图上拖一个框来新增区域（Esc 取消）"
        : "点「抓一帧」或打开「跟随」，每 2 秒自动刷新。";
    });
    const shot = $("#shot");
    shot.addEventListener("pointerdown", (e) => {
      if (!drawing) return;
      const box = shot.getBoundingClientRect();
      dx = (e.clientX - box.left) / box.width;
      dy = (e.clientY - box.top) / box.height;
      const paint = (ev) => {
        const nx = Math.max(0, Math.min(1, (ev.clientX - box.left) / box.width));
        const ny = Math.max(0, Math.min(1, (ev.clientY - box.top) / box.height));
        $("#frame-hint").textContent =
          "框：x=" + Math.min(dx, nx).toFixed(3) +
          " y=" + Math.min(dy, ny).toFixed(3) +
          " w=" + Math.abs(nx - dx).toFixed(3) +
          " h=" + Math.abs(ny - dy).toFixed(3);
      };
      window.addEventListener("pointermove", paint);
      shot.addEventListener("pointerup", async (ev) => {
        window.removeEventListener("pointermove", paint);
        const r = shot.getBoundingClientRect();
        const nx = Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
        const ny = Math.max(0, Math.min(1, (ev.clientY - r.top) / r.height));
        const rect = {
          x: Math.min(dx, nx), y: Math.min(dy, ny),
          w: Math.abs(nx - dx), h: Math.abs(ny - dy),
        };
        if (rect.w < 0.005 || rect.h < 0.005) return;
        const name = window.prompt("区域名（英文，如 gold / shop / bench）", "region" + Object.keys((state.layout && state.layout.regions) || {}).length);
        if (!name) return;
        const kind = window.prompt("类型：digits / shop / units / result", "digits");
        if (!kind) return;
        try {
          const cur = await api("/api/layout");
          const regions = Object.assign({}, cur.layout.regions || {});
          regions[name] = {
            rect,
            kind,
            params: kind === "shop" ? { slots: 4 } : kind === "units" ? { slots: 10 } : {},
          };
          await api("/api/layout", { regions });
          toast("已保存区域 " + name);
          drawing = false;
          $("#btn-draw").classList.remove("on");
          refresh();
        } catch (err) {
          toast(err.message);
        }
      }, { once: true });
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        drawing = false;
        $("#btn-draw").classList.remove("on");
      }
    });

    $("#regions").addEventListener("click", (e) => {
      const cand = e.target.closest("[data-cand]");
      if (cand) {
        const [region, idx, kind, opId] = cand.dataset.cand.split(":");
        e.stopPropagation();
        return learnCandidate(region, Number(idx), kind, opId);
      }
      const learn = e.target.closest("[data-learn-digit]");
      if (learn) return learnDigit(learn.dataset.learnDigit);
      const slot = e.target.closest("[data-slot]");
      if (slot) {
        const [region, idx, kind] = slot.dataset.slot.split(":");
        if (!Object.keys(OP()).length) {
          const opId = window.prompt("这一格是谁？填一个 id（例如 firefly）", "");
          if (!opId) return;
          const url = kind === "units" ? "/api/vision/learn-unit" : "/api/vision/learn-slot";
          return api(url, { region, index: Number(idx), op_id: opId })
            .then(() => { toast("已学 " + opId); refresh(); })
            .catch((err) => toast(err.message));
        }
        return openPicker(region, Number(idx), kind, false);
      }
      const reg = e.target.closest("[data-region]");
      if (reg) {
        state.selected = state.selected === reg.dataset.region ? null : reg.dataset.region;
        render();
      }
    });

    $("#pick-search").addEventListener("input", (e) => renderOps(e.target.value.trim()));
    $("#pick-ops").addEventListener("click", (e) => {
      const op = e.target.closest("[data-op]");
      if (op) {
        state.pickOp = op.dataset.op;
        document.querySelectorAll("#pick-ops .op").forEach((n) => n.classList.toggle("sel", n === op));
        return;
      }
      const st = e.target.closest("[data-star]");
      if (st) {
        state.pickOp = st.dataset.star;
        document.querySelectorAll("#pick-ops .op").forEach((n) => n.classList.toggle("sel", n === st));
      }
    });
    $("#pick-cancel").addEventListener("click", () => $("#picker").classList.remove("open"));
    $("#pick-ok").addEventListener("click", async () => {
      const p = state.pick;
      if (!p || !state.pickOp) return toast("先选一个");
      const body = { region: p.region, index: p.index };
      let url = p.kind === "units" ? "/api/vision/learn-unit" : "/api/vision/learn-slot";
      if (p.kind === "units") body.op_id = state.pickOp;
      else body.op_id = state.pickOp;
      if (p.star) {
        url = "/api/vision/learn-star";
        body.star = Number(state.pickOp);
      }
      try {
        await api(url, body);
        $("#picker").classList.remove("open");
        toast("已学习");
        refresh();
      } catch (err) {
        toast(err.message);
      }
    });

    $("#shot").addEventListener("load", () => {
      requestAnimationFrame(() => drawBoxes(state.report));
      setTimeout(() => drawBoxes(state.report), 120);
    });
    window.addEventListener("resize", () => drawBoxes(state.report));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") $("#picker").classList.remove("open");
      if (e.key === "r" && e.target.tagName !== "INPUT") refresh(true);
    });
  }

  async function boot() {
    bindEvents();
    bindThresholds();
    try {
      state.pack = await api("/api/pack");
    } catch {
      state.pack = null;
    }
    try {
      await refresh();
    } catch {
      /* 没接采集源也能开着 */
    }
  }

  boot();
})();
