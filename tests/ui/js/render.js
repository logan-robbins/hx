/* Render every screen of src/hx/ui/static/app.js against tests/ui/fixtures and
 * print what came out as JSON, for tests/ui/test_views_js.py to assert on.
 *
 *   node tests/ui/js/render.js <fixtures dir> <static dir> [overrides.json] [ids]
 *
 * The document is the real `static/index.html`, parsed by the shim, so the
 * harness walks the page the server serves: the sidebar, the tabs, the graph,
 * the board, the agent table, the chat and the drawer.
 *
 * `ids` is a comma-separated list of agent ids to open in the drawer, in turn;
 * each one's snapshot lands in `views.agents[<id>]`. Default: eng-001.
 *
 * `overrides.json` maps an API path to the document to serve instead of the
 * fixture (a null means "this endpoint fails"), so a test can render a variant
 * without a second set of fixture files. */

"use strict";

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const { buildDocument, fire } = require("./domshim.js");

const [fixtures, staticDir, overridesPath, openIds] = process.argv.slice(2);
const OPEN = (openIds || "eng-001").split(",").filter(Boolean);
const OVERRIDES = overridesPath ? JSON.parse(fs.readFileSync(overridesPath, "utf8")) : {};

const read = (name) => JSON.parse(fs.readFileSync(path.join(fixtures, name), "utf8"));
const exists = (name) => fs.existsSync(path.join(fixtures, name));

const asked = [];
const posted = [];
/* `__wake__` in the overrides is what `POST /api/partner/wake` answers, so a
 * test can exercise the three CONTRACTS.md statuses. */
let wakeAnswer = OVERRIDES.__wake__ || { delivered: true, status: "accepted" };

const doc = buildDocument(fs.readFileSync(path.join(staticDir, "index.html"), "utf8"));
global.document = doc;

/* The address bar, with the one behaviour app.js depends on: setting `hash`
 * fires `hashchange`. */
const listeners = {};
global.window = {
  addEventListener: (type, handler) => ((listeners[type] = listeners[type] || []).push(handler)),
  scrollTo: () => {},
};
global.location = {
  _hash: "",
  get hash() {
    return this._hash;
  },
  set hash(value) {
    const next = value.startsWith("#") ? value : "#" + value;
    if (next === this._hash) return;
    this._hash = next;
    for (const handler of listeners.hashchange || []) handler({});
  },
};
global.history = { replaceState: () => {} };

global.fetch = async (url, init) => {
  const options = init || {};
  if ((options.method || "GET") === "POST") {
    posted.push({ url, body: JSON.parse(options.body), credentials: options.credentials });
    return { ok: true, status: 200, json: async () => wakeAnswer };
  }
  asked.push({ url, credentials: options.credentials, headers: options.headers || {} });
  if (Object.hasOwn(OVERRIDES, url)) {
    if (OVERRIDES[url] === null) {
      return { ok: false, status: 404, json: async () => ({ error: "no such id: " + url }) };
    }
    return { ok: true, status: 200, json: async () => OVERRIDES[url] };
  }
  const name = {
    "/api/board": "board.json",
    "/api/orders": "orders.json",
    "/api/archive": "archive.json",
  }[url];
  const show = /^\/api\/show\/(.+)$/.exec(url);
  const file = name || (show ? "show-" + decodeURIComponent(show[1]) + ".json" : null);
  if (!file || !exists(file)) {
    return { ok: false, status: 404, json: async () => ({ error: "no such path: " + url }) };
  }
  return { ok: true, status: 200, json: async () => read(file) };
};
global.EventSource = class {
  constructor(url) {
    asked.push({ url, sse: true });
    EventSourceStub.last = this;
  }
};
const EventSourceStub = global.EventSource;

vm.runInThisContext(fs.readFileSync(path.join(staticDir, "app.js"), "utf8"), { filename: "app.js" });

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const quiet = async () => {
  for (let i = 0; i < 30; i++) await settle();
};

const content = doc.getElementById("content");
const drawer = doc.getElementById("drawer");

async function go(hash) {
  location.hash = hash;
  await quiet();
}

async function click(node) {
  fire(doc, node, "click");
  await quiet();
}

/* -- snapshots --------------------------------------------------------- */

const textOf = (node) => (node ? node.textContent.replace(/\s+/g, " ").trim() : null);
const all = (root, selector) => (root ? root.querySelectorAll(selector) : []);

