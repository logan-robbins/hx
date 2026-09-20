/* hx UI (spec 16.2). Vanilla, no framework, no build step.
 *
 * Every view is one GET; the only POST in the page is the Partner wake, which
 * ui-2 wires to the chat box. SSE pushes the scopes whose mtime moved and the
 * page re-fetches the views that show them. */

"use strict";

const BOOT = JSON.parse(document.getElementById("bootstrap").textContent);
const main = document.getElementById("main");
const banner = document.getElementById("banner");
const live = document.getElementById("live");

let view = "board";
let recent = new Set(); // scopes changed by the last SSE frame, flashed once

/* -- plumbing ---------------------------------------------------------- */

async function api(path, options) {
  const init = Object.assign({ headers: {} }, options);
  init.headers = Object.assign({ Authorization: "Bearer " + BOOT.token }, init.headers);
  const response = await fetch(path, init);
  const payload = await response.json().catch(() => ({ error: response.statusText }));
  if (!response.ok) throw new Error(payload.error || "HTTP " + response.status);
  return payload;
}

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child);
  }
  return node;
}

function pill(value, extra) {
  const label = value === null || value === undefined ? "—" : String(value);
  return el("span", { class: "pill " + (extra || (value ? String(value) : "none")), text: label });
}

function fail(message) {
  banner.textContent = message;
  banner.hidden = false;
}

function clearFail() {
  banner.hidden = true;
}

/** ts → the clock part, which is what the human scans for. */
function clock(ts) {
  if (!ts) return "—";
  const match = /T(\d\d:\d\d:\d\d)/.exec(ts);
  return match ? match[1] + "Z" : ts;
}

function errors(list) {
  if (!list || !list.length) return null;
  return el(
    "section",
    { class: "errors" },
    el("h2", { text: "invariant errors (" + list.length + ")" }),
    el("ul", {}, list.map((text) => el("li", { text })))
  );
}

function head(payload) {
  return el("p", {
    class: "meta",
    text: (payload.root_abs || "") + "  ·  read " + clock(payload.ts),
  });
}

/* -- board ------------------------------------------------------------- */

const COLUMNS = [
  "id", "pod / role", "state", "outcome", "after", "subagents",
  "goal", "session", "context", "seams", "turn",
];

function afterCell(item) {
  if (!item.after || !item.after.length) return el("span", { class: "met", text: "—" });
  const text = item.after.join(", ");
  return item.ready
    ? el("span", { class: "met", text: text + " ✓" })
    : el("span", { class: "wait", text: "waits on " + text });
}

function boardRow(item) {
  const row = el(
    "tr",
    { class: [item.id === "partner" ? "partner" : "", recent.has(item.id) ? "flash" : ""].join(" ").trim() },
    el("td", { class: "id" }, item.id, el("div", { class: "sub", text: item.file || "" })),
    el("td", {}, el("span", { class: "sub", text: (item.pod || "—") + " / " + (item.role || "—") })),
    el("td", {}, pill(item.state)),
    el("td", {}, pill(item.outcome)),
    el("td", {}, afterCell(item)),
    el("td", { class: "num", text: item.open_subagents === null ? "—" : String(item.open_subagents) }),
    el(
      "td",
      {},
      el("span", { class: "sub", text: clock(item.goal_ts) }),
      item.goal_pending ? el("div", {}, pill("goal pending", "queued")) : null
    ),
    el("td", {}, el("span", {
      class: "dot" + (item.session_alive ? "" : " off"),
      text: item.session_alive ? "● live" : "● dead",
    })),
    el("td", { class: "num", text: item.context_tokens === null ? "—" : item.context_tokens.toLocaleString() }),
    el("td", { class: "num", text: item.seams === null ? "—" : String(item.seams) }),
    el("td", {}, el("span", { class: "sub", text: clock(item.turn_ts) }))
  );
  return row;
}

function renderBoard(payload) {
  const items = payload.items || [];
  return [
    head(payload),
    errors(payload.errors),
    el(
      "section",
      {},
      el("h2", { text: "board · " + items.length + " items" }),
      el(
        "div",
        { class: "scroll" },
        el(
          "table",
          {},
          el("thead", {}, el("tr", {}, COLUMNS.map((name) => el("th", { text: name })))),
          el("tbody", {}, items.map(boardRow))
        )
      )
    ),
  ];
}

/* -- orders ------------------------------------------------------------ */

function orderCard(order) {
  const record = order.record;
  const children = [
    el(
      "div",
      { class: "order-head" },
      el("span", { class: "name", text: order.id }),
      pill(order.state === null ? "not dispatched" : order.state, order.state || "none"),
      record ? pill(record.outcome) : null,
      order.waiting_on && order.waiting_on.length
        ? el("span", { class: "wait", text: "waits on " + order.waiting_on.join(", ") })
        : null,
      order.file_matches_record === false
        ? el("span", { class: "pill bad", text: "file edited since dispatch" })
        : null
    ),
    el("p", {
      class: "meta",
      text: [
        order.path,
        "after: " + (order.after && order.after.length ? order.after.join(", ") : "—"),
        record ? "dispatched " + clock(record.dispatched) : "never dispatched",
        record && record.completed ? "completed " + clock(record.completed) : null,
      ].filter(Boolean).join("  ·  "),
    }),
    el("pre", { text: order.order || "" }),
  ];
  for (const addendum of order.addenda || []) {
    children.push(
      el("p", { class: "meta", text: "addendum " + clock(addendum.ts) + (addendum.path ? "  ·  " + addendum.path : "") }),
      el("pre", { class: "addendum", text: addendum.text || "" })
    );
  }
  return el("article", { class: "card" + (recent.has(order.id) ? " flash" : "") }, children);
}

