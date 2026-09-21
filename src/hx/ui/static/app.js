/* hx UI (spec 16). autodev's shell — sidebar, topbar, breadcrumb, graph, board,
 * agent table, drawer, toasts — pointed at hx.
 *
 * The model mapping, once: pillars are pods, agents are HarnessAgents with their
 * Companion, ledger tasks are the work item's `## Tasks` and the step state's
 * open and closed steps, `currentTask`/`currentText` is the open step's next
 * action, `phase` is state and outcome, the graph is the whole instance, and
 * activity is the stream tails with their seam markers.
 *
 * The token is never in this file and never in a URL: `GET /` set an HttpOnly
 * cookie, the browser carries it on every request, and this script cannot read
 * it. Live updates are the SSE stream, not a poll. The only write the page can
 * make is `POST /api/partner/wake`; everything else is a GET.
 *
 * Two rendering idioms, deliberately: the shell is autodev's HTML strings with
 * every interpolation through `esc()`, and anything a HarnessAgent wrote — a
 * work item, an order, a digest, a pane, a tool result — is built as DOM nodes
 * with `textContent` and dropped into a `slot()`, so no markup an agent emits
 * can ever reach `innerHTML`. */

"use strict";

const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
const icons = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  board:
    '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 4v16M5.5 8h1M11.5 8h1M17.5 8h1M5.5 12h1M11.5 12h1"/>',
  agents:
    '<circle cx="9" cy="8" r="3"/><path d="M3 20v-3a6 6 0 0112 0v3M16 5a3 3 0 010 6M18 14a5 5 0 013 4v2"/>',
  activity: '<path d="M2 12h5l3-8 4 16 3-8h5"/>',
  refresh:
    '<path d="M20 8a8 8 0 00-14-3L3 8m0-5v5h5M4 16a8 8 0 0014 3l3-3m0 5v-5h-5"/>',
  search: '<circle cx="10" cy="10" r="6.5"/><path d="m15 15 5 5"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  graph:
    '<rect x="9" y="2" width="6" height="5" rx="1"/><rect x="2" y="17" width="6" height="5" rx="1"/><rect x="16" y="17" width="6" height="5" rx="1"/><path d="M12 7v5M5 17v-5h14v5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  terminal:
    '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m6 9 3 3-3 3m6 0h5"/>',
  file: '<path d="M14 2H5v20h14V7Zm0 0v6h5M8 12h8M8 16h6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  inbox: '<path d="M4 4h16l2 11v5H2v-5Zm-2 11h6l2 3h4l2-3h6"/>',
};
const icon = (name) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.grid}</svg>`;
document.querySelectorAll("[data-icon]").forEach((el) => (el.innerHTML = icon(el.dataset.icon)));

/* -- state ------------------------------------------------------------- */

let board = null; // /api/board
let orders = null; // /api/orders
let archive = null; // /api/archive
let fleet = null; // the pod/agent model built from the three above
const details = new Map(); // id -> /api/show/<id>, filled lazily and by SSE
const pending = new Set(); // ids being fetched right now
let partnerShow = null; // /api/show/partner
let query = "",
  zoom = 1,
  boardLimit = 20,
  renderedRoute = "",
  drawerSignature = "",
  selected = null,
  lastSuccess = 0,
  toastTimer;
const sent = []; // what this page has sent the Partner, with the wake's answer
/* `hx archive` walks the whole tree, so it is read when the Archive page is
 * open and something has moved — not on every board change. `InstanceSource`'s
 * sweep has no `archive` scope of its own; `tasks` is what moves when a
 * dispatch is archived. */
let archiveStale = true;

/* -- plumbing ---------------------------------------------------------- */

async function api(path, options) {
  // `same-origin` is what carries the HttpOnly cookie; there is no header to set.
  const response = await fetch(path, Object.assign({ credentials: "same-origin" }, options || {}));
  const payload = await response.json().catch(() => ({ error: response.statusText }));
  if (!response.ok) throw new Error(payload.error || "HTTP " + response.status);
  return payload;
}

function toast(message) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").hidden = false;
  toastTimer = setTimeout(() => ($("toast").hidden = true), 4500);
}

function fail(message) {
  $("error-banner").hidden = false;
  $("error-banner").textContent = message;
}

function clearFail() {
  $("error-banner").hidden = true;
  $("error-banner").textContent = "";
}

/* Anything a HarnessAgent wrote is built as nodes and mounted here, never
 * interpolated into HTML. `slot()` reserves the hole; `paint()` fills it. */
const slots = [];

function slot(node, extra) {
  const key = "s" + slots.length;
  slots.push({ key, node });
  return `<div class="slot ${extra || ""}" data-slot="${key}"></div>`;
}

function paint(target, html) {
  const waiting = slots.splice(0, slots.length);
  target.innerHTML = html;
  for (const { key, node } of waiting) {
    const hole = target.querySelector('[data-slot="' + key + '"]');
    if (hole) hole.replaceChildren(...(Array.isArray(node) ? node.flat(Infinity).filter(Boolean) : [node]));
  }
}

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child);
  }
  return node;
}

/* -- vocabulary -------------------------------------------------------- */

const initials = (value) =>
  String(value ?? "")
    .split(/[-\s]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((v) => v[0].toUpperCase())
    .join("");
const age = (timestamp) => {
  if (!timestamp) return "Not dispatched";
  const n = Math.max(0, (Date.now() - Date.parse(timestamp)) / 1000);
  return n < 60
    ? "just now"
    : n < 3600
      ? `${Math.floor(n / 60)}m ago`
      : n < 86400
        ? `${Math.floor(n / 3600)}h ago`
        : `${Math.floor(n / 86400)}d ago`;
};

function clock(ts) {
  if (!ts) return "—";
  const match = /T(\d\d:\d\d:\d\d)/.exec(String(ts));
  return match ? match[1] + "Z" : String(ts);
}

/** "1 seam", "2 seams" — a count a human reads. */
function count(n, one, many) {
  return n + " " + (n === 1 ? one : many || one + "s");
}

function number(value) {
  return value === null || value === undefined ? "—" : Number(value).toLocaleString();
}

/* `phase` in autodev was the task's stage. In hx it is the work item's state and
 * outcome, which is also the board's column key: `idle`, `working`, and one
 * `complete: <outcome>` per outcome (CONTRACTS.md). */
const COLUMNS = [
  ["idle", "Idle", "muted"],
  ["working", "Working", "green"],
  ["complete:done", "Complete · done", "green"],
  ["complete:decision", "Complete · decision", "blue"],
  ["complete:blocked", "Complete · blocked", "amber"],
  ["complete:exhausted", "Complete · exhausted", "red"],
];

function phase(a) {
  if (a.state !== "complete") return a.state || "unknown";
  return "complete:" + (a.outcome || "no outcome");
}

function phaseLabel(a) {
  return phase(a).replace(":", ": ");
}

/** The badge class for a state/outcome, over autodev's badge palette. */
function phaseClass(a) {
  if (a.state === "working") return "working";
  if (a.state === "idle") return "waiting";
  if (a.state !== "complete") return "unknown";
  return { done: "completed", decision: "publishing", blocked: "interrupted", exhausted: "unknown" }[a.outcome] || "";
}

const badge = (status, label = status) =>
  `<span class="badge ${esc(status)}"><span class="dot"></span>${esc(label)}</span>`;

function phaseBadge(a) {
  return `<span class="badge ${esc(phaseClass(a))}"><span class="dot"></span>${esc(phaseLabel(a))}</span>`;
}

const avatar = (a) =>
  `<span class="avatar ${a.id === "partner" ? "manager" : ""} ${a.session_alive ? "online" : ""}" title="${esc(a.id)}">${esc(initials(a.id))}</span>`;

/** The dot colour: what is happening, then whether the session is even alive. */
function onlineColor(a) {
  if (a.state === "working") return a.session_alive ? "green" : "amber";
  if (a.state === "complete") return a.outcome === "done" ? "green" : a.outcome === "decision" ? "blue" : "amber";
  return a.session_alive ? "blue" : "muted";
}

/* -- the model --------------------------------------------------------- */

/* autodev's fleet was pillars → agents → ledger tasks. hx's is pods →
 * HarnessAgents → the work item, and every agent has exactly one Companion.
 * The board is the whole fleet; `tasks.json` (the Orders view) says which of
 * them the Partner has dispatched, which is where the graph's edges come from. */

function buildFleet() {
  const items = (board && board.items) || [];
  const byPod = new Map();
  for (const item of items) {
    const slug = item.pod || "unassigned";
    if (!byPod.has(slug)) byPod.set(slug, { slug, agents: [] });
    byPod.get(slug).agents.push(agentOf(item));
  }
  const pods = [...byPod.values()].sort((a, b) => a.slug.localeCompare(b.slug));
  for (const pod of pods) {
    pod.agents.sort((a, b) => a.id.localeCompare(b.id));
    pod.online = pod.agents.filter((a) => a.session_alive).length;
    pod.working = pod.agents.filter((a) => a.state === "working").length;
    pod.attention = pod.agents.filter(
      (a) => (a.state === "working" && !a.session_alive) || ["blocked", "exhausted"].includes(a.outcome),
    ).length;
    pod.status = pod.attention ? "attention" : pod.working ? "active" : "ready";
  }
  return {
    root_abs: (board && board.root_abs) || "",
    ts: (board && board.ts) || null,
    name: instanceName((board && board.root_abs) || ""),
    pods,
    agents: pods.flatMap((p) => p.agents),
    partner: {
      id: "partner",
      pod: null,
      role: "partner",
      state: "working",
      outcome: null,
      session_alive: partnerAlive(),
    },
  };
}

function instanceName(rootAbs) {
  const parts = String(rootAbs || "").split("/").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : "hx";
}

function partnerAlive() {
  return Boolean(partnerShow && partnerShow.pane && partnerShow.pane.alive);
}

function agentOf(item) {
  return Object.assign({}, item);
}

/* The Companion is not a board row — every agent has exactly one, in window
 * `<id>:companion` (CONTRACTS.md). What hx can see of it is the step state it
 * has written, so that is read at paint time rather than frozen into the model:
 * the detail cache fills in after the board does. */
function companionStreams(id) {
  return Object.keys((details.get(id) || {}).step_state || {});
}

const allAgents = () => (fleet ? fleet.agents : []);
const findAgent = (id) => allAgents().find((a) => a.id === id) || null;
const orderOf = (id) => ((orders && orders.orders) || []).find((o) => o.id === id) || null;

/** Split a work-item body into its `## ` sections, in order. */
function sections(body) {
  const found = [];
  let current = null;
  for (const line of String(body || "").split("\n")) {
    const heading = /^##\s+(.*)$/.exec(line);
    if (heading) {
      current = { title: heading[1].trim(), lines: [] };
      found.push(current);
    } else if (current) {
      current.lines.push(line);
    }
  }
  return found.map((s) => ({ title: s.title, text: s.lines.join("\n").trim() }));
}