function labelled(root) {
  /* `.small-label` names each block of the drawer and of a panel body; the
   * value is whatever follows it until the next label. */
  const out = [];
  for (const label of all(root, ".small-label")) {
    const siblings = label.parentNode.childNodes;
    const start = siblings.indexOf(label);
    const body = [];
    for (let i = start + 1; i < siblings.length; i++) {
      const node = siblings[i];
      if (node.nodeType === 1 && node.classList && node.classList.contains("small-label")) break;
      body.push(node.textContent);
    }
    out.push({ label: textOf(label), text: body.join(" ").replace(/\s+/g, " ").trim() });
  }
  return out;
}

function snapshot(root) {
  return {
    banner: doc.getElementById("error-banner").hidden ? null : textOf(doc.getElementById("error-banner")),
    breadcrumb: all(doc.getElementById("breadcrumb"), "a").map(textOf).concat(all(doc.getElementById("breadcrumb"), "strong").map(textOf)),
    heading: textOf(all(root, "h1")[0]),
    headings: all(root, "h2").map(textOf),
    subheadings: all(root, "h3").map(textOf),
    tabs: all(root, ".tab").map((n) => ({ label: textOf(n), selected: n.classList.contains("selected") })),
    badges: all(root, ".badge").map((n) => ({ class: n.className, text: textOf(n) })),
    pills: all(root, ".pill").map((n) => ({ class: n.className, text: textOf(n) })),
    rows: all(root, "tr")
      .filter((row) => all(row, "td").length)
      .map((row) => ({ class: row.className, agent: row.dataset.agent || null, cells: all(row, "td").map(textOf) })),
    headers: all(root, "th").map(textOf),
    pre: all(root, "pre").map((n) => ({ class: n.className, text: n.textContent })),
    listItems: all(root, "li").map((n) => ({ class: n.className, text: textOf(n) })),
    code: all(root, "code").map((n) => ({ class: n.className, text: textOf(n) })),
    labels: labelled(root),
    notYet: all(root, ".notyet").length,
    empty: all(root, ".empty").map(textOf),
    followups: all(root, ".followup").map((n) => ({ class: n.className, title: n.getAttribute("title"), text: textOf(n) })),
    metrics: metricsTable(root),
    openable: all(root, "[data-agent]").map((n) => n.dataset.agent),
    text: root.textContent.replace(/\s+/g, " ").trim(),
  };
}

function metricsTable(root) {
  const table = all(root, "table.metrics")[0];
  if (!table) return null;
  const row = (n) => ({
    class: n.className,
    title: n.getAttribute("title"),
    cells: all(n, "td").map((c) => ({ class: c.className, text: textOf(c) })),
  });
  return {
    headers: all(all(table, "thead")[0], "th").map(textOf),
    rows: all(all(table, "tbody")[0], "tr").map(row),
    totals: all(all(table, "tfoot")[0] || table, "tr").map(row)[0] || null,
    warn: all(root, ".metrics-warn").map(textOf),
    ok: all(root, ".metrics-ok").map(textOf),
  };
}

function graphSnapshot() {
  return {
    nodes: all(content, ".agent-node").map((n) => ({
      agent: n.dataset.agent,
      class: n.className,
      name: textOf(all(n, ".node-top strong")[0]),
      task: textOf(all(n, ".node-task")[0]),
      stage: textOf(all(n, ".node-bottom .stage")[0]),
      left: n.offsetLeft,
      top: n.offsetTop,
    })),
    clusters: all(content, ".cluster").map((n) => ({
      pod: n.dataset.pod,
      class: n.className,
      label: textOf(all(n, ".cluster-label")[0]),
    })),
    companions: all(content, ".companion-node").map((n) => textOf(n)),
    edges: all(content, "path").map((n) => ({ class: n.className, title: textOf(all(n, "title")[0]) })),
    legend: all(content, ".graph-legend span").map(textOf),
    zoom: textOf(doc.getElementById("zoom-value")),
  };
}

function boardSnapshot() {
  return {
    columns: all(content, ".column").map((n) => ({
      label: textOf(all(n, ".column-head")[0]),
      cards: all(n, ".task-card").length,
      empty: all(n, ".column-empty").length > 0,
    })),
    cards: all(content, ".task-card").map((n) => ({
      agent: n.dataset.agent,
      title: textOf(all(n, "h3")[0]),
      tasks: all(n, ".card-tasks li").map((li) => ({ done: li.classList.contains("done"), text: textOf(li) })),
      noTasks: all(n, ".card-tasks-empty").map(textOf)[0] || null,
      progress: textOf(all(n, ".task-progress")[0]),
      time: textOf(all(n, ".time")[0]),
    })),
  };
}

