/* Render every view of src/hx/ui/static/app.js against tests/ui/fixtures and
 * print what came out as JSON, for tests/ui/test_views_js.py to assert on.
 *
 *   node tests/ui/js/render.js <fixtures dir> <static dir>
 */

"use strict";

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const { buildDocument, collect, NAV } = require("./domshim.js");

const [fixtures, staticDir] = process.argv.slice(2);

const read = (name) => JSON.parse(fs.readFileSync(path.join(fixtures, name), "utf8"));

const ROUTES = {
  "/api/board": () => read("board.json"),
  "/api/orders": () => read("orders.json"),
  "/api/archive": () => read("archive.json"),
  "/api/show/partner": () => read("show-partner.json"),
  "/api/show/eng-001": () => read("show-eng-001.json"),
};

const asked = [];
const posted = [];

global.document = buildDocument();
global.fetch = async (url, init) => {
  const options = init || {};
  if ((options.method || "GET") === "POST") {
    posted.push({ url, body: JSON.parse(options.body), credentials: options.credentials });
    return { ok: true, status: 200, json: async () => ({ delivered: true }) };
  }
  asked.push({ url, credentials: options.credentials, headers: options.headers || {} });
  const route = ROUTES[url];
  if (!route) return { ok: false, status: 404, json: async () => ({ error: "no such path: " + url }) };
  return { ok: true, status: 200, json: async () => route() };
};
global.EventSource = class {
  constructor(url) {
    asked.push({ url, sse: true });
  }
};

vm.runInThisContext(fs.readFileSync(path.join(staticDir, "app.js"), "utf8"), { filename: "app.js" });

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const quiet = async () => {
  for (let i = 0; i < 6; i++) await settle();
};

const nav = document.getElementById("nav");
const main = document.getElementById("main");

async function show(view) {
  nav.listeners.click({ target: nav.descendants().find((n) => n.dataset.view === view) });
  await quiet();
}

/** Click an id button inside the rendered view, the way a human opens an agent. */
async function open(id) {
  const button = main.descendants().find((n) => n.dataset.open === id);
  if (!button) throw new Error("render.js: no [data-open=" + id + "] button in the current view");
  main.listeners.click({ target: button });
  await quiet();
}

function snapshot() {
  return {
    headings: collect(main, "h2").map((n) => n.textContent),
    subheadings: collect(main, "h3").map((n) => n.textContent),
    rows: collect(main, "tr")
      .filter((row) => collect(row, "td").length)
      .map((row) => ({ class: row.className, cells: collect(row, "td").map((c) => c.textContent.trim()) })),
    headers: collect(main, "th").map((n) => n.textContent),
    pills: collect(main, "span")
      .filter((n) => n.className.startsWith("pill"))
      .map((n) => ({ class: n.className, text: n.textContent })),
    pre: collect(main, "pre").map((n) => ({ class: n.className, text: n.textContent })),
    listItems: collect(main, "li").map((n) => ({ class: n.className, text: n.textContent })),
    code: collect(main, "code").map((n) => ({ class: n.className, text: n.textContent })),
    notYet: collect(main, "p").filter((n) => n.className === "notyet").length,
    text: main.textContent,
    errorBlocks: collect(main, "section")
      .filter((n) => n.className === "errors")
      .map((n) => ({
        heading: collect(n, "h2").map((h) => h.textContent)[0],
        items: collect(n, "li").map((li) => li.textContent),
      })),
    edges: collect(main, "div")
      .filter((n) => n.className.startsWith("edge"))
      .map((n) => ({ class: n.className, text: n.textContent })),
    openable: main.descendants().filter((n) => n.dataset.open).map((n) => n.dataset.open),
  };
}

(async () => {
  const out = { views: {}, navigation: NAV, requests: asked, posted: [], banner: null };
  await quiet();
  out.views.board = snapshot();

  for (const view of ["orders", "archive"]) {
    await show(view);
    out.views[view] = snapshot();
  }

  // The Agent view with no id yet is a picker; opening an id from the board fills it.
  await show("agent");
  out.views.agentPicker = snapshot();
  await show("board");
  await open("eng-001");
  out.views.agent = snapshot();

  await show("partner");
  out.views.partner = snapshot();

  // The chat box is the page's only write path.
  const box = main.descendants().find((n) => n.tagName === "TEXTAREA");
  const send = main.descendants().find((n) => n.dataset.open === undefined && n.className === "send");
  box.value = "  eng-003 complete: decision; hx read eng-003  ";
  send.listeners.click();
  await quiet();
  out.posted = posted;
  out.wakeStatus = main.descendants().find((n) => n.attrs.id === "wake-status").textContent;
  out.wakeCleared = box.value === "";

  out.banner = document.getElementById("banner").hidden ? null : document.getElementById("banner").textContent;
  process.stdout.write(JSON.stringify(out, null, 2));
})().catch((error) => {
  process.stderr.write(String(error && error.stack ? error.stack : error));
  process.exit(1);
});