function findSection(body, name) {
  const match = sections(body).find((s) => s.title.toLowerCase() === name.toLowerCase());
  return match ? match.text : null;
}

/** The agent's own `## Tasks` checklist, as `[{done, text}]`. */
function taskList(id) {
  const show = details.get(id);
  const text = findSection(((show || {}).work_item || {}).body, "Tasks");
  const out = [];
  for (const line of String(text || "").split("\n")) {
    const match = /^\s*[-*]\s+\[([ xX])\]\s*(.*)$/.exec(line);
    if (match) out.push({ done: match[1].toLowerCase() === "x", text: match[2].trim() });
  }
  return out;
}

/** Every open step of every stream the Companion has state for, main first. */
function openSteps(id) {
  const state = (details.get(id) || {}).step_state || {};
  const handles = Object.keys(state).sort((a, b) =>
    a.endsWith("-main") ? -1 : b.endsWith("-main") ? 1 : a.localeCompare(b),
  );
  return handles.flatMap((handle) =>
    ((state[handle] || {}).open_steps || []).map((step) => Object.assign({ handle }, step)),
  );
}

/* Goal item 4: every screen says what an agent is doing in one line without
 * opening it — the open step's next action, or the first unchecked `## Tasks`
 * line when the Companion has written no state yet. */
function currentText(a) {
  const steps = openSteps(a.id);
  if (steps.length && (steps[0].next || steps[0].intent)) return steps[0].next || steps[0].intent;
  const open = taskList(a.id).find((t) => !t.done);
  if (open) return open.text;
  const show = details.get(a.id);
  if (!show) return "Reading the work item…";
  if (show.__error) return "Could not be read: " + show.__error;
  if (a.state === "complete") return "Complete · " + (a.outcome || "no outcome recorded");
  if (a.state === "idle") return "Idle · nothing dispatched";
  return "No step recorded yet";
}

/** The order's first line, which is what a board card is titled with. */
function orderTitle(id) {
  const order = orderOf(id);
  const text = (order && order.order) || ((details.get(id) || {}).task || {}).order || "";
  for (const line of String(text).split("\n")) {
    const body = line.trim();
    if (!body || body.startsWith("#") || body.startsWith("```")) continue;
    return body;
  }
  return "No order dispatched";
}

/* -- lazy detail ------------------------------------------------------- *
 * `hx show <id>` is the expensive read — streams, tails, a pane capture — so it
 * is fetched per id and cached, never for the whole fleet on every paint. The
 * SSE frame names the ids that moved; only those are dropped and re-read. */

const DETAIL_CONCURRENCY = 4;

async function loadDetail(id) {
  if (pending.has(id) || pending.size >= DETAIL_CONCURRENCY) return false;
  pending.add(id);
  try {
    const show = await api("/api/show/" + encodeURIComponent(id));
    details.set(id, show);
    return true;
  } catch (error) {
    // A read that fails must not blank the row: remember the failure so the
    // line says so and the id is not asked for again on every paint.
    details.set(id, { id, __error: error.message });
    return true;
  } finally {
    pending.delete(id);
  }
}

/** Fetch what the open view needs, then repaint once the batch lands. */
async function fillDetails(ids) {
  const wanted = ids.filter((id) => id && !details.has(id) && !pending.has(id));
  if (!wanted.length) return;
  let changed = false;
  for (let i = 0; i < wanted.length; i += DETAIL_CONCURRENCY) {
    const batch = wanted.slice(i, i + DETAIL_CONCURRENCY);
    const results = await Promise.all(batch.map(loadDetail));
    changed = changed || results.some(Boolean);
  }
  if (changed) render();
}

/* -- agent-written text, as nodes --------------------------------------
 * Carried over from ui-2..ui-7 unchanged: every renderer below builds DOM
 * with textContent, so a work item, an order, a digest or a tool result
 * cannot inject markup. They are mounted into the shell through slot(). */

function pill(value, extra) {
  const label = value === null || value === undefined ? "—" : String(value);
  return el("span", { class: "pill " + (extra || (value ? String(value) : "none")), text: label });
}

/** Spec: render an absent value as "not yet", never as an empty box. */
function orNotYet(value, render) {
  if (value === null || value === undefined || (Array.isArray(value) && !value.length)) {
    return el("p", { class: "notyet", text: "not yet" });
  }
  return render(value);
}

/* -- markdown ----------------------------------------------------------
 * A deliberately small subset: headings, fenced code, lists, blockquotes,
 * paragraphs, and inline code/bold/italic. Everything becomes a DOM node built
 * with textContent, so nothing a HarnessAgent writes into a work item can
 * inject markup into this page. */

/* Code spans first, so nothing inside backticks is re-read as emphasis. The
 * underscore form is what the context file uses (`_source: …_`, `_none yet_`,
 * handoff/build-to-ui.md build-3); it is deliberately intraword-safe, so
 * `working_set` and `file_matches_record` stay literal. */
const INLINE_SOURCE =
  "(`[^`]+`)|(\\*\\*[^*]+\\*\\*)|(\\*[^*]+\\*)|((?<![\\w\\\\])_(?!\\s)([^_]+?)(?<!\\s)_(?![\\w]))";

function inline(text, into) {
  // A fresh matcher per call: `inline` recurses for `_…_`, and a shared /g regex
  // would have its `lastIndex` clobbered by the inner pass and never terminate.
  const pattern = new RegExp(INLINE_SOURCE, "g");
  let last = 0;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) into.append(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("`")) into.append(el("code", { text: token.slice(1, -1) }));
    else if (token.startsWith("**")) into.append(el("strong", { text: token.slice(2, -2) }));
    else if (token.startsWith("*")) into.append(el("em", { text: token.slice(1, -1) }));
    // `_…_` may wrap a code span, as `_source: \`path\`_` does, so recurse.
    else into.append(inline(token.slice(1, -1), el("em", {})));
    last = match.index + token.length;
  }
  if (last < text.length) into.append(text.slice(last));
  return into;
}

/** The `|---|:--:|` row that makes the line above it a table header. */
const TABLE_RULE = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/;

function tableCells(line) {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split("|").map((cell) => cell.trim());
}

function alignOf(rule) {
  const left = rule.startsWith(":");
  const right = rule.endsWith(":");
  if (left && right) return "center";
  if (right) return "right";
  return null;
}

function markdown(text) {
  const out = [];
  const lines = String(text || "").split("\n");
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];

    if (line.startsWith("```")) {
      const body = [];
      const language = line.slice(3).trim();
      index++;
      while (index < lines.length && !lines[index].startsWith("```")) body.push(lines[index++]);
      index++;
      out.push(el("pre", { class: "code", "data-lang": language || null, text: body.join("\n") }));
      continue;
    }

    // A GFM table: a pipe row, then a row of dashes with optional colons. Both
    // are required, so a lone line with pipes in it stays a paragraph.
    if (line.includes("|") && TABLE_RULE.test(lines[index + 1] || "")) {
      const header = tableCells(line);
      const align = tableCells(lines[index + 1]).map(alignOf);
      index += 2;
      const rows = [];
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        rows.push(tableCells(lines[index++]));
      }
      out.push(
        el(
          "div",
          { class: "scroll" },
          el(
            "table",
            { class: "md" },
            el("thead", {}, el("tr", {}, header.map((cell, n) =>
              inline(cell, el("th", { class: align[n] || null }))
            ))),
            el("tbody", {}, rows.map((row) =>
              el("tr", {}, header.map((_, n) =>
                inline(row[n] === undefined ? "" : row[n], el("td", { class: align[n] || null }))
              ))
            ))
          )
        )
      );
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      out.push(inline(heading[2], el("h" + Math.min(heading[1].length + 2, 6), { class: "md" })));
      index++;
      continue;
    }

    if (/^\s*[-*]\s+/.test(line) || /^\s*\d+\.\s+/.test(line)) {
      const ordered = /^\s*\d+\.\s+/.test(line);
      const items = [];
      while (index < lines.length && (/^\s*[-*]\s+/.test(lines[index]) || /^\s*\d+\.\s+/.test(lines[index]))) {
        const body = lines[index].replace(/^\s*(?:[-*]|\d+\.)\s+/, "");
        const task = /^\[([ xX])\]\s*(.*)$/.exec(body);
        if (task) {
          const done = task[1].toLowerCase() === "x";
          items.push(
            inline(task[2], el("li", { class: "task " + (done ? "done" : "open") }, el("span", {
              class: "box", text: done ? "☑" : "☐",
            }), " "))
          );
        } else {
          items.push(inline(body, el("li", {})));
        }
        index++;
      }
      out.push(el(ordered ? "ol" : "ul", { class: "md" }, items));
      continue;
    }

    if (line.startsWith(">")) {
      const body = [];
      while (index < lines.length && lines[index].startsWith(">")) body.push(lines[index++].replace(/^>\s?/, ""));
      out.push(inline(body.join(" "), el("blockquote", {})));
      continue;
    }

    if (!line.trim()) {
      index++;
      continue;
    }

    const body = [];
    while (index < lines.length && lines[index].trim() && !/^(#{1,6}\s|```|>|\s*[-*]\s|\s*\d+\.\s)/.test(lines[index])) {
      body.push(lines[index++]);
    }
    out.push(inline(body.join(" "), el("p", {})));
  }
  return out;
}

/** Split a work-item body into its `## ` sections, in order. */
/* -- agent ------------------------------------------------------------- */

function frontmatterTable(frontmatter) {
  return el(
    "div",
    { class: "scroll" },
    el(
      "table",
      { class: "kv" },
      el(
        "tbody",
        {},
        Object.entries(frontmatter || {}).map(([key, value]) =>
          el(
            "tr",
            {},
            el("th", { text: key }),
            el("td", { text: value === null || value === undefined ? "not yet" : String(value) })
          )
        )
      )
    )
  );
}

/* Spec 10: the budget is in tokens, estimated at four characters per token —
 * the same estimate `hx.stepstate` evicts on, so the bar shows what hx sees.
 * The budget itself is `companion.state_budget_tokens` from the agent's
 * `harness.json`, which `hx show --json` does not carry; until it does, this is
 * the template default and is labelled as such (handoff/ui-to-build.md). */
const CHARS_PER_TOKEN = 4;
const DEFAULT_STATE_BUDGET = 10000;

function estimateTokens(state) {
  return Math.ceil(JSON.stringify(state).length / CHARS_PER_TOKEN);
}

