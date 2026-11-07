/* 独立窗口 JS 桥接：node tests/test_desktop_frontend.js */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { createDesktopController } = require("../covenant/ui/web/desktop.js");

class ClassList {
  constructor(initial = []) { this.values = new Set(initial); }
  contains(name) { return this.values.has(name); }
  toggle(name, force) {
    if (force) this.values.add(name);
    else this.values.delete(name);
  }
}

function element(hidden = true) {
  const listeners = {};
  const attributes = {};
  return {
    hidden,
    disabled: false,
    textContent: "",
    classList: new ClassList(hidden ? ["hidden"] : []),
    addEventListener(name, callback) { listeners[name] = callback; },
    setAttribute(name, value) { attributes[name] = String(value); },
    getAttribute(name) { return attributes[name] ?? null; },
    async click() { await listeners.click(); },
  };
}

function documentMock() {
  const elements = {
    body: { classList: new ClassList() },
    "#btn-compact": element(),
    "#btn-topmost": element(),
  };
  return {
    elements,
    body: elements.body,
    querySelector(selector) { return elements[selector]; },
    querySelectorAll(selector) {
      return selector === ".desktop-only" ? [elements["#btn-compact"], elements["#btn-topmost"]] : [];
    },
  };
}

async function testBridge() {
  const doc = documentMock();
  const api = {
    get_state: async () => ({ compact: false, on_top: false }),
    set_compact: async (value) => ({ compact: value, on_top: value }),
    set_on_top: async (value) => ({ compact: false, on_top: value }),
  };
  const notices = [];
  const controller = createDesktopController({ pywebview: { api } }, doc, (message) => notices.push(message));
  await controller.init();
  assert.deepEqual(notices, []);
  assert.equal(doc.elements["#btn-compact"].hidden, false);
  assert.equal(doc.elements["#btn-compact"].classList.contains("hidden"), false, "初始化后必须移除 CSS 强制隐藏类");
  await controller.setCompact(true);
  assert.equal(doc.elements.body.classList.contains("compact"), true);
  assert.equal(doc.elements["#btn-compact"].textContent, "完整模式");
  await controller.ensureFull();
  assert.equal(doc.elements.body.classList.contains("compact"), false);
  await controller.setOnTop(true);
  assert.equal(doc.elements["#btn-topmost"].getAttribute("aria-pressed"), "true");
  await doc.elements["#btn-compact"].click();
  assert.equal(doc.elements.body.classList.contains("compact"), true);
}

async function testFailureStaysContained() {
  const doc = documentMock();
  const notices = [];
  const controller = createDesktopController(
    { pywebview: { api: { get_state: async () => { throw new Error("no bridge"); } } } },
    doc,
    (message) => notices.push(message),
  );
  await controller.init();
  assert.equal(doc.elements["#btn-compact"].hidden, true);
  assert.equal(doc.elements["#btn-compact"].classList.contains("hidden"), true, "桥接失败时保留 CSS 隐藏类");
  assert.match(notices[0], /桌面窗口操作失败/);
  await controller.ensureFull();
}

async function testBootstrapWhenReadyEventAlreadyFired() {
  const doc = documentMock();
  const window = {
    pywebview: { api: { get_state: async () => ({ compact: false, on_top: false }) } },
    addEventListener() {},
    setTimeout,
    clearTimeout,
  };
  vm.createContext({ window, document: doc, setTimeout, clearTimeout });
  const source = fs.readFileSync(`${__dirname}/../covenant/ui/web/desktop.js`, "utf8");
  vm.runInContext(source, vm.createContext({ window, document: doc, setTimeout, clearTimeout }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(doc.elements["#btn-compact"].hidden, false, "API 已就绪时不应再等待已经错过的事件");
}

Promise.resolve()
  .then(testBridge)
  .then(testFailureStaysContained)
  .then(testBootstrapWhenReadyEventAlreadyFired)
  .then(() => console.log("ok - 桌面桥接、紧凑切换、启动时序与异常回退"))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
