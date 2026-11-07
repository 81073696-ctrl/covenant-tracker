/* 可选桌面桥接：普通浏览器中没有 pywebview 时完全不改变界面。 */
(function (root, factory) {
  const createDesktopController = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = { createDesktopController };
    return;
  }
  function startDesktop() {
    if (!root.pywebview?.api || root.CovenantDesktop) return;
    const notify = (message) => {
      const toast = document.querySelector("#toast");
      if (!toast) return;
      toast.textContent = message;
      toast.classList.remove("hidden");
      toast.classList.toggle("bad", true);
      clearTimeout(root.__desktopToastTimer);
      root.__desktopToastTimer = setTimeout(() => toast.classList.add("hidden"), 3600);
    };
    const controller = createDesktopController(root, document, notify);
    root.CovenantDesktop = controller;
    controller.init();
  }
  // WebView2 可能在本脚本加载前就发出 ready；两种时序都要覆盖。
  if (root.pywebview?.api) startDesktop();
  else root.addEventListener("pywebviewready", startDesktop, { once: true });
})(typeof window !== "undefined" ? window : globalThis, function () {
  return function createDesktopController(win, doc, notify) {
    let api = null;
    let ready = false;
    let state = { compact: false, on_top: false };
    const compactButton = () => doc.querySelector("#btn-compact");
    const topButton = () => doc.querySelector("#btn-topmost");

    function apply(next) {
      state = { compact: !!next.compact, on_top: !!next.on_top };
      doc.body.classList.toggle("compact", state.compact);
      const compact = compactButton();
      const top = topButton();
      compact.textContent = state.compact ? "完整模式" : "紧凑模式";
      compact.setAttribute("aria-pressed", String(state.compact));
      top.textContent = state.on_top ? "取消置顶" : "置顶";
      top.setAttribute("aria-pressed", String(state.on_top));
      top.classList.toggle("on", state.on_top);
    }

    function report(error) {
      notify(`桌面窗口操作失败：${error?.message || error}`);
    }

    const setCompact = (value) => operate("set_compact", !!value);
    const setOnTop = (value) => operate("set_on_top", !!value);

    async function init() {
      try {
        api = win.pywebview?.api;
        if (!api) return;
        apply(await api.get_state());
        // Kimi：hidden 属性与 .hidden（display:none !important）必须同步，否则按钮仍不可见。
        doc.querySelectorAll(".desktop-only").forEach((button) => {
          button.hidden = false;
          button.classList.toggle("hidden", false);
        });
        compactButton().addEventListener("click", () => setCompact(!state.compact));
        topButton().addEventListener("click", () => setOnTop(!state.on_top));
        ready = true;
      } catch (error) {
        ready = false;
        doc.querySelectorAll(".desktop-only").forEach((button) => {
          button.hidden = true;
          button.classList.toggle("hidden", true);
        });
        report(error);
      }
    }

    async function operate(action, value) {
      if (!ready) return;
      const buttons = [compactButton(), topButton()];
      buttons.forEach((button) => { button.disabled = true; });
      try {
        apply(await api[action](value));
      } catch (error) {
        report(error);
      } finally {
        buttons.forEach((button) => { button.disabled = false; });
      }
    }

    return {
      init,
      setCompact,
      setOnTop,
      ensureFull: () => ready && state.compact ? setCompact(false) : Promise.resolve(),
    };
  };
});