function budgetBar(state) {
  const used = estimateTokens(state);
  const budget = DEFAULT_STATE_BUDGET;
  const share = Math.min(used / budget, 1);
  const over = used > budget;
  return el(
    "div",
    { class: "budget" },
    el(
      "div",
      { class: "bar" + (over ? " over" : share > 0.8 ? " near" : "") },
      el("span", { class: "fill", style: "width: " + (share * 100).toFixed(1) + "%" })
    ),
    el("span", {
      class: "sub",
      text: used.toLocaleString() + " of " + budget.toLocaleString() +
        " tokens (est.)" + (over ? " — over budget; hx evicts closed detail first" : ""),
    }),
    el("span", { class: "sub dim", text: "budget: the templates/worker default" })
  );
}

function evidence(ev) {
  if (!ev || !ev.length) return null;
  return el("span", { class: "sub ev", text: "ev " + ev.join(", ") });
}

/** Keys a Companion added that spec 07.2 does not name (build-6: entry keys are open). */
function extraKeys(entry, known) {
  const extra = Object.keys(entry).filter((key) => !known.includes(key));
  if (!extra.length) return null;
  return el("div", { class: "sub extra" }, extra.map((key) =>
    el("span", { class: "kv-extra" }, el("code", { text: key }), " " + JSON.stringify(entry[key]))
  ));
}

function stepState(handle, state) {
  const workingSet = state.working_set || {};
  return el(
    "article",
    { class: "card" },
    el(
      "div",
      { class: "order-head" },
      el("span", { class: "name", text: handle }),
      // build-6: hx owns the cursor, `seq = max(model, log head)`, so this is
      // safely read as "the Companion has seen the stream through record N".
      el("span", { class: "sub", text: "caught up through record " + (state.seq ?? "—") }),
      state.prompt_version
        ? el("span", {
            class: "sub",
            text: "prompt " + (state.prompt_version.base || "?") + " / " + (state.prompt_version.role || "?"),
          })
        : null,
      state.ts ? el("span", { class: "sub", text: "written " + clock(state.ts) }) : null
    ),
    budgetBar(state),

    state.goal ? el("p", {}, el("strong", { text: "goal: " }), state.goal) : null,
    (state.constraints || []).length
      ? el("ul", { class: "constraints" }, state.constraints.map((c) => el("li", { text: c })))
      : null,

    el("h3", { text: "decisions" }),
    orNotYet(state.decisions, (list) =>
      el("ul", { class: "steps" }, list.map((decision) =>
        el(
          "li",
          {},
          el("span", { text: decision.d || "" }),
          decision.why ? el("div", { class: "why" }, el("span", { class: "lbl", text: "why: " }), decision.why) : null,
          evidence(decision.ev),
          extraKeys(decision, ["d", "why", "ev"])
        )
      ))
    ),

    el("h3", { text: "open steps" }),
    orNotYet(state.open_steps, (steps) =>
      el("ul", { class: "steps" }, steps.map((step) =>
        el(
          "li",
          {},
          el("span", { class: "stepid", text: step.id }),
          " ",
          el("span", { text: step.intent || "" }),
          el("div", { class: "next" }, el("span", { class: "lbl", text: "next: " }), step.next || "—"),
          evidence(step.ev),
          extraKeys(step, ["id", "intent", "next", "ev"])
        )
      ))
    ),

    el("h3", { text: "closed steps" }),
    orNotYet(state.closed_steps, (steps) =>
      el("ul", { class: "steps" }, steps.map((step) =>
        el(
          "li",
          {},
          el("span", { class: "stepid", text: step.id }),
          " ",
          el("span", { text: step.outcome || "" }),
          " ",
          pill(step.verified ? "verified" : "unverified", step.verified ? "done" : "queued"),
          step.commit ? el("code", { class: "commit", text: step.commit }) : null,
          evidence(step.ev),
          extraKeys(step, ["id", "outcome", "verified", "commit", "ev"])
        )
      ))
    ),

    el("h3", { text: "working set" }),
    el(
      "div",
      { class: "wset" },
      el("p", { class: "meta", text: "commits" }),
      orNotYet(workingSet.commits, (commits) =>
        el("ul", {}, commits.map((c) =>
          typeof c === "string"
            ? el("li", {}, el("code", { class: "commit", text: c.split(" ")[0] }), " " + c.split(" ").slice(1).join(" "))
            : el("li", {}, el("code", { class: "commit", text: c.sha || "" }), " " + (c.msg || ""))
        ))
      ),
      el("p", { class: "meta", text: "dirty" }),
      orNotYet(workingSet.dirty, (dirty) => el("ul", {}, dirty.map((path) => el("li", {}, el("code", { text: path }))))),
      el("p", { class: "meta", text: "files read, not changed" }),
      orNotYet(workingSet.files, (files) =>
        el("ul", {}, files.map((f) =>
          el("li", {}, el("code", { text: f.path || "" }), " — " + (f.note || ""))
        ))
      ),
      workingSet.last_failure ? el("p", {}, el("strong", { text: "last failure: " }), workingSet.last_failure) : null,
      workingSet.hypothesis ? el("p", {}, el("strong", { text: "hypothesis: " }), workingSet.hypothesis) : null
    ),

    el("h3", { text: "blockers" }),
    orNotYet(state.blockers, (list) => el("ul", { class: "bad-list" }, list.map((b) => el("li", { text: b })))),

    el("h3", { text: "dead ends" }),
    orNotYet(state.dead_ends, (list) => el("ul", {}, list.map((d) => el("li", { text: d })))),

    (state.subagents_open || []).length
      ? el("p", { class: "meta", text: "open subagent streams: " + state.subagents_open.join(", ") })
      : null
  );
}

/* Spec 07.4: `reads_of_context_file` must be 1 per seam, and every read of a
 * file already in the working set is waste. Those are the two things the table
 * exists to show, so a seam failing either is marked. */
function seamIsBad(next) {
  if (!next) return false;
  return next.reads_of_context_file !== 1 || next.reads_of_working_set > 0;
}

function seamWhy(next) {
  if (!next) return null;
  const reasons = [];
  if (next.reads_of_context_file === 0) reasons.push("never read its context file");
  else if (next.reads_of_context_file > 1) reasons.push("read the context file " + next.reads_of_context_file + " times");
  if (next.reads_of_working_set > 0) {
    reasons.push("re-read " + next.reads_of_working_set + " working-set file" +
      (next.reads_of_working_set === 1 ? "" : "s"));
  }
  return reasons.length ? reasons.join("; ") : null;
}

const METRICS_COLUMNS = [
  "seq", "ts", "source", "prompt", "ctx tokens before", "context file",
  "working set", "turns", "tool calls", "ctx-file reads", "working-set reads", "other",
];

function metricsRow(seam) {
  const next = seam.next_10_turns || {};
  const bad = seamIsBad(next);
  const why = seamWhy(next);
  return el(
    "tr",
    { class: bad ? "bad-row" : "", title: why || null },
    el("td", { class: "num", text: number(seam.seq) }),
    el("td", {}, el("span", { class: "sub", text: clock(seam.ts) })),
    el("td", {}, pill(seam.source, "none")),
    el("td", {}, el("span", { class: "sub", text: seam.prompt_version || "—" })),
    el("td", { class: "num", text: number(seam.context_tokens_before) }),
    el("td", { class: "num", text: number(seam.context_file_bytes) + " B" }),
    el("td", { class: "num", text: number(seam.working_set_size) }),
    el("td", { class: "num" + (next.turns < 10 ? " short" : ""), text: number(next.turns) }),
    el("td", { class: "num", text: number(next.tool_calls) }),
    el("td", { class: "num" + (next.reads_of_context_file === 1 ? "" : " bad-cell"), text: number(next.reads_of_context_file) }),
    el("td", { class: "num" + (next.reads_of_working_set > 0 ? " bad-cell" : ""), text: number(next.reads_of_working_set) }),
    el("td", { class: "num", text: number(next.other) })
  );
}

function metricsTotals(totals) {
  if (!totals) return null;
  return el(
    "tr",
    { class: "totals" },
    el("td", { text: "totals" }),
    el("td", { text: "" }),
    el("td", { text: (totals.seams === 1 ? "1 seam" : totals.seams + " seams") }),
    el("td", { text: "" }),
    el("td", { text: "" }),
    el("td", { text: "" }),
    el("td", { text: "" }),
    el("td", { text: "" }),
    el("td", { class: "num", text: number(totals.tool_calls) }),
    el("td", { class: "num" + (totals.reads_of_context_file === totals.seams ? "" : " bad-cell"), text: number(totals.reads_of_context_file) }),
    el("td", { class: "num" + (totals.reads_of_working_set > 0 ? " bad-cell" : ""), text: number(totals.reads_of_working_set) }),
    el("td", { class: "num", text: number(totals.other) })
  );
}

function renderMetrics(metrics) {
  const seams = (metrics && metrics.seams) || [];
  if (!seams.length) return el("p", { class: "notyet", text: "not yet" });
  const flagged = seams.filter((seam) => seamIsBad(seam.next_10_turns));
  return el(
    "article",
    { class: "card" },
    el("p", {
      class: "meta",
      text: [
        metrics.stream || "",
        "dispatched " + clock(metrics.dispatched),
        count(seams.length, "seam"),
      ].filter(Boolean).join("  ·  "),
    }),
    flagged.length
      ? el("p", {
          class: "metrics-warn",
          text: flagged.length + " of " + seams.length +
            " seams did not hand over cleanly: every seam should be exactly one read of the " +
            "context file and no re-reads of the working set (spec 07.4).",
        })
      : el("p", { class: "metrics-ok", text: "every seam handed over cleanly." }),
    el(
      "div",
      { class: "scroll" },
      el(
        "table",
        { class: "metrics" },
        el("thead", {}, el("tr", {}, METRICS_COLUMNS.map((name) => el("th", { text: name })))),
        el("tbody", {}, seams.map(metricsRow)),
        el("tfoot", {}, metricsTotals(metrics.totals))
      )
    )
  );
}

/** The metrics entry for a seam record in a stream tail, by `seq`. */
function seamMetrics(metrics, seq) {
  const seams = (metrics && metrics.seams) || [];
  return seams.find((seam) => seam.seq === seq) || null;
}

/* Spec 16.2: "A seam appears as a marker in the stream tail with the context
 * file size and the tool calls of the ten turns that followed." Those counts
 * live in the metrics document, keyed by the seam's `seq`. */
function seamFollowUp(seam) {
  // The tail can outrun the metrics document; say so rather than imply zero.
  if (!seam) return el("span", { class: "followup unknown", text: "next 10 turns: not yet" });
  const next = seam.next_10_turns || {};
  const why = seamWhy(next);
  return el(
    "span",
    { class: "followup" + (seamIsBad(next) ? " bad" : ""), title: why || null },
    el("span", { class: "lbl", text: next.turns === 10 ? "next 10 turns: " : "next " + next.turns + " turns: " }),
    el("span", { text: number(next.tool_calls) + " tool calls" }),
    el("span", { class: "sub", text: " · " + number(next.reads_of_context_file) + " ctx-file" }),
    el("span", { class: "sub", text: " · " + number(next.reads_of_working_set) + " working-set" }),
    el("span", { class: "sub", text: " · " + number(next.other) + " other" })
  );
}

