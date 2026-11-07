/* 无框架前端行为回归测试：node tests/test_frontend.js */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../covenant/ui/web/app.js"), "utf8");
const refreshSource = source.slice(source.indexOf("async function refresh()"), source.indexOf("let CAPTURE ="));
const warningSource = source.slice(source.indexOf("function renderLedgerWarn()"), source.indexOf("function render()"));
const box = { hidden: true, classList: { toggle(_name, hidden) { box.hidden = hidden; } } };
const text = { textContent: "" };
const context = {
  SNAP_ETAG: null,
  SNAP: { ledger: { error: null } },
  VIEW: "stats",
  document: { hidden: false },
  $: (selector) => selector === "#ledger-warn" ? box : text,
  fetch: async () => ({
    status: 200,
    ok: true,
    headers: { get: () => null },
    json: async () => ({ ledger: { error: "账本写入失败" } }),
  }),
  refreshCapture: async () => {},
  render: () => { throw new Error("统计页不应调用完整对局渲染"); },
  setConn: () => {},
};

async function testStatsRefreshUpdatesLedgerWarning() {
  vm.createContext(context);
  vm.runInContext(refreshSource + "\n" + warningSource, context);
  await context.refresh();
  assert.equal(box.hidden, false);
  assert.equal(text.textContent, "账本写入失败");
}

testStatsRefreshUpdatesLedgerWarning().then(() => {
  console.log("ok - 统计页轮询更新全局账本提示");
}).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