function sidebarSnapshot() {
  const nav = doc.getElementById("pod-nav");
  return {
    pods: all(nav, ".pillar-item").map((n) => ({ text: textOf(n), selected: n.classList.contains("selected") })),
    agents: all(nav, ".agent-child").map((n) => ({ text: textOf(n), selected: n.classList.contains("selected") })),
    links: doc.querySelectorAll("[data-nav]").map((n) => ({
      nav: n.dataset.nav,
      text: textOf(n),
      selected: n.classList.contains("selected"),
    })),
    workspace: textOf(doc.getElementById("workspace-name")),
    footer: textOf(doc.getElementById("footer-info")),
    connection: textOf(doc.getElementById("connection")),
  };
}

(async () => {
  const out = { views: {}, requests: asked, posted: [], banner: null };
  await go("#overview");
  out.views.overview = Object.assign(snapshot(content), { graph: graphSnapshot(), sidebar: sidebarSnapshot() });

  await go("#board");
  out.views.board = Object.assign(snapshot(content), boardSnapshot());

  await go("#agents");
  out.views.agentTable = snapshot(content);

  await go("#activity");
  out.views.activity = snapshot(content);

  await go("#orders");
  out.views.orders = snapshot(content);

  await go("#archive");
  out.views.archive = snapshot(content);

  // The drawer: opened the way a human opens it, by clicking the agent.
  // `views.agents[<id>]` is that agent's drawer — the Agent page of spec 16.2,
  // which the restored shell opens over whatever page you were on.
  out.views.agents = {};
  for (const id of OPEN) {
    await go("#overview");
    const node = content.querySelectorAll('[data-agent="' + id + '"]')[0];
    // The graph node is how a human opens an agent; an id the graph does not
    // carry (an unknown one, or a failed read) is still addressable by URL.
    if (node) await click(node);
    else await go("#overview?agent=" + encodeURIComponent(id));
    out.views.agents[id] = Object.assign(snapshot(drawer), {
      hidden: drawer.hidden,
      hash: location.hash,
      sidebar: sidebarSnapshot(),
    });
  }
  out.views.agent = out.views.agents[OPEN[0]];

  // The Session page: the drawer's "Open session" link, which opens in another window. It
  // names the agent the same way, so the drawer must stay closed over it.
  out.views.sessions = {};
  for (const id of OPEN) {
    await go("#session?agent=" + encodeURIComponent(id));
    out.views.sessions[id] = Object.assign(snapshot(content), { drawerHidden: drawer.hidden, hash: location.hash });
  }
  out.views.session = out.views.sessions[OPEN[0]];

  // The sidebar's pod entry zooms the graph; there is no pod page.
  await go("#overview?pod=engineers");
  out.views.podFocus = Object.assign(snapshot(content), { graph: graphSnapshot(), sidebar: sidebarSnapshot() });

  // The board card opens the same drawer as the graph node.
  await go("#board");
  const card = content.querySelectorAll(".task-card")[0];
  if (card) {
    await click(card);
    out.views.cardOpens = { hash: location.hash, hidden: drawer.hidden, heading: textOf(drawer.querySelectorAll("h2")[0]) };
  }

  await go("#partner");
  out.views.partner = snapshot(content);

  // The chat box is the page's only write path.
  const box = doc.getElementById("chat-text");
  box.value = "  eng-003 complete: decision; hx read eng-003  ";
  fire(doc, box, "input");
  await click(doc.getElementById("chat-send"));
  out.posted = posted;
  out.chat = {
    status: textOf(doc.getElementById("chat-status")),
    messages: doc.querySelectorAll(".chat-message").map(textOf),
    said: doc.querySelectorAll(".chat-pending").map(textOf),
    cleared: doc.getElementById("chat-text").value === "",
    error: textOf(doc.getElementById("chat-error")),
    toast: doc.getElementById("toast").hidden ? null : textOf(doc.getElementById("toast")),
  };

  // The Partner's own drawer, from the graph root node.
  await go("#overview");
  await click(content.querySelectorAll('[data-agent="partner"]')[0]);
  out.views.agents.partner = Object.assign(snapshot(drawer), { hidden: drawer.hidden, hash: location.hash });
  await go("#session?agent=partner");
  out.views.sessions.partner = Object.assign(snapshot(content), { drawerHidden: drawer.hidden });
  await go("#overview");
  await click(content.querySelectorAll('[data-agent="partner"]')[0]);

  // Escape closes it, as in autodev.
  fire(doc, drawer, "keydown", { key: "Escape" });
  await quiet();
  out.drawerClosed = drawer.hidden;

  out.banner = doc.getElementById("error-banner").hidden ? null : textOf(doc.getElementById("error-banner"));
  out.navigation = doc.querySelectorAll("[data-nav]").map((n) => n.dataset.nav);
  process.stdout.write(JSON.stringify(out, null, 2));
})().catch((error) => {
  process.stderr.write(String(error && error.stack ? error.stack : error));
  process.exit(1);
});