/* The raw-record vocabulary the M4 hooks write (spec 07.1,
 * handoff/build-to-ui.md build-5). Boundary and subagent records are markers —
 * evidence that something happened to the thread, not work the thread did — so
 * they read differently from a tool call. */
const MARKERS = {
  boundary: "decision",
  seam: "decision",
  spawned: "queued",
  closed: "complete",
  close: "complete",
  open: "working",
  subagent_result: "complete",
};

/** Just the file name, when a record carries an absolute path. */
function baseName(path) {
  if (!path) return null;
  const parts = String(path).split("/");
  return parts[parts.length - 1] || String(path);
}

/* `input` and `output` are head excerpts, capped and marked with a trailing
 * ellipsis (build-5). Saying so matters: a reader must not take a truncated
 * tool result for the whole of it. */
function excerpt(value, extra) {
  const text = String(value);
  const cut = text.endsWith("…");
  return el(
    "div",
    { class: "exc" },
    el("pre", { class: "excerpt" + (extra ? " " + extra : ""), text }),
    cut ? el("span", { class: "sub cut", text: "head excerpt — the rest is in the transcript" }) : null
  );
}

/** `ref` points at the full payload in Claude Code's own transcript. */
function recordRef(ref) {
  if (!ref || (!ref.transcript && !ref.tool_use_id)) return null;
  return el(
    "div",
    { class: "sub ref" },
    ref.tool_use_id ? el("code", { text: ref.tool_use_id }) : null,
    ref.transcript ? el("span", { text: " in " + baseName(ref.transcript) }) : null
  );
}

function recordBody(record, metrics) {
  switch (record.event) {
    case "boundary":
      return [
        el("span", { class: "sub", text: "session start · " + (record.source || "—") }),
        record.context_file
          ? el("span", { class: "sub", text: "context file " + baseName(record.context_file) })
          : null,
      ];
    case "seam":
      return [
        el("span", { class: "sub", text: "context file " + (record.context_file_bytes || 0) + " B" }),
        seamFollowUp(seamMetrics(metrics, record.seq)),
      ];
    case "spawned":
      return [
        el("code", { text: record.handle }),
        el("span", { class: "sub", text: "spawned · " + (record.agent_type || "subagent") }),
        el("span", { class: "sub", text: record.agent_id || "" }),
      ];
    case "closed":
      return [
        el("code", { text: record.handle }),
        el("span", { class: "sub", text: "stream closed" }),
        record.digest ? el("span", { class: "sub", text: "digest " + baseName(record.digest) }) : null,
      ];
    case "subagent_result":
      return [
        el("code", { text: record.handle }),
        el("span", {
          class: "sub",
          text: record.digest ? "result returned to the parent" : "returned with no digest",
        }),
      ];
    case "open":
      return [
        el("span", { class: "sub", text: "stream opened · " + (record.agent_type || "subagent") }),
        el("span", { class: "sub", text: record.agent_id || "" }),
      ];
    case "close":
      return [el("span", { class: "sub", text: "stream closed" })];
    default:
      return [
        record.tool ? el("code", { text: record.tool }) : null,
        record.exit !== null && record.exit !== undefined
          ? pill("exit " + record.exit, record.exit === 0 ? "done" : "bad")
          : null,
      ];
  }
}

function recordCard(record, metrics) {
  const marker = MARKERS[record.event];
  return el(
    "div",
    { class: "rec" + (marker ? " marker " + record.event : "") },
    el("span", { class: "seq", text: "#" + record.seq }),
    el("span", { class: "sub", text: clock(record.ts) }),
    pill(record.event, marker || "none"),
    // A tool call hx saw from a subagent it never saw start lands on the main
    // stream with `agent_id` set. That is the deliberate fallback (build-5), so
    // the row says whose call it was rather than looking like the agent's own.
    record.event === "post_tool" && record.agent_id
      ? el("span", { class: "pill queued", text: "from " + record.agent_id })
      : null,
    recordBody(record, metrics),
    record.input ? excerpt(record.input) : null,
    record.output ? excerpt(record.output, "out") : null,
    recordRef(record.ref),
    record.context_tokens === null || record.context_tokens === undefined
      ? null
      : el("div", { class: "sub", text: "context " + record.context_tokens.toLocaleString() })
  );
}

function streamCard(stream, metrics) {
  const subagent = stream.handle && !stream.handle.endsWith("-main");
  return el(
    "article",
    { class: "card" },
    el(
      "div",
      { class: "order-head" },
      el("span", { class: "name", text: stream.handle }),
      pill(stream.open ? "open" : "closed", stream.open ? "working" : "complete"),
      el("span", { class: "sub", text: count(stream.records, "record") + "  ·  " + stream.path })
    ),
    subagent && !stream.open
      ? el(
          "div",
          {},
          el("h3", { text: "digest" }),
          orNotYet(stream.digest, (digest) =>
            el("div", { class: "digest" }, markdown(digest))
          )
        )
      : null,
    el("h3", { text: "tail" }),
    orNotYet(stream.tail, (tail) =>
      el("div", { class: "tail" }, tail.map((record) => recordCard(record, metrics)))
    )
  );
}

/* Spec 16.2's Agent view is per id, and the only way to reach another one used

/** Where the pane text came from, said in words rather than "from the none". */
function paneSource(source) {
  if (source === "session") return "from the session";
  if (source === "log") return "from the log";
  return "no session, no log";
}


/* -- routing ----------------------------------------------------------- */

const TABS = [
  ["overview", "graph", "Graph"],
  ["board", "board", "Task board"],
  ["agents", "agents", "Harness Agents"],
  ["partner", "terminal", "Partner chat"],
];
const PAGE_LABEL = {
  overview: "All Pods",
  board: "Task board",
  agents: "Harness Agents",
  partner: "Partner chat",
  activity: "Activity",
  orders: "Orders",
  archive: "Archive",
};

function route() {
  const [path, search = ""] = location.hash.slice(1).split("?");
  return { parts: (path || "overview").split("/"), params: new URLSearchParams(search) };
}

function openDetails(id) {
  const base = location.hash.split("?")[0] || "#overview";
  const pod = (findAgent(id) || {}).pod;
  location.hash = base + "?" + (pod ? "pod=" + encodeURIComponent(pod) + "&" : "") + "agent=" + encodeURIComponent(id);
}

function closeDetails() {
  location.hash = location.hash.split("?")[0] || "#overview";
}

function matches(text) {
  return String(text).toLowerCase().includes(query.toLowerCase());
}

function empty(title, body, symbol = "inbox") {
  return `<div class="empty">${icon(symbol)}<strong>${esc(title)}</strong>${esc(body)}</div>`;
}

function section(title, subtitle = "", right = "") {
  return `<div class="section-heading"><div><h2>${esc(title)}</h2>${subtitle ? `<p>${esc(subtitle)}</p>` : ""}</div>${right}</div>`;
}

function filters(placeholder = "Search agents…") {
  return `<div class="tools"><label class="search">${icon("search")}<input id="search" aria-label="${esc(placeholder.replace("…", ""))}" placeholder="${esc(placeholder)}" value="${esc(query)}"></label></div>`;
}

function tabs(current) {
  return `<nav class="tabs" aria-label="Instance views">${TABS.map(
    ([key, i, label]) =>
      `<a class="tab ${key === current ? "selected" : ""}" href="#${key}" ${key === current ? 'aria-current="page"' : ""}>${icon(i)}${esc(label)}</a>`,
  ).join("")}</nav>`;
}

/* -- the fleet graph (the home page) ----------------------------------- *
 * Spec 16 and goal ui-8: hx is the graph. One page for the whole instance —
 * the Partner at the root, every HarnessAgent below it as a card clustered by
 * pod, each with its Companion attached, an edge from the Partner to every id
 * it has dispatched (`tasks.json`) and from every agent to its Companion.
 * There are no per-pod pages: the sidebar zooms this graph instead. */

const CARD_W = 202;
const CARD_H = 111;
const COMPANION_H = 38;
const SLOT_W = CARD_W + 28;
const SLOT_H = CARD_H + COMPANION_H + 30;
const CLUSTER_PAD = 16;
const CLUSTER_HEAD = 30;
const CLUSTER_GAP = 24;
const ROW_MAX = 1180;
const PARTNER_TOP = 18;
const PARTNER_GAP = 74;

function graphLayout(pods) {
  const clusters = pods.map((pod) => {
    const cols = Math.min(3, Math.max(1, pod.agents.length));
    const rows = Math.ceil(pod.agents.length / cols) || 1;
    return {
      pod,
      cols,
      width: cols * SLOT_W + CLUSTER_PAD * 2,
      height: rows * SLOT_H + CLUSTER_HEAD + CLUSTER_PAD * 2,
    };
  });
  // Pack the clusters left to right, wrapping at a readable width, and centre
  // each row under the Partner so the whole instance reads as one tree.
  const lines = [];
  let line = [];
  let lineWidth = 0;
  for (const cluster of clusters) {
    if (line.length && lineWidth + CLUSTER_GAP + cluster.width > ROW_MAX) {
      lines.push({ items: line, width: lineWidth });
      line = [];
      lineWidth = 0;
    }
    lineWidth += (line.length ? CLUSTER_GAP : 0) + cluster.width;
    line.push(cluster);
  }
  if (line.length) lines.push({ items: line, width: lineWidth });

  const width = Math.max(700, ...lines.map((l) => l.width), CARD_W + 80);
  let y = PARTNER_TOP + CARD_H + PARTNER_GAP;
  const positions = new Map();
  for (const current of lines) {
    let x = Math.max(0, (width - current.width) / 2);
    let tallest = 0;
    for (const cluster of current.items) {
      cluster.x = x;
      cluster.y = y;
      cluster.pod.agents.forEach((agent, i) => {
        const col = i % cluster.cols;
        const row = Math.floor(i / cluster.cols);
        positions.set(agent.id, {
          x: x + CLUSTER_PAD + col * SLOT_W,
          y: y + CLUSTER_HEAD + CLUSTER_PAD + row * SLOT_H,
        });
      });
      x += cluster.width + CLUSTER_GAP;
      tallest = Math.max(tallest, cluster.height);
    }
    y += tallest + CLUSTER_GAP + 8;
  }
  const partner = { x: Math.max(0, (width - CARD_W) / 2), y: PARTNER_TOP };
  return { clusters, positions, partner, width, height: Math.max(430, y + 10) };
}

