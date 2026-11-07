/* 教它认界面：拖框标区域、学数字、学干员格子。
   所有坐标以「相对客户区的比例」保存，换分辨率不用重标。 */
(() => {
  const $ = (sel) => document.querySelector(sel);
  const state = { layout: null, ops: [], drag: null, pending: null, imgW: 0, imgH: 0 };

  // ---------- 基础 ----------
  async function api(path, data) {
    const opt = data === undefined
      ? {}
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) };
    const res = await fetch(path, opt);
    let body = null;
    try { body = await res.json(); } catch (_) { /* 可能是图片 */ }
    if (!res.ok) throw new Error((body && body.error) || `HTTP ${res.status}`);
    return body;
  }

  let toastTimer = null;
  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
  }

  // ---------- 布局 ----------
  function ensureLayout(layout) {
    layout = layout || {};
    if (!layout.regions || Array.isArray(layout.regions)) layout.regions = {};
    layout.schema = layout.schema || 1;
    state.layout = layout;
  }

  function regionEntries() {
    return Object.entries(state.layout.regions || {});
  }

  function renderRegionList() {
    const box = $("#regions");
    box.innerHTML = "";
    const entries = regionEntries();
    if (!entries.length) {
      box.innerHTML = '<div class="hint">还没有区域，直接在左边图上拖一个框。</div>';
    }
    for (const [name, reg] of entries) {
      const row = document.createElement("div");
      row.className = "reg";
      row.innerHTML = `
        <input data-k="name" value="${escapeHtml(name)}" title="区域名">
        <select data-k="kind">
          <option value="digits">digits</option>
          <option value="shop">shop</option>
          <option value="units">units</option>
          <option value="result">result</option>
        </select>
        <input data-k="slots" type="number" min="1" max="12" value="${reg.params && reg.params.slots ? reg.params.slots : ""}" title="格子数">
        <button class="del" title="删除">✕</button>`;
      row.querySelector('[data-k="kind"]').value = reg.kind || "digits";
      row.querySelector('[data-k="kind"]').onchange = (e) => { reg.kind = e.target.value; draw(); };
      row.querySelector('[data-k="slots"]').onchange = (e) => {
        reg.params = reg.params || {};
        reg.params.slots = Math.max(1, parseInt(e.target.value || "1", 10));
        draw();
      };
      row.querySelector('[data-k="name"]').onchange = (e) => {
        const next = e.target.value.trim();
        if (!next || next === name) return;
        delete state.layout.regions[name];
        state.layout.regions[next] = reg;
        renderAll();
      };
      row.querySelector(".del").onclick = () => { delete state.layout.regions[name]; renderAll(); };
      box.appendChild(row);
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ---------- 画布 ----------
  function syncCanvas() {
    const img = $("#shot");
    const c = $("#overlay");
    if (!img.clientWidth) return;
    c.style.left = img.offsetLeft + "px";
    c.style.top = img.offsetTop + "px";
    c.style.transform = "none";
    c.style.width = img.clientWidth + "px";
    c.style.height = img.clientHeight + "px";
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.round(img.clientWidth * dpr);
    c.height = Math.round(img.clientHeight * dpr);
    const ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw();
  }

  function draw() {
    const c = $("#overlay");
    const ctx = c.getContext("2d");
    ctx.clearRect(0, 0, c.width, c.height);
    const W = c.clientWidth, H = c.clientHeight;
    if (!W || !H || !state.layout) return;

    const colors = { digits: "#f6a329", shop: "#5aa9e6", units: "#69c46b" };
    for (const [name, reg] of regionEntries()) {
      const [x, y, w, h] = reg.rect || [0, 0, 0, 0];
      const px = x * W, py = y * H, pw = w * W, ph = h * H;
      const color = colors[reg.kind] || "#999";
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.strokeRect(px + 0.5, py + 0.5, pw, ph);
      ctx.fillStyle = color;
      ctx.font = "12px Consolas, monospace";
      ctx.fillRect(px, Math.max(0, py - 16), Math.min(W - px, ctx.measureText(name).width + 10), 16);
      ctx.fillStyle = "#0d0f12";
      ctx.fillText(name, px + 4, Math.max(12, py - 4));

      // shop/units 画出格子切分
      if ((reg.kind === "shop" || reg.kind === "units") && pw > 10) {
        const n = Math.max(1, parseInt(reg.params && reg.params.slots, 10) || 1);
        ctx.strokeStyle = color + "88";
        ctx.lineWidth = 1;
        for (let i = 1; i < n; i++) {
          const gx = px + (pw * i) / n;
          ctx.beginPath(); ctx.moveTo(gx, py); ctx.lineTo(gx, py + ph); ctx.stroke();
        }
      }
    }

    if (state.drag && state.drag.moved) {
      const d = state.drag;
      ctx.strokeStyle = "#fff";
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1;
      ctx.strokeRect(Math.min(d.x0, d.x1), Math.min(d.y0, d.y1), Math.abs(d.x1 - d.x0), Math.abs(d.y1 - d.y0));
      ctx.setLineDash([]);
    }
  }

  function normFromEvent(e) {
    const c = $("#overlay");
    const r = c.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
    };
  }

  // ---------- 拖框 ----------
  function bindDrag() {
    const c = $("#overlay");
    c.addEventListener("pointerdown", (e) => {
      const p = normFromEvent(e);
      state.drag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y, moved: false };
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener("pointermove", (e) => {
      if (!state.drag) return;
      const p = normFromEvent(e);
      const d = state.drag;
      d.x1 = p.x; d.y1 = p.y;
      if (Math.abs(d.x1 - d.x0) > 0.004 || Math.abs(d.y1 - d.y0) > 0.004) d.moved = true;
      draw();
    });
    c.addEventListener("pointerup", () => {
      const d = state.drag;
      state.drag = null;
      if (!d || !d.moved) { draw(); return; }
      const rect = [
        Math.min(d.x0, d.x1), Math.min(d.y0, d.y1),
        Math.abs(d.x1 - d.x0), Math.abs(d.y1 - d.y0),
      ].map((v) => Math.round(v * 10000) / 10000);
      state.pending = rect;
      const names = regionEntries().map(([n]) => n);
      $("#m-name").value = names.includes("shop") ? "" : "shop";
      $("#modal").classList.add("show");
      $("#m-name").focus();
      draw();
    });
  }

  function addPending() {
    const name = $("#m-name").value.trim();
    const kind = $("#m-kind").value;
    const slots = Math.max(1, parseInt($("#m-slots").value || "1", 10));
    if (!name) { toast("先给区域起个名字"); return; }
    if (!state.pending) { $("#modal").classList.remove("show"); return; }
    const reg = { rect: state.pending, kind };
    if (kind === "shop" || kind === "units") reg.params = { slots };
    state.layout.regions[name] = reg;
    state.pending = null;
    $("#modal").classList.remove("show");
    renderAll();
    toast(`已加上 ${name}`);
  }

  // ---------- 渲染 ----------
  function renderAll() {
    renderRegionList();
    renderRegionSelects();
    draw();
  }

  function renderRegionSelects() {
    const picks = {
      "digit-region": (r) => r.kind === "digits",
      "slot-region": (r) => r.kind === "shop" || r.kind === "units",
      "result-region": (r) => r.kind === "result",
    };
    for (const [id, ok] of Object.entries(picks)) {
      const sel = $(`#${id}`);
      const cur = sel.value;
      sel.innerHTML = "";
      for (const [name, reg] of regionEntries()) {
        if (!ok(reg)) continue;
        const o = document.createElement("option");
        o.value = name; o.textContent = name;
        sel.appendChild(o);
      }
      if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
    }
  }

  function renderOps() {
    const sel = $("#op-select");
    sel.innerHTML = "";
    const ops = state.ops.slice().sort((a, b) => (a.tier - b.tier) || String(a.name).localeCompare(String(b.name), "zh"));
    for (const op of ops) {
      const o = document.createElement("option");
      o.value = op.id;
      o.textContent = `${op.name} · T${op.tier}`;
      sel.appendChild(o);
    }
  }

  function renderWindows(wins) {
    const sel = $("#win-list");
    sel.innerHTML = '<option value="">— 已打开的窗口 —</option>';
    for (const w of wins) {
      const o = document.createElement("option");
      o.value = w.title;
      o.textContent = `${w.title}（${w.width}×${w.height}）`;
      sel.appendChild(o);
    }
  }

  // ---------- 数据 ----------
  async function loadLayout() {
    const r = await api("/api/layout");
    ensureLayout(r.layout);
    renderAll();
  }

  async function loadPack() {
    const pack = await api("/api/pack");
    let ops = pack.operators || [];
    if (!Array.isArray(ops)) {
      ops = Object.entries(ops).map(([id, v]) => (v && typeof v === "object" ? { id, ...v } : { id, name: id }));
    }
    state.ops = ops.filter((o) => o && o.id);
    renderOps();
  }

  async function loadWindows() {
    try {
      const r = await api("/api/windows");
      renderWindows(r.windows || []);
    } catch (e) { /* 非 Windows 或枚举失败就算了 */ }
  }

  async function refreshFrame() {
    const img = $("#shot");
    await new Promise((resolve) => {
      img.onload = resolve;
      img.onerror = resolve;
      img.src = "/api/frame?t=" + Date.now();
    });
    if (img.naturalWidth) {
      state.imgW = img.naturalWidth; state.imgH = img.naturalHeight;
      $("#frame-hint").textContent = `已截图 ${img.naturalWidth}×${img.naturalHeight}`;
      syncCanvas();
    } else {
      $("#frame-hint").textContent = "没有采集源：在上面连接一个窗口，或确认游戏已打开。";
    }
  }

  async function connectWindow(title) {
    if (!title) { toast("先填窗口标题"); return; }
    try {
      const r = await api("/api/frame-source", { name: title });
      toast("已连接：" + (r.window || title));
      await refreshFrame();
    } catch (e) { toast("连接失败：" + e.message); }
  }

  // ---------- 事件 ----------
  function bind() {
    $("#btn-connect").onclick = () => connectWindow($("#win").value.trim() || $("#win-list").value);
    $("#win-list").onchange = (e) => { if (e.target.value) { $("#win").value = e.target.value; connectWindow(e.target.value); } };
    $("#btn-shot").onclick = refreshFrame;
    $("#btn-save").onclick = async () => {
      try {
        await api("/api/layout", { layout: state.layout });
        toast("布局已保存到 var/layout.json");
      } catch (e) { toast("保存失败：" + e.message); }
    };
    $("#btn-reset").onclick = async () => {
      try {
        const r = await api("/api/layout/default");
        ensureLayout(r.layout);
        renderAll();
        toast("已恢复默认区域（记得保存）");
      } catch (e) { toast("失败：" + e.message); }
    };
    $("#btn-learn-digit").onclick = async () => {
      const region = $("#digit-region").value;
      const text = $("#digit-text").value.trim();
      if (!region || !text) { toast("选区域并填数字"); return; }
      try {
        const r = await api("/api/vision/learn-digit", { region, text });
        toast(`学了 ${r.learned} 个字形`);
      } catch (e) { toast("学习失败：" + e.message); }
    };
    $("#btn-learn-slot").onclick = async () => {
      const region = $("#slot-region").value;
      const op_id = $("#op-select").value;
      const index = parseInt($("#slot-index").value || "0", 10);
      if (!region || !op_id) { toast("选区域和干员"); return; }
      try {
        await api("/api/vision/learn-slot", { region, index, op_id });
        toast("学了这一格");
      } catch (e) { toast("学习失败：" + e.message); }
    };
    $("#btn-learn-result").onclick = async () => {
      const region = $("#result-region").value;
      const result = $("#result-kind").value;
      if (!region) { toast("先拖一个 kind=result 的区域"); return; }
      try {
        await api("/api/vision/learn-result", { region, result });
        toast(result === "clear" ? "学了「通关」模板" : "学了「失败」模板");
      } catch (e) { toast("学习失败：" + e.message); }
    };
    $("#btn-read").onclick = async () => {
      $("#read-out").textContent = "识别中…";
      try {
        const r = await api("/api/vision/read", {});
        $("#read-out").textContent = JSON.stringify(r.observations, null, 2);
        toast(`产出 ${r.count} 条观测`);
      } catch (e) {
        $("#read-out").textContent = "失败：" + e.message;
      }
    };
    $("#m-cancel").onclick = () => { state.pending = null; $("#modal").classList.remove("show"); draw(); };
    $("#m-ok").onclick = addPending;
    $("#m-name").onkeydown = (e) => { if (e.key === "Enter") addPending(); };
    window.addEventListener("resize", syncCanvas);
  }

  // ---------- 启动 ----------
  (async function boot() {
    bind();
    bindDrag();
    try { await loadLayout(); } catch (e) { toast("加载布局失败：" + e.message); }
    try { await loadPack(); } catch (e) { /* 数据包坏了主面板也会提示 */ }
    loadWindows();
    refreshFrame();
  })();
})();
