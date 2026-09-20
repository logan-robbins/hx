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
const TOKEN = "render-token";

const read = (name) => JSON.parse(fs.readFileSync(path.join(fixtures, name), "utf8"));

const ROUTES = {
  "/api/board": () => read("board.json"),
  "/api/orders": () => read("orders.json"),
  "/api/archive": () => read("archive.json"),
};

const asked = [];

global.document = buildDocument(TOKEN);
global.fetch = async (url, init) => {
  asked.push({ url, headers: (init && init.headers) || {} });
  const route = ROUTES[url];
  if (!route) return { ok: false, status: 404, json: async () => ({ error: "not found" }) };
  return { ok: true, status: 200, json: async () => route() };
};
global.EventSource = class {
  constructor(url) {
    asked.push({ url, sse: true });
  }
};

vm.runInThisContext(fs.readFileSync(path.join(staticDir, "app.js"), "utf8"), { filename: "app.js" });

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

/** Click a nav button the way the delegated listener sees it. */
async function show(view) {
  const button = document.getElementById("nav").descendants().find((n) => n.dataset.view === view);
  document.getElementById("nav").listeners.click({ target: button });
  await settle();
  await settle();
}

function snapshot() {
  const main = document.getElementById("main");
  return {
    headings: collect(main, "h2").map((n) => n.textContent),
    rows: collect(main, "tr")
      .filter((row) => collect(row, "td").length)
      .map((row) => ({
        class: row.className,
        cells: collect(row, "td").map((cell) => cell.textContent.trim()),
      })),
    headers: collect(main, "th").map((n) => n.textContent),
    pills: collect(main, "span")
      .filter((n) => n.className.startsWith("pill"))
      .map((n) => ({ class: n.className, text: n.textContent })),
    pre: collect(main, "pre").map((n) => n.textContent),
    text: main.textContent,
    errorBlocks: collect(main, "section")
      .filter((n) => n.className === "errors")
      .map((n) => ({ heading: collect(n, "h2").map((h) => h.textContent)[0], items: collect(n, "li").map((li) => li.textContent) })),
    edges: collect(main, "div")
      .filter((n) => n.className.startsWith("edge"))
      .map((n) => ({ class: n.className, text: n.textContent })),
  };
}

(async () => {
  const out = { views: {}, navigation: NAV, requests: asked, banner: null };
  await settle();
  await settle();
  out.views.board = snapshot();
  for (const view of ["orders", "archive", "agent", "partner"]) {
    await show(view);
    out.views[view] = snapshot();
  }
  out.banner = document.getElementById("banner").hidden ? null : document.getElementById("banner").textContent;
  out.navOn = document
    .getElementById("nav")
    .descendants()
    .filter((n) => n.classList.contains("on"))
    .map((n) => n.dataset.view);
  process.stdout.write(JSON.stringify(out, null, 2));
})();