function graphNode(a, focusedId) {
  const pos = graphNode.pos;
  const line = currentText(a);
  return `<button class="agent-node ${esc(a.state || "")} ${a.id === focusedId ? "focused" : ""}" style="left:${pos.x}px;top:${pos.y}px" data-agent="${esc(a.id)}" aria-label="Inspect ${esc(a.id)}"><div class="node-top">${avatar(a)}<strong>${esc(a.id)}</strong><span class="dot ${onlineColor(a)}" title="${esc(a.session_alive ? "session alive" : "no session")}"></span></div><div class="node-task" title="${esc(line)}">${esc(line)}</div><div class="node-bottom"><span>${esc(a.pod || "—")} · ${esc(a.role || "—")}</span><span class="stage">${esc(phaseLabel(a))}</span></div></button>`;
}

function companionNode(a) {
  const pos = graphNode.pos;
  const streams = companionStreams(a.id).length;
  return `<div class="companion-node" style="left:${pos.x + 44}px;top:${pos.y + CARD_H + 22}px" title="${esc(a.id)}'s Companion — window ${esc(a.id)}:companion">${icon("clock")}<span>Companion</span><small>${streams ? esc(count(streams, "stream")) : "no state yet"}</small></div>`;
}

function graphPage(focus) {
  if (!fleet) {
    return `<div class="page-heading"><div><div class="eyebrow">The whole instance</div><h1>hx is the graph</h1><p class="subtitle">The Partner at the root, every HarnessAgent below it, clustered by pod.</p></div></div>${tabs("overview")}${empty("Reading the instance…", "hx board has not answered yet.", "graph")}`;
  }
  // No pods yet: the Partner is still the instance, so it is drawn alone at the
  // root rather than behind an empty state (live rehearsal 2026-09-21).
  const onlyPartner = !fleet.pods.length
    ? `<p class="graph-note">No HarnessAgent yet. The Partner creates agents on your instruction, in chat.</p>`
    : "";
  const layout = graphLayout(fleet.pods);
  const dispatched = new Set(
    ((orders && orders.orders) || []).filter((o) => o.dispatched).map((o) => o.id),
  );
  let edges = "";
  const from = { x: layout.partner.x + CARD_W / 2, y: layout.partner.y + CARD_H };
  for (const agent of fleet.agents) {
    const pos = layout.positions.get(agent.id);
    if (!pos) continue;
    const to = { x: pos.x + CARD_W / 2, y: pos.y };
    if (dispatched.has(agent.id)) {
      edges += `<path class="handoff ${agent.state === "working" ? "flowing" : ""}" marker-end="url(#arrowhead)" d="M${from.x} ${from.y} C${from.x} ${from.y + 60},${to.x} ${to.y - 60},${to.x} ${to.y}"><title>Partner dispatched ${esc(agent.id)}</title></path>`;
    } else {
      edges += `<path class="membership" d="M${from.x} ${from.y} C${from.x} ${from.y + 60},${to.x} ${to.y - 60},${to.x} ${to.y}"><title>${esc(agent.id)} has no dispatch in tasks.json</title></path>`;
    }
    edges += `<path class="companion-edge" d="M${pos.x + 60} ${pos.y + CARD_H} L${pos.x + 60} ${pos.y + CARD_H + 22}"><title>${esc(agent.id)} and its Companion</title></path>`;
  }
  const clusterBoxes = layout.clusters
    .map(
      (c) =>
        `<div class="cluster ${focus.pod === c.pod.slug ? "focused" : ""}" style="left:${c.x}px;top:${c.y}px;width:${c.width}px;height:${c.height}px" data-pod="${esc(c.pod.slug)}"><div class="cluster-label"><span class="dot ${c.pod.status === "active" ? "green" : c.pod.status === "attention" ? "amber" : "muted"}"></span>${esc(c.pod.slug)}<span class="cluster-count">${c.pod.agents.length}</span></div></div>`,
    )
    .join("");
  const nodes = fleet.agents
    .map((a) => {
      graphNode.pos = layout.positions.get(a.id);
      return graphNode(a, focus.agent) + companionNode(a);
    })
    .join("");
  graphNode.pos = layout.partner;
  const partner = `<button class="agent-node manager ${focus.agent === "partner" ? "focused" : ""}" style="left:${layout.partner.x}px;top:${layout.partner.y}px" data-agent="partner" aria-label="Open the Partner"><div class="node-top">${avatar(fleet.partner)}<strong>Partner</strong><span class="dot ${partnerAlive() ? "green" : "muted"}"></span></div><div class="node-task">Operates the fleet on your instruction, in chat</div><div class="node-bottom"><span>no work item</span><span class="stage">${esc(count(dispatched.size, "dispatch", "dispatches"))}</span></div></button>`;

  return `<div class="page-heading"><div><div class="eyebrow">The whole instance</div><h1>${esc(fleet.name)} <span class="count">${fleet.agents.length}</span></h1><p class="subtitle">The Partner at the root, every HarnessAgent below it as a card clustered by pod, each with its Companion.</p></div><span class="badge">${esc(count(fleet.pods.length, "pod"))} · ${fleet.agents.filter((a) => a.session_alive).length} / ${fleet.agents.length} sessions alive</span></div>${tabs("overview")}<section class="panel graph-panel"><div class="panel-head"><div><h2>Fleet graph</h2><p>${esc(count(fleet.agents.length, "HarnessAgent"))} · ${esc(count(dispatched.size, "dispatch", "dispatches"))} from the Partner</p></div><div class="graph-toolbar"><span id="zoom-value">${Math.round(zoom * 100)}%</span><button class="icon-button" data-zoom="out" aria-label="Zoom out">${icon("minus")}</button><button class="icon-button" data-zoom="in" aria-label="Zoom in">${icon("plus")}</button><button class="button" data-zoom="fit">Fit</button></div></div><div class="graph-viewport" id="graph-viewport"><div style="width:${layout.width * zoom}px;height:${layout.height * zoom}px"><div class="graph-canvas" style="width:${layout.width}px;height:${layout.height}px;transform:scale(${zoom})"><svg class="graph-edges" viewBox="0 0 ${layout.width} ${layout.height}" aria-label="Dispatch and Companion connections"><defs><marker id="arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" fill="#6cbaa2"/></marker></defs>${edges}</svg>${clusterBoxes}${partner}${nodes}</div></div></div>${onlyPartner}<div class="graph-legend"><span><i class="legend-line handoff"></i>Dispatched by the Partner (tasks.json)</span><span><i class="legend-line"></i>No dispatch on record</span><span><span class="dot green"></span> Working</span><span><span class="dot amber"></span> Needs attention</span><span><span class="dot muted"></span> Idle or no session</span></div></section>`;
}

/* -- task board -------------------------------------------------------- *
 * autodev's columns were Queued / Working / Validating / Blocked. hx's are the
 * work item's state and outcome, and a card is a work item: its agent's avatar,
 * the order's first line, that agent's own `## Tasks` checklist, and the age of
 * the dispatch. No counters — the checklist is what is shown. */

function cardTasks(id) {
  const tasks = taskList(id);
  if (!tasks.length) {
    return `<p class="card-tasks-empty">${esc(details.has(id) ? "No ## Tasks written yet" : "Reading the work item…")}</p>`;
  }
  return `<ul class="card-tasks">${tasks
    .map(
      (t) =>
        `<li class="${t.done ? "done" : "open"}"><span class="box">${t.done ? "☑" : "☐"}</span>${esc(t.text)}</li>`,
    )
    .join("")}</ul>`;
}

function taskCard(a) {
  return `<button class="task-card" data-agent="${esc(a.id)}"><div class="task-card-top"><span>${esc(a.pod || "—")}</span><span>${esc(a.id.toUpperCase())}</span></div><h3>${esc(orderTitle(a.id))}</h3>${cardTasks(a.id)}<p class="task-progress">${esc(currentText(a))}</p>${a.state === "working" && !a.session_alive ? badge("interrupted", "Session gone") : ""}<div class="task-card-bottom">${avatar(a)}<span>${esc(a.id)}</span><span class="time">${esc(age(a.dispatched))}</span></div></button>`;
}

/* build-to-ui, build-7: `state` is whatever the filename suffix says and nothing
 * validates it — a HarnessAgent may rename its own work item. So the six known
 * columns are joined by one per unknown value found, rather than dropping the
 * card off the board. */
function boardColumnsFor(agents) {
  const known = new Set(COLUMNS.map(([key]) => key));
  const extra = [...new Set(agents.map(phase))].filter((key) => !known.has(key)).sort();
  return COLUMNS.concat(extra.map((key) => [key, key.replace(":", " · "), "red"]));
}

function boardColumns(agents) {
  const shown = agents.filter((a) => matches(a.id + " " + a.pod + " " + orderTitle(a.id) + " " + currentText(a)));
  return `<div class="board-wrap"><div class="board" style="grid-template-columns:repeat(${boardColumnsFor(agents).length},minmax(228px,1fr))">${boardColumnsFor(agents).map(([key, label, color]) => {
    const items = shown
      .filter((a) => phase(a) === key)
      .sort((x, y) => String(y.dispatched || "").localeCompare(String(x.dispatched || "")));
    return `<section class="column"><div class="column-head"><span class="dot ${color}"></span>${esc(label)}<span class="number">${items.length}</span></div>${
      items.slice(0, boardLimit).map(taskCard).join("") || '<div class="column-empty">No work items here</div>'
    }${items.length > boardLimit ? `<button class="load-more" data-more>Show more (${items.length - boardLimit} remaining)</button>` : ""}</section>`;
  }).join("")}</div></div>`;
}

function boardPage() {
  const agents = allAgents();
  return `<div class="page-heading"><div><div class="eyebrow">Work in motion</div><h1>Task board</h1><p class="subtitle">One card per work item, in the state its filename carries. Each card shows that agent's own <code class="code-inline">## Tasks</code> list.</p></div><span class="badge">${esc(count(agents.length, "work item"))}</span></div>${tabs("board")}${section("Across all pods", "State and outcome are read from the work item; nothing here judges them.", filters("Search work items…"))}${boardColumns(agents)}`;
}

/* -- the agent table --------------------------------------------------- */

function agentTable(agents) {
  const list = agents.filter((a) => matches(a.id + " " + a.pod + " " + a.role + " " + currentText(a)));
  return `<div class="panel table-wrap"><table class="agent-table"><thead><tr><th>Harness Agent</th><th>Pod</th><th>Role</th><th>State</th><th>Current work</th><th>Session</th><th class="numeric">Seams</th></tr></thead><tbody>${list
    .map(
      (a) =>
        `<tr tabindex="0" role="button" aria-label="Inspect ${esc(a.id)}" data-agent="${esc(a.id)}"><td><div class="agent-identity">${avatar(a)}<span><strong>${esc(a.id)}</strong><small>${esc(a.file || "no work item")}</small></span></div></td><td class="muted">${esc(a.pod || "—")}</td><td class="muted">${esc(a.role || "—")}</td><td>${phaseBadge(a)}</td><td><div class="current-work">${esc(currentText(a))}</div></td><td><span class="dot ${a.session_alive ? "green" : "muted"}"></span> ${a.session_alive ? "alive" : "none"}</td><td class="numeric">${a.seams === null || a.seams === undefined ? "—" : a.seams}</td></tr>`,
    )
    .join("")}</tbody></table>${!list.length ? empty("No matching agents", "Try a different search.") : ""}</div>`;
}