function renderOrders(payload) {
  const orders = payload.orders || [];
  const edges = (payload.graph && payload.graph.edges) || [];
  return [
    head(payload),
    errors(payload.errors),
    el(
      "section",
      {},
      el("h2", { text: "after graph · " + edges.length + " edges" }),
      edges.length
        ? el(
            "div",
            { class: "graph" },
            edges.map((edge) =>
              el(
                "div",
                { class: "edge" + (edge.met ? "" : " unmet") },
                el("span", { text: edge.to }),
                el("span", { class: "arrow", text: edge.met ? "── after ──▶" : "── waits on ──▶" }),
                el("span", { text: edge.from }),
                pill(edge.met ? "met" : "unmet", edge.met ? "done" : "queued")
              )
            )
          )
        : el("p", { class: "empty", text: "no dependencies" })
    ),
    el(
      "section",
      {},
      el("h2", { text: "orders · " + orders.length }),
      orders.length ? orders.map(orderCard) : el("p", { class: "empty", text: "no orders" })
    ),
  ];
}

/* -- archive ----------------------------------------------------------- */

function archiveList(title, entries) {
  return el(
    "div",
    {},
    el("p", { class: "meta", text: title + " · " + entries.length }),
    entries.length
      ? entries.map((entry) =>
          el(
            "div",
            {},
            el("p", { class: "meta", text: clock(entry.ts) + "  ·  " + entry.path }),
            el("pre", { text: entry.digest || "(no digest)" })
          )
        )
      : el("p", { class: "empty", text: "none" })
  );
}

function renderArchive(payload) {
  const items = payload.items || [];
  return [
    head(payload),
    errors(payload.errors),
    el(
      "section",
      {},
      el("h2", { text: "archive · " + items.length + " ids" }),
      items.length
        ? items.map((item) =>
            el(
              "article",
              { class: "card" + (recent.has(item.id) ? " flash" : "") },
              el(
                "div",
                { class: "order-head" },
                el("span", { class: "name", text: item.id }),
                el("span", { class: "sub", text: item.pod || "" })
              ),
              archiveList("benched bodies (pods/<pod>/archive/)", item.bench || []),
              archiveList("archived dispatches (archive/<id>/)", item.archive || [])
            )
          )
        : el("p", { class: "empty", text: "nothing archived" })
    ),
  ];
}

/* -- agent / partner shells (ui-2) ------------------------------------- */

function renderSoon(name, what) {
  return [
    el(
      "section",
      {},
      el(
        "article",
        { class: "card soon" },
        el("h2", { text: name }),
        el("p", { text: what }),
        el("p", {
          class: "meta",
          text: "This view lands in ui-2. Its data is already served: " +
            (name === "Agent" ? "GET /api/show/<id>" : "GET /api/show/partner and POST /api/partner/wake") + ".",
        }),
        name === "Partner"
          ? el("p", {
              class: "meta",
              text: "Full control — slash commands, interrupts — stays `tmux attach -t partner`.",
            })
          : null
      )
    ),
  ];
}

/* -- views ------------------------------------------------------------- */

const VIEWS = {
  board: { path: "/api/board", render: renderBoard },
  orders: { path: "/api/orders", render: renderOrders },
  archive: { path: "/api/archive", render: renderArchive },
  agent: {
    render: () =>
      renderSoon(
        "Agent",
        "The work item, step state, the last context file with its seam, stream tails, metrics and the pane capture for one id."
      ),
  },
  partner: {
    render: () => renderSoon("Partner", "PARTNER.md, the board, and a chat box that sends through `hx wake partner`."),
  },
};

async function draw() {
  const spec = VIEWS[view];
  try {
    const payload = spec.path ? await api(spec.path) : {};
    main.replaceChildren(...spec.render(payload).filter(Boolean));
    clearFail();
  } catch (error) {
    fail(view + ": " + error.message);
  }
}

function select(name) {
  view = name;
  for (const button of document.querySelectorAll("#nav button")) {
    button.classList.toggle("on", button.dataset.view === name);
  }
  recent = new Set();
  draw();
}

document.getElementById("nav").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-view]");
  if (button) select(button.dataset.view);
});

/* -- live updates ------------------------------------------------------ */

function listen() {
  const events = new EventSource("/api/events?token=" + encodeURIComponent(BOOT.token));
  events.onopen = () => {
    live.textContent = "live";
    live.className = "live on";
  };
  events.onerror = () => {
    live.textContent = "reconnecting";
    live.className = "live off";
  };
  events.onmessage = (event) => {
    let changed;
    try {
      changed = (JSON.parse(event.data) || {}).changed;
    } catch (error) {
      return;
    }
    if (!changed || !changed.length) return;
    // Every entry is an agent id except the reserved scope `tasks` (CONTRACTS.md),
    // which means the board and the orders view. Re-fetching whichever view is open
    // covers that and every id case, so the scopes are only used to flash what moved.
    recent = new Set(changed);
    draw();
  };
}

select("board");
listen();
