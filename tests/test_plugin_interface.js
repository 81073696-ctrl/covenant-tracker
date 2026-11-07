/* 插件操作台结构与可访问性回归：node tests/test_plugin_interface.js */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "../covenant/ui/web/index.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "../covenant/ui/web/style.css"), "utf8");
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
const requiredIds = [
  "ver", "mode", "pack-info", "conn-dot", "conn-text", "btn-new", "btn-auto", "btn-edit", "btn-settings",
  "ledger-warn", "ledger-warn-text", "match-main", "result-hint", "result-hint-text", "result-record",
  "result-dismiss", "round", "rounds-max", "gold", "level", "hp", "phase", "btn-refresh", "cost-refresh",
  "btn-levelup", "cost-level", "btn-next", "gain-next", "btn-freeze", "btn-add", "shop-hint",
  "btn-confirm-shop", "shop", "bench-count", "bench", "board-count", "board", "rules-note", "odds-meta",
  "tiers", "search", "only-owned", "odds", "odds-note", "refresh-note", "refresh-history", "bond-meta",
  "bonds", "eco-meta", "economy", "log", "stats-main", "st-range", "st-kpis", "st-mode", "st-band",
  "st-patch", "st-min", "st-refresh", "st-record", "st-import", "st-upload", "st-export", "st-notes",
  "st-ops", "st-bonds", "st-comps", "st-bands", "st-rounds", "st-round", "st-round-ops", "record",
  "rec-close", "rec-result", "rec-cups", "rec-note", "rec-preview", "rec-save", "importer", "imp-close",
  "imp-file", "imp-text", "imp-go", "settings", "set-close", "set-meta", "set-interval", "set-auto",
  "set-source", "set-save", "uploader", "up-close", "up-url", "up-token", "up-preview", "up-go", "picker",
  "picker-title", "picker-close", "picker-search", "picker-list", "newgame", "ng-close", "ng-mode",
  "ng-band", "ng-group-field", "ng-group", "ng-hp", "ng-start", "toast", "menu",
];

assert.equal(new Set(ids).size, ids.length, "元素 ID 不得重复");
for (const id of requiredIds) assert.ok(ids.includes(id), `缺少 #${id}`);
assert.match(html, /<aside class="intel-panel" aria-label="对局情报">/, "缺少独立情报栏");
assert.match(html, /<section class="panel intel-module odds-panel">\s*<details open>/, "概率模块应默认展开");
assert.equal((html.match(/<section class="panel intel-module/g) || []).length, 4, "应有概率、盟约、经济、日志四个情报模块");
assert.match(css, /@media \(max-width: 899px\)/, "缺少窄窗口布局");
assert.match(css, /@media \(max-width: 899px\)[\s\S]*?\.head-right \{ width: 100%; min-width: 0; \}/, "窄窗口顶部功能组必须换行且不得撑宽页面");
assert.match(css, /@media \(prefers-reduced-motion: reduce\)/, "缺少减少动态效果支持");
assert.match(html, /<link rel="icon" href="data:image\/svg\+xml,/, "应内嵌站点图标，避免 favicon 404");
assert.match(css, /\.shop \{ display: grid; grid-template-columns: repeat\(auto-fit,/, "商店应按实际槽位撑满可用宽度");
assert.match(html, /<button class="primary shop-confirm" id="btn-confirm-shop"/, "确认商店应是明显主按钮");
assert.ok(ids.includes("btn-odds-detail"), "缺少概率详细列开关");
assert.equal((html.match(/class="num odds-detail"/g) || []).length, 3, "单格与实测三列应按需展开");
assert.match(html, /<details class="refresh-details">/, "刷新记录应默认收起");

console.log(`ok - 操作台结构完整，保留 ${requiredIds.length} 个既有元素 ID`);