function agentsPage() {
  const agents = allAgents();
  return `<div class="page-heading"><div><div class="eyebrow">The native harness fleet</div><h1>Harness Agents</h1><p class="subtitle">What each agent believes it is doing, from its Companion's open step.</p></div><span class="badge active">${agents.filter((a) => a.session_alive).length} / ${agents.length} sessions alive</span></div>${tabs("agents")}${section("Your fleet", "Select an agent to open its work item, step state, streams and pane.", filters("Search agents…"))}${agentTable(agents)}`;
}

/* -- activity ---------------------------------------------------------- *
 * Spec 16.2: a seam appears as a marker in the stream tail with the context
 * file size and the tool calls of the ten turns that followed. This is that,
 * across every stream the page has read. */

function activityRows() {
  const rows = [];
  for (const agent of allAgents()) {
    const show = details.get(agent.id);
    for (const stream of (show || {}).streams || []) {
      for (const record of stream.tail || []) {
        rows.push({ agent: agent.id, handle: stream.handle, record, metrics: (show || {}).metrics });
      }
    }
  }
  return rows.sort((a, b) => String(b.record.ts || "").localeCompare(String(a.record.ts || "")));
}

function activityRow(row) {
  const r = row.record;
  const seam = r.event === "seam" ? seamMetrics(row.metrics, r.seq) : null;
  const next = (seam || {}).next_10_turns;
  const tone = r.event === "seam" || r.event === "boundary" ? "blue" : r.event === "closed" ? "green" : "";
  const what =
    r.event === "seam"
      ? `context file ${number(r.context_file_bytes)} B · ` +
        (next ? `next ${next.turns} turns: ${number(next.tool_calls)} tool calls, ${number(next.reads_of_context_file)} ctx-file, ${number(next.reads_of_working_set)} working-set` : "next 10 turns: not yet")
      : r.tool || r.handle || r.source || r.event;
  return `<button class="event-row" data-agent="${esc(row.agent)}" style="width:100%;text-align:left;background:none;border-left:0;border-right:0;border-top:0;color:inherit"><span class="event-dot ${tone}"><span class="dot"></span></span><span class="event-copy"><strong>${esc(r.event)}${r.event === "seam" ? " · seam" : ""}</strong><p>${esc(row.agent)} · ${esc(row.handle)} · #${esc(r.seq)} · ${esc(what)}</p></span><time title="${esc(r.ts || "")}">${esc(clock(r.ts))}</time></button>`;
}

function activityPage() {
  const rows = activityRows().filter((row) => matches(row.agent + " " + row.handle + " " + row.record.event));
  return `<div class="page-heading"><div><div class="eyebrow">Observed stream records</div><h1>Activity</h1><p class="subtitle">The tail of every stream the page has read, newest first, with each seam's follow-up.</p></div></div>${section("Latest records", "Seams are marked with the context file size and the turns that followed.", filters("Search activity…"))}<section class="panel">${
    rows.length
      ? rows.slice(0, 100).map(activityRow).join("")
      : empty("Nothing on a stream yet", "Records appear as hooks write them under logs/<id>/.", "activity")
  }</section>`;
}

/* -- orders and archive ------------------------------------------------ *
 * hx-only views with no autodev counterpart (spec 16.2 lists both). They keep
 * the autodev shell — page heading, panels, cards — and their bodies are the
 * markdown renderers, mounted through slot(). */

function orderPanel(order) {
  const meta = [
    order.dispatched ? "dispatched " + clock(order.dispatched) : "not dispatched",
    order.completed ? "completed " + clock(order.completed) : null,
    (order.addenda || []).length ? count(order.addenda.length, "addendum", "addenda") : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const addenda = (order.addenda || [])
    .map((a) => `<div class="small-label">addendum ${esc(clock(a.ts))}</div>${slot(markdown(a.text || ""), "addendum-block")}`)
    .join("");
  return `<section class="panel"><div class="panel-body"><div class="panel-head"><div><h2>${esc(order.id)}</h2><p>${esc(order.pod || "")} · ${esc(meta)}</p></div>${phaseBadge(order)}<button class="button" data-agent="${esc(order.id)}">${icon("arrow")}Open agent</button></div><div class="divider"></div>${slot(markdown(order.order || ""))}${addenda}</div></section>`;
}

function ordersPage() {
  const list = ((orders && orders.orders) || []).filter((o) => matches(o.id + " " + (o.order || "")));
  return `<div class="page-heading"><div><div class="eyebrow">tasks.json</div><h1>Orders</h1><p class="subtitle">The order text and every addendum, per id. Order files are consumed at dispatch, so this is the whole source.</p></div><span class="badge">${esc(count(list.length, "order"))}</span></div>${section("Dispatched work", "", filters("Search orders…"))}${
    list.length ? list.map(orderPanel).join("") : empty("Nothing dispatched yet", "The Partner dispatches with hx dispatch <id> <order-file>.", "file")
  }`;
}

function archiveList(title, entries) {
  return `<div class="small-label">${esc(title)} · ${entries.length}</div>${
    entries.length
      ? entries
          .map(
            (e) =>
              `<code class="file-path">${esc(clock(e.ts))} · ${esc(e.path)}</code>${slot(markdown(e.digest || "_no digest_"), "digest")}`,
          )
          .join("")
      : '<p class="muted">none</p>'
  }`;
}

function archivePage() {
  const items = ((archive && archive.items) || []).filter((i) => matches(i.id));
  return `<div class="page-heading"><div><div class="eyebrow">Benched and archived</div><h1>Archive</h1><p class="subtitle">Benched work-item bodies and archived dispatches, with their digests.</p></div><span class="badge">${esc(count(items.length, "id"))}</span></div>${section("Per agent", "", filters("Search archive…"))}${
    items.length
      ? items
          .map(
            (item) =>
              `<section class="panel"><div class="panel-body"><div class="panel-head"><div><h2>${esc(item.id)}</h2><p>${esc(item.pod || "")}</p></div><button class="button" data-agent="${esc(item.id)}">${icon("arrow")}Open agent</button></div><div class="divider"></div>${archiveList("benched bodies (pods/<pod>/archive/)", item.bench || [])}<div class="divider"></div>${archiveList("archived dispatches (archive/<id>/)", item.archive || [])}</div></section>`,
          )
          .join("")
      : empty("Nothing archived yet", "hx bench and hx archive write here.", "inbox")
  }`;
}

/* -- the Partner ------------------------------------------------------- *
 * autodev's GM chat was a mailbox with a delivery thread and a reply CLI. hx
 * has neither: a message goes out through `hx wake partner`, and the replies
 * are the Partner's own pane. Full control stays `tmux attach -t partner`. */

const WAKE_SAID = {
  accepted: "Delivered. The reply appears in the pane below.",
  "no-socket":
    "Not delivered: the Partner has no messaging socket yet — it has not started a session. Check tmux attach -t partner.",
  refused:
    "Not delivered: the Partner's socket refused the connection — it may be stale. hx restart partner rewrites it at the next SessionStart.",
};

function chatMessages() {
  if (!sent.length) {
    return '<div class="chat-empty">Ask the Partner for the fleet\'s state, or give it the next goal. It dispatches; this page does not.</div>';
  }
  return sent
    .map(
      (m) =>
        `<article class="chat-message user"><div class="chat-byline">You <time>${esc(clock(m.at))}</time></div><div class="chat-text">${esc(m.text)}</div></article><div class="chat-pending" role="status">${esc(m.said)}</div>`,
    )
    .join("");
}

function partnerPage() {
  const show = partnerShow || {};
  const pane = show.pane || {};
  const alive = Boolean(pane.alive);
  const paneBlock = `<section class="panel"><div class="panel-head"><div><h2>Partner pane</h2><p>${esc(count((pane.lines || []).length, "line"))} · ${esc(paneSource(pane.source))} · the Partner's replies</p></div><span class="badge ${alive ? "completed" : "unknown"}"><span class="dot"></span>${alive ? "session alive" : "no session"}</span></div><div class="panel-body">${slot(el("pre", { class: "output", text: (pane.lines || []).join("\n") || "No pane capture yet." }))}</div></section>`;
  const partnerMd = `<section class="panel"><div class="panel-head"><div><h2>PARTNER.md</h2><p>What the Partner reads at every SessionStart.</p></div></div><div class="panel-body">${slot(markdown(show.partner_md || "_not yet_"))}</div></section>`;
  // Spec 16.2 puts the board on the Partner view: it is what the human and the
  // Partner are talking about. It is the same table the Agents tab draws.
  const board = `${section("The fleet", "Every HarnessAgent, with the open step each one is on.")}${agentTable(allAgents())}`;
  return `<div class="page-heading"><div><div class="eyebrow">The fleet's own agent</div><h1>Partner chat</h1><p class="subtitle">The Partner has no work item and never dispatches itself. You give it its goal here, or in its pane.</p></div><span class="badge ${alive ? "completed" : "unknown"}"><span class="dot"></span>${alive ? "session alive" : "no session"}</span></div>${tabs("partner")}<section class="panel gm-chat"><header class="chat-heading">${avatar({ id: "partner", session_alive: alive })}<div><h2>Partner</h2><span>hx wake partner · Claude Code in tmux session partner</span></div><span id="chat-status" role="status">${alive ? "Online" : "Offline · the message waits for a session"}</span></header><div id="chat-messages" class="chat-messages" role="log" aria-label="Messages sent to the Partner" aria-live="polite">${chatMessages()}</div><form id="chat-form" class="chat-composer"><label class="sr-only" for="chat-text">Message the Partner</label><textarea id="chat-text" rows="3" maxlength="16000" placeholder="Message the Partner. This goes through hx wake partner."></textarea><div class="chat-compose-actions"><label>An idle Partner starts a turn; a busy one takes it as steering in the current turn.</label><button class="button primary" id="chat-send" type="button">Send</button></div><div id="chat-error" role="alert"></div><small>Full control — slash commands, interrupts — stays <code class="code-inline">tmux attach -t partner</code>. This page observes and sends; it does not operate the fleet.</small></form></section>${partnerMd}${board}${paneBlock}`;
}

function bindChat() {
  const form = $("chat-form");
  if (!form) return;
  const box = $("chat-text");
  box.value = chatDraft;
  box.addEventListener("input", () => (chatDraft = box.value));
  box.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      submitChat();
    }
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submitChat();
  });
  // The button is `type="button"`, so this is the only path a click takes:
  // nothing here ever submits a form, and nothing double-sends.
  $("chat-send").addEventListener("click", submitChat);
}

let chatDraft = "";

async function submitChat() {
  const box = $("chat-text");
  const send = $("chat-send");
  const text = (box.value || "").trim();
  if (!text || (send && send.disabled)) return;
  if (send) send.disabled = true;
  $("chat-error").textContent = "";
  try {
    const result = await api("/api/partner/wake", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const said = WAKE_SAID[result.status] || (result.delivered ? WAKE_SAID.accepted : "Not delivered.");
    sent.push({ text, at: new Date().toISOString(), said });
    if (result.delivered) {
      box.value = "";
      chatDraft = "";
    }
    toast(said);
    drawerSignature = "";
    await refresh();
  } catch (error) {
    $("chat-error").textContent = error.message;
  } finally {
    if (send) send.disabled = false;
  }
}

/* -- the drawer: the whole Agent page (spec 16.2) ---------------------- */

/* The stop hook's marker (CONTRACTS.md); the board's `turn_ts` is the same
 * instant, and is the fallback until `hx show --json` carries `turn`. */
function turnNote(turn, row) {
  const ts = (turn && turn.ts) || (row && row.turn_ts);
  return ts ? "last turn " + clock(ts) : "no turn yet";
}

function executionFlow(a) {
  const steps = [
    ["idle", "Idle"],
    ["working", "Working"],
    ["complete", "Complete" + (a.outcome ? " · " + a.outcome : "")],
  ];
  const order = ["idle", "working", "complete"];
  const at = order.indexOf(a.state);
  return `<div class="execution-flow" aria-label="Work item state">${steps
    .map(([key, label], i) => {
      const reached = at >= 0 && i <= at;
      return `<div class="flow-step ${reached ? "reached" : ""} ${a.state === key ? "current" : ""}"><i>${key === "complete" && a.state === "complete" ? "✓" : i + 1}</i>${esc(label)}</div>`;
    })
    .join("")}</div>`;
}

function paneBlock(pane, id) {
  return `<div class="small-label">Pane · ${esc((pane && pane.session) || id)} · ${esc(count(((pane || {}).lines || []).length, "line"))} · ${esc(paneSource((pane || {}).source))}</div>${
    (pane || {}).error ? `<div class="alert-note">${esc(pane.error)}</div>` : ""
  }${slot(el("pre", { class: "output", text: ((pane || {}).lines || []).join("\n") || "No pane capture yet." }))}`;
}

function partnerDrawer() {
  const show = partnerShow || {};
  const pane = show.pane || {};
  return (
    `<div class="drawer-head"><a href="#partner">Partner chat</a><span class="slash">/</span><span>partner</span><button class="icon-button" id="close-drawer" aria-label="Close details">${icon("close")}</button></div>` +
    `<div class="drawer-title">${avatar({ id: "partner", session_alive: pane.alive })}<div><h2>Partner</h2><p>no work item · no task · no step state</p></div><span style="margin-left:auto">${badge(pane.alive ? "completed" : "unknown", pane.alive ? "session alive" : "no session")}</span></div>` +
    `<div class="drawer-actions"><a class="button primary" href="#partner">${icon("terminal")}Open the chat</a></div>` +
    `<div class="small-label">PARTNER.md</div>${slot(markdown(show.partner_md || "_not yet_"))}` +
    `<div class="divider"></div><div class="small-label">Streams</div>${slot((show.streams || []).map((s) => streamCard(s, null)))}` +
    `<div class="divider"></div>${paneBlock(pane, "partner")}`
  );
}

function agentDrawer(a) {
  const show = details.get(a.id) || {};
  const body = (show.work_item || {}).body || "";
  const tasks = taskList(a.id);
  const steps = openSteps(a.id);
  const stepStates = show.step_state || {};
  const subagents = show.subagents || {};
  const contextFile = show.context_file;
  const turn = show.turn;
  const background = ((turn || {}).background_tasks || []).length
    ? `<div class="alert-note">Stopped with work still running — ${esc(count(turn.background_tasks.length, "background task"))}: ${esc(turn.background_tasks.join(", "))}</div>`
    : "";

  const workItem =
    `<div class="small-label">Frontmatter</div>${slot(frontmatterTable((show.work_item || {}).frontmatter))}` +
    `<div class="small-label">Order</div>${slot(markdown((show.task || {}).order || "_not yet_"))}` +
    (((show.task || {}).addenda || []).length
      ? `<div class="small-label">Addenda</div>` +
        show.task.addenda
          .map((x) => `<div class="small-label">${esc(clock(x.ts))}</div>${slot(markdown(x.text || ""), "addendum-block")}`)
          .join("")
      : "") +
    ["Tasks", "Deliverables", "Commands", "Open decision", "Digest"]
      .map((name) => `<div class="small-label">${esc(name)}</div>${slot(markdown(findSection(body, name) || "_not yet_"))}`)
      .join("");

  return (
    `<div class="drawer-head"><a href="#overview?pod=${encodeURIComponent(a.pod || "")}">${esc(a.pod || "fleet")}</a><span class="slash">/</span><span>${esc(a.id)}</span><button class="icon-button" id="close-drawer" aria-label="Close details">${icon("close")}</button></div>` +
    `<div class="drawer-title">${avatar(a)}<div><h2>${esc(a.id)}</h2><p>${esc(a.role || "—")} · ${esc(a.pod || "—")} · ${esc(a.file || "no work item")}</p></div><span style="margin-left:auto">${phaseBadge(a)}</span></div>` +
    `<p class="drawer-meta">${esc(a.file || "no work item")} · persona ${esc(show.persona_path || "—")} · ${esc(turnNote(turn, a))}</p>` +
    `<div class="drawer-actions"><span class="badge">${a.session_alive ? "session alive" : "no session"}</span><span class="badge">${esc(count(a.seams === null || a.seams === undefined ? 0 : a.seams, "seam"))}</span><span class="badge">${esc(count(a.open_subagents || 0, "open subagent"))}</span></div>` +
    background +
    (show.__error
      ? `<div class="alert-note">${esc(a.id)} could not be read: ${esc(show.__error)}. The board still lists this id, so this is a read failure rather than a missing agent.</div>`
      : "") +
    // Every board field that has no other home on this page (CONTRACTS.md):
    // the ones the old board table carried in its columns.
    `<p class="drawer-meta">goal sent ${esc(clock(a.goal_ts))} · dispatched ${esc(clock(a.dispatched))} · completed ${esc(clock(a.completed))}</p>` +
    `<div class="detail-stats"><div><strong>${steps.length}</strong>Open steps</div><div><strong>${tasks.filter((t) => t.done).length}/${tasks.length}</strong>Tasks checked</div><div><strong>${esc(number(a.context_tokens))}</strong>Context tokens</div></div>` +
    `<section class="drawer-task"><div class="small-label">Current work · the open step's next action</div>${phaseBadge(a)}<h3>${esc(currentText(a))}</h3>${executionFlow(a)}` +
    `<div class="small-label">Dispatched</div><p class="task-instructions">${esc(a.dispatched ? a.dispatched + " (" + age(a.dispatched) + ")" : "not dispatched")}</p>` +
    `<div class="divider"></div><div class="small-label">Work item</div>${workItem}` +
    `<div class="divider"></div><div class="small-label">Step state</div>${
      Object.keys(stepStates).length
        ? slot(Object.entries(stepStates).map(([handle, state]) => stepState(handle, state)))
        : '<p class="notyet">not yet</p>'
    }` +
    `<div class="divider"></div><div class="small-label">Context file</div>${
      contextFile
        ? `<code class="file-path">${esc(contextFile.path)} · seam ${esc(clock(contextFile.seam_ts))}</code>${slot(markdown(contextFile.text || "_not composed yet_"), "context")}`
        : '<p class="notyet">not yet</p>'
    }` +
    `<div class="divider"></div><div class="small-label">Streams</div>${slot((show.streams || []).map((s) => streamCard(s, show.metrics)))}` +
    `<div class="divider"></div><div class="small-label">Subagents</div>${
      Object.keys(subagents).length
        ? slot(subagentTable(show))
        : '<p class="notyet">not yet</p>'
    }` +
    `<div class="divider"></div><div class="small-label">Metrics</div>${slot(renderMetrics(show.metrics))}` +
    `<div class="divider"></div>${paneBlock(show.pane, a.id)}` +
    `<div class="small-label">Persona</div><code class="file-path">${esc(show.persona_path || "—")}</code>` +
    `</section>`
  );
}

/** `subagents.json` is {claude agent_id: sNNN}; the stream is the handle ending in it. */
function subagentTable(show) {
  return el(
    "div",
    { class: "scroll" },
    el(
      "table",
      { class: "kv" },
      el("thead", {}, el("tr", {}, [
        el("th", { text: "handle" }),
        el("th", { text: "claude agent_id" }),
        el("th", { text: "stream" }),
        el("th", { text: "digest" }),
      ])),
      el(
        "tbody",
        {},
        Object.entries(show.subagents || {}).map(([claudeId, handle]) => {
          const stream = (show.streams || []).find((s) => s.handle === show.id + "-" + handle);
          return el(
            "tr",
            {},
            el("td", {}, el("code", { text: handle })),
            el("td", {}, el("code", { text: claudeId })),
            el(
              "td",
              {},
              stream ? pill(stream.open ? "open" : "closed", stream.open ? "working" : "complete") : null,
              el("span", { class: "sub", text: stream ? "  " + count(stream.records, "record") : "no stream" }),
            ),
            el(
              "td",
              {},
              stream && stream.digest
                ? el("div", { class: "digest" }, markdown(stream.digest))
                : el("span", { class: "notyet", text: "not yet" }),
            ),
          );
        }),
      ),
    ),
  );
}

/* ui-7's finding, kept: an id the board no longer lists must not be a dead end.
 * The Orders view still reaches it — `tasks.json` outlives the work item — so
 * the drawer says which kind of absence this is and offers the ids that exist. */
function unknownDrawer(id) {
  return (
    `<div class="drawer-head"><a href="#overview">fleet</a><span class="slash">/</span><span>${esc(id)}</span><button class="icon-button" id="close-drawer" aria-label="Close details">${icon("close")}</button></div>` +
    `<div class="drawer-title">${avatar({ id, session_alive: false })}<div><h2>${esc(id)}</h2><p>not on the board</p></div></div>` +
    `<div class="alert-note">${esc(id)} could not be read. The board does not list this id, so its work item is gone; <code class="code-inline">tasks.json</code> still records the order.</div>` +
    `<div class="small-label">The ids the board does list</div><div class="drawer-actions">${allAgents()
      .map((other) => `<button class="button" data-agent="${esc(other.id)}">${esc(other.id)}</button>`)
      .join("")}</div>`
  );
}

function renderDrawer() {
  const r = route();
  const id = r.params.get("agent");
  const drawer = $("drawer");
  if (!id) {
    if (!drawer.hidden) {
      drawer.hidden = true;
      $("drawer-backdrop").hidden = true;
    }
    selected = null;
    drawerSignature = "";
    return;
  }
  const a = id === "partner" ? null : findAgent(id);
  const signature = JSON.stringify([id, a, details.get(id) || null, partnerShow && partnerShow.pane]);
  if (signature === drawerSignature) return;
  const scroll = drawer.scrollTop;
  const wasOpen = !drawer.hidden;
  drawerSignature = signature;
  selected = id;
  drawer.hidden = false;
  $("drawer-backdrop").hidden = false;
  paint(drawer, id === "partner" ? partnerDrawer() : a ? agentDrawer(a) : unknownDrawer(id));
  drawer.scrollTop = scroll;
  if (!wasOpen && $("close-drawer")) $("close-drawer").focus();
}

/* -- the shell --------------------------------------------------------- */

function sidebar(focusPod, focusAgent) {
  const nav = $("pod-nav");
  const scroll = nav.scrollTop;
  nav.innerHTML = (fleet ? fleet.pods : [])
    .map((pod) => {
      const expanded = focusPod === pod.slug || pod.agents.some((a) => a.id === focusAgent);
      return `<div class="pillar-branch"><a class="pillar-item ${expanded ? "selected" : ""}" href="#overview?pod=${encodeURIComponent(pod.slug)}" ${expanded ? 'aria-current="true"' : ""}><span class="branch-chevron" aria-hidden="true">${expanded ? "⌄" : "›"}</span><span>${esc(pod.slug)}</span><span class="dot ${pod.status === "active" ? "green" : pod.status === "attention" ? "amber" : "muted"}" title="${esc(pod.status)}"></span></a>${
        expanded
          ? `<div class="agent-children" aria-label="${esc(pod.slug)} HarnessAgents">${pod.agents
              .map(
                (a) =>
                  `<a class="agent-child ${focusAgent === a.id ? "selected" : ""}" href="#overview?pod=${encodeURIComponent(pod.slug)}&agent=${encodeURIComponent(a.id)}" ${focusAgent === a.id ? 'aria-current="page"' : ""}><span class="dot ${onlineColor(a)}" title="${esc(phaseLabel(a))}"></span><span>${esc(a.id)}</span></a>`,
              )
              .join("")}</div>`
          : ""
      }</div>`;
    })
    .join("");
  nav.scrollTop = scroll;
}

function breadcrumb(parts, focusPod, focusAgent) {
  const name = fleet ? fleet.name : "hx";
  const trail = [`<a href="#overview">${esc(name)}</a>`];
  if (focusPod) trail.push(`<span class="slash">/</span><a href="#overview?pod=${encodeURIComponent(focusPod)}">${esc(focusPod)}</a>`);
  if (focusAgent) trail.push(`<span class="slash">/</span><strong aria-current="page">${esc(focusAgent)}</strong>`);
  if (!focusPod && !focusAgent) {
    trail.push(`<span class="slash">/</span><strong aria-current="page">${esc(PAGE_LABEL[parts[0]] || "All Pods")}</strong>`);
  }
  $("breadcrumb").innerHTML = trail.join("");
}

function connection(ok) {
  const el = $("connection");
  el.className = `connection ${ok ? "live" : "stale"}`;
  el.innerHTML = `<span class="dot"></span>${ok ? "Live · server-sent events" : "Disconnected · retrying"}`;
}

const PAGES = {
  overview: graphPage,
  board: boardPage,
  agents: agentsPage,
  partner: partnerPage,
  activity: activityPage,
  orders: ordersPage,
  archive: archivePage,
};

function render() {
  if (!fleet) return;
  const r = route();
  const parts = r.parts;
  const key = parts.join("/");
  const changed = key !== renderedRoute;
  if (changed) {
    query = "";
    zoom = 1;
    boardLimit = 20;
    renderedRoute = key;
  }
  const focusAgent = r.params.get("agent");
  const focusPod = r.params.get("pod") || (focusAgent ? (findAgent(focusAgent) || {}).pod : null) || null;

  $("workspace-name").textContent = fleet.name;
  sidebar(focusPod, focusAgent);
  breadcrumb(parts, focusPod, focusAgent);
  document.querySelectorAll("[data-nav]").forEach((el) => el.classList.toggle("selected", el.dataset.nav === parts[0]));
  $("footer-info").textContent = fleet.root_abs
    ? `Observing ${fleet.root_abs} · read ${clock(fleet.ts)}`
    : "Observing HARNESS_ROOT";

  const graphScroll = $("graph-viewport") ? [$("graph-viewport").scrollLeft, $("graph-viewport").scrollTop] : null;
  // An SSE frame repaints whatever is open, which would otherwise take the
  // caret out of the search box or the Partner's message mid-sentence.
  const focused = document.activeElement && document.activeElement.id;
  const caret = focused && $(focused) ? $(focused).selectionStart : null;
  const page = PAGES[parts[0]] || graphPage;

  paint($("content"), page({ pod: focusPod, agent: focusAgent }));
  if (parts[0] === "partner") bindChat();
  if (graphScroll && $("graph-viewport")) {
    $("graph-viewport").scrollLeft = graphScroll[0];
    $("graph-viewport").scrollTop = graphScroll[1];
  }
  // The sidebar's pod and agent entries zoom the home graph to that cluster or
  // node; there are no pod pages to go to (goal ui-8).
  if (parts[0] === "overview" && (focusPod || focusAgent) && $("graph-viewport")) {
    const target =
      $("graph-viewport").querySelector(focusAgent ? '[data-agent="' + focusAgent + '"]' : '[data-pod="' + focusPod + '"]');
    if (target) {
      const viewport = $("graph-viewport");
      viewport.scrollLeft = Math.max(0, target.offsetLeft * zoom - viewport.clientWidth / 2 + CARD_W / 2);
      viewport.scrollTop = Math.max(0, target.offsetTop * zoom - viewport.clientHeight / 3);
    }
  }
  if ((focused === "search" || focused === "chat-text") && $(focused)) {
    $(focused).focus();
    if (caret !== null) $(focused).setSelectionRange(caret, caret);
  }
  renderDrawer();

  if (parts[0] === "archive" && archiveStale) refreshArchive().then(() => render());
  // Whatever the open screen needs and has not been read yet; `fillDetails`
  // repaints once the batch lands.
  const wanted =
    parts[0] === "overview" || parts[0] === "board" || parts[0] === "agents" || parts[0] === "activity"
      ? allAgents().map((a) => a.id)
      : [];
  if (focusAgent && focusAgent !== "partner") wanted.push(focusAgent);
  if (wanted.length) fillDetails(wanted);
}

/* -- reading ----------------------------------------------------------- */

let fetching = false;

async function refresh() {
  if (fetching) return;
  fetching = true;
  try {
    const [nextBoard, nextOrders] = await Promise.all([api("/api/board"), api("/api/orders").catch(() => orders)]);
    board = nextBoard;
    orders = nextOrders;
    fleet = buildFleet();
    lastSuccess = Date.now();
    clearFail();
    connection(true);
    render();
  } catch (error) {
    fail(
      `Live observation interrupted. Showing the last successful read${lastSuccess ? " from " + new Date(lastSuccess).toLocaleTimeString() : ""}. ${error.message}`,
    );
    connection(false);
  } finally {
    fetching = false;
  }
}

async function refreshPartner() {
  try {
    partnerShow = await api("/api/show/partner");
  } catch (error) {
    partnerShow = { id: "partner", __error: error.message, pane: { alive: false, lines: [] } };
  }
}

async function refreshArchive() {
  archiveStale = false;
  try {
    archive = await api("/api/archive");
  } catch (error) {
    archive = { items: [], __error: error.message };
  }
}

/* -- events ------------------------------------------------------------ */

document.addEventListener("click", (event) => {
  const target = event.target.closest("button,a,[data-agent]");
  if (!target) return;
  if (target.dataset.agent) {
    openDetails(target.dataset.agent);
    return;
  }
  if (target.id === "close-drawer") {
    closeDetails();
    return;
  }
  if (target.dataset.zoom) {
    const canvas = document.querySelector(".graph-canvas");
    const viewport = $("graph-viewport");
    zoom =
      target.dataset.zoom === "fit"
        ? Math.min(
            1,
            (viewport.clientWidth - 20) / canvas.offsetWidth,
            (viewport.clientHeight - 20) / canvas.offsetHeight,
          )
        : Math.min(1.4, Math.max(0.15, zoom + (target.dataset.zoom === "in" ? 0.1 : -0.1)));
    render();
    return;
  }
  if (target.hasAttribute("data-more")) {
    boardLimit += 20;
    render();
    return;
  }
  if (target.id === "refresh") refresh();
});

document.addEventListener("input", (event) => {
  if (event.target.id === "search") {
    query = event.target.value;
    render();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("drawer").hidden) {
    closeDetails();
    return;
  }
  if (event.key === "Enter" && event.target.matches && event.target.matches("tr[data-agent]")) {
    openDetails(event.target.dataset.agent);
  }
});

$("drawer-backdrop").addEventListener("click", closeDetails);
window.addEventListener("hashchange", () => render());

/* -- live updates ------------------------------------------------------ *
 * SSE replaces autodev's 2 s poll. Every entry in `changed` is an id except the
 * reserved scope `tasks` (CONTRACTS.md), which is "re-fetch the board and the
 * orders view". An id is dropped from the detail cache so the next paint reads
 * it again; `route()` then re-renders. */

function listen() {
  const events = new EventSource("/api/events");
  events.onopen = () => connection(true);
  events.onerror = () => connection(false);
  events.onmessage = (event) => {
    let changed;
    try {
      changed = (JSON.parse(event.data) || {}).changed;
    } catch (error) {
      return;
    }
    if (!changed || !changed.length) return;
    // Every scope re-reads the board: an agent scope moves when its work item is
    // created or renamed (`hx launch`, `hx complete`), and only the board carries
    // that. Without this a fresh `hx launch` stayed invisible until a manual
    // reload (live rehearsal 2026-09-21).
    let wantsBoard = true;
    for (const scope of changed) {
      if (scope === "tasks" || scope === "board" || scope === "orders" || scope === "archive") {
        archiveStale = true;
      } else if (scope === "partner") refreshPartner().then(() => render());
      else details.delete(scope);
    }
    if (wantsBoard) refresh();
    else render();
  };
}

async function start() {
  await Promise.all([refresh(), refreshPartner(), refreshArchive()]);
  render();
  listen();
}

start();
