/* hx UI (spec 16.2). Vanilla, no framework, no build step, no CDN.
 *
 * The token is never in this file and never in a URL: GET / set an HttpOnly
 * cookie, so the browser carries it on every fetch and on the event stream, and
 * this script cannot read it. SSE pushes the scopes whose mtime moved and the
 * page re-fetches what is open. The only write the page can make is the Partner
 * wake; every other request is a GET. */

"use strict";

const main = document.getElementById("main");
const banner = document.getElementById("banner");
const live = document.getElementById("live");

let view = "board";
let agentId = null; // which id the Agent view is showing
let recent = new Set(); // scopes from the last SSE frame, flashed once

/* -- plumbing ---------------------------------------------------------- */

async function api(path, options) {
  const response = await fetch(path, Object.assign({ credentials: "same-origin" }, options));
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
  // flat(Infinity): a view may nest arrays (a heading plus a mapped list), and a
  // single-level flat would append the inner Array object itself.
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child);
  }
  return node;
}

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

function fail(message) {
  banner.textContent = message;
  banner.hidden = false;
}

function clearFail() {
  banner.hidden = true;
}

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

function section(title, ...body) {
  return el("section", {}, el("h2", { text: title }), body.flat());
}

/* -- markdown ----------------------------------------------------------
 * A deliberately small subset: headings, fenced code, lists, blockquotes,
 * paragraphs, and inline code/bold/italic. Everything becomes a DOM node built
 * with textContent, so nothing a HarnessAgent writes into a work item can
 * inject markup into this page. */

function inline(text, into) {
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)/g;
  let last = 0;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) into.append(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("`")) into.append(el("code", { text: token.slice(1, -1) }));
    else if (token.startsWith("**")) into.append(el("strong", { text: token.slice(2, -2) }));
    else into.append(el("em", { text: token.slice(1, -1) }));
    last = match.index + token.length;
  }
  if (last < text.length) into.append(text.slice(last));
  return into;
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
  const open = el("button", { class: "linkish", "data-open": item.id, text: item.id });
  return el(
    "tr",
    { class: [item.id === "partner" ? "partner" : "", recent.has(item.id) ? "flash" : ""].join(" ").trim() },
    el("td", { class: "id" }, open, el("div", { class: "sub", text: item.file || "" })),
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
}

function boardTable(payload) {
  const items = payload.items || [];
  return el(
    "div",
    { class: "scroll" },
    el(
      "table",
      {},
      el("thead", {}, el("tr", {}, COLUMNS.map((name) => el("th", { text: name })))),
      el("tbody", {}, items.map(boardRow))
    )
  );
}

function renderBoard(payload) {
  const items = payload.items || [];
  return [
    head(payload),
    errors(payload.errors),
    section("board · " + items.length + " items", boardTable(payload)),
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
    section(
      "after graph · " + edges.length + " edges",
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
    section(
      "orders · " + orders.length,
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
    section(
      "archive · " + items.length + " ids",
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

function stepState(handle, state) {
  const workingSet = state.working_set || {};
  return el(
    "article",
    { class: "card" },
    el(
      "div",
      { class: "order-head" },
      el("span", { class: "name", text: handle }),
      el("span", { class: "sub", text: "seq " + (state.seq === null || state.seq === undefined ? "—" : state.seq) }),
      state.prompt_version
        ? el("span", {
            class: "sub",
            text: "prompt " + (state.prompt_version.base || "?") + " / " + (state.prompt_version.role || "?"),
          })
        : null
    ),
    state.goal ? el("p", {}, el("strong", { text: "goal: " }), state.goal) : null,
    (state.constraints || []).length
      ? el("p", { class: "meta", text: "constraints: " + state.constraints.join("; ") })
      : null,

    el("h3", { text: "open steps" }),
    orNotYet(state.open_steps, (steps) =>
      el(
        "ul",
        { class: "steps" },
        steps.map((step) =>
          el(
            "li",
            {},
            el("span", { class: "stepid", text: step.id }),
            " ",
            el("span", { text: step.intent || "" }),
            el("div", { class: "next" }, el("span", { class: "lbl", text: "next: " }), step.next || "—"),
            el("div", { class: "sub", text: "ev " + (step.ev || []).join(", ") })
          )
        )
      )
    ),

    el("h3", { text: "closed steps" }),
    orNotYet(state.closed_steps, (steps) =>
      el(
        "ul",
        { class: "steps" },
        steps.map((step) =>
          el(
            "li",
            {},
            el("span", { class: "stepid", text: step.id }),
            " ",
            el("span", { text: step.outcome || "" }),
            " ",
            pill(step.verified ? "verified" : "unverified", step.verified ? "done" : "queued"),
            step.commit ? el("code", { class: "commit", text: step.commit }) : null,
            el("div", { class: "sub", text: "ev " + (step.ev || []).join(", ") })
          )
        )
      )
    ),

    el("h3", { text: "working set" }),
    el(
      "div",
      { class: "wset" },
      el("p", { class: "meta", text: "commits" }),
      orNotYet(workingSet.commits, (commits) =>
        el("ul", {}, commits.map((c) => el("li", {}, el("code", { class: "commit", text: c.sha || "" }), " " + (c.msg || ""))))
      ),
      el("p", { class: "meta", text: "dirty" }),
      orNotYet(workingSet.dirty, (dirty) => el("ul", {}, dirty.map((path) => el("li", { text: path })))),
      el("p", { class: "meta", text: "files read, not changed" }),
      orNotYet(workingSet.files, (files) =>
        el("ul", {}, files.map((f) => el("li", {}, el("code", { text: f.path || "" }), " — " + (f.note || ""))))
      ),
      workingSet.last_failure ? el("p", {}, el("strong", { text: "last failure: " }), workingSet.last_failure) : null,
      workingSet.hypothesis ? el("p", {}, el("strong", { text: "hypothesis: " }), workingSet.hypothesis) : null
    ),

    el("h3", { text: "blockers" }),
    orNotYet(state.blockers, (list) => el("ul", { class: "bad-list" }, list.map((b) => el("li", { text: b })))),

    el("h3", { text: "dead ends" }),
    orNotYet(state.dead_ends, (list) => el("ul", {}, list.map((d) => el("li", { text: d }))))
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

function number(value) {
  return value === null || value === undefined ? "—" : Number(value).toLocaleString();
}

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
        seams.length + (seams.length === 1 ? " seam" : " seams"),
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

function streamCard(stream, metrics) {
  return el(
    "article",
    { class: "card" },
    el(
      "div",
      { class: "order-head" },
      el("span", { class: "name", text: stream.handle }),
      pill(stream.open ? "open" : "closed", stream.open ? "working" : "complete"),
      el("span", { class: "sub", text: stream.records + " records  ·  " + stream.path })
    ),
    stream.digest ? el("p", {}, el("strong", { text: "digest: " }), stream.digest) : null,
    el("h3", { text: "tail" }),
    orNotYet(stream.tail, (tail) =>
      el(
        "div",
        { class: "tail" },
        tail.map((record) =>
          el(
            "div",
            { class: "rec" + (record.event === "seam" ? " seam" : "") },
            el("span", { class: "seq", text: "#" + record.seq }),
            el("span", { class: "sub", text: clock(record.ts) }),
            pill(record.event, record.event === "seam" ? "decision" : "none"),
            record.tool ? el("code", { text: record.tool }) : null,
            record.exit !== null && record.exit !== undefined
              ? pill("exit " + record.exit, record.exit === 0 ? "done" : "bad")
              : null,
            record.event === "seam"
              ? el("span", { class: "sub", text: "context file " + (record.context_file_bytes || 0) + " B" })
              : null,
            record.event === "seam" ? seamFollowUp(seamMetrics(metrics, record.seq)) : null,
            record.input ? el("pre", { class: "excerpt", text: String(record.input) }) : null,
            record.output ? el("pre", { class: "excerpt out", text: String(record.output) }) : null,
            el("div", {
              class: "sub",
              text: "context " + (record.context_tokens === null || record.context_tokens === undefined
                ? "—" : record.context_tokens.toLocaleString()),
            })
          )
        )
      )
    )
  );
}

function renderAgent(payload) {
  if (payload.__picker) {
    return [
      section(
        "agent",
        el("p", { class: "empty", text: "Pick an id on the Board, or here:" }),
        el("div", { class: "picker" }, (payload.ids || []).map((id) =>
          el("button", { class: "linkish", "data-open": id, text: id })
        ))
      ),
    ];
  }

  const body = (payload.work_item || {}).body || "";
  const pane = payload.pane || {};
  const subagents = payload.subagents || {};
  const digests = {};
  for (const stream of payload.streams || []) if (stream.digest) digests[stream.handle] = stream.digest;

  return [
    el(
      "p",
      { class: "meta" },
      payload.file || "",
      "  ·  ",
      payload.pod || "",
      " / ",
      payload.role || "",
      "  ·  persona ",
      payload.persona_path || "—"
    ),

    section(
      payload.id + " · work item",
      el(
        "article",
        { class: "card" },
        el("h3", { text: "frontmatter" }),
        frontmatterTable((payload.work_item || {}).frontmatter),

        el("h3", { text: "order" }),
        markdown((payload.task || {}).order || ""),

        ((payload.task || {}).addenda || []).length
          ? [
              el("h3", { text: "addenda" }),
              (payload.task.addenda || []).map((addendum) =>
                el(
                  "div",
                  { class: "addendum-block" },
                  el("p", { class: "meta", text: "addendum " + clock(addendum.ts) }),
                  markdown(addendum.text || "")
                )
              ),
            ]
          : null,

        el("h3", { text: "tasks" }),
        orNotYet(findSection(body, "Tasks"), (text) => markdown(text)),

        el("h3", { text: "deliverables" }),
        orNotYet(findSection(body, "Deliverables"), (text) => markdown(text)),

        el("h3", { text: "commands" }),
        orNotYet(findSection(body, "Commands"), (text) => markdown(text)),

        el("h3", { text: "open decision" }),
        orNotYet(findSection(body, "Open decision"), (text) => markdown(text)),

        el("h3", { text: "digest" }),
        orNotYet(findSection(body, "Digest"), (text) => markdown(text))
      )
    ),

    section(
      "step state",
      orNotYet(Object.keys(payload.step_state || {}), () =>
        Object.entries(payload.step_state).map(([handle, state]) => stepState(handle, state))
      )
    ),

    section(
      "context file",
      orNotYet(payload.context_file, (file) =>
        el(
          "article",
          { class: "card" },
          el("p", {
            class: "meta",
            text: file.path + "  ·  seam " + clock(file.seam_ts) + "  ·  " + (file.text || "").length + " chars",
          }),
          el("pre", { text: file.text || "" })
        )
      )
    ),

    section(
      "streams",
      orNotYet(payload.streams, (streams) => streams.map((stream) => streamCard(stream, payload.metrics)))
    ),

    section(
      "subagents",
      orNotYet(Object.keys(subagents), () =>
        el(
          "div",
          { class: "scroll" },
          el(
            "table",
            { class: "kv" },
            el("thead", {}, el("tr", {}, [el("th", { text: "claude agent_id" }), el("th", { text: "handle" }), el("th", { text: "digest" })])),
            el(
              "tbody",
              {},
              Object.entries(subagents).map(([claudeId, handle]) =>
                el(
                  "tr",
                  {},
                  el("td", {}, el("code", { text: claudeId })),
                  el("td", {}, el("code", { text: handle })),
                  el("td", { text: digests[payload.id + "-" + handle] || "not yet" })
                )
              )
            )
          )
        )
      )
    ),

    section("metrics", renderMetrics(payload.metrics)),

    section(
      "pane · " + (pane.session || payload.id),
      el(
        "article",
        { class: "card" },
        el(
          "p",
          { class: "meta" },
          el("span", { class: "dot" + (pane.alive ? "" : " off"), text: pane.alive ? "● live" : "● dead" }),
          "  " + (pane.lines || []).length + " lines" + (pane.source ? "  ·  from the " + pane.source : "")
        ),
        pane.error ? el("p", { class: "pane-error", text: pane.error }) : null,
        orNotYet(pane.lines, (lines) => el("pre", { class: "pane", text: lines.join("\n") }))
      )
    ),
  ];
}

/* -- partner ----------------------------------------------------------- */

function chatBox() {
  const input = el("textarea", {
    id: "wake-text",
    rows: "3",
    placeholder: "Message the Partner. This goes through `hx wake partner`.",
  });
  const status = el("span", { class: "sub", id: "wake-status" });
  const send = el("button", { class: "send", id: "wake-send", text: "Send" });

  async function submit() {
    const text = input.value.trim();
    if (!text) return;
    send.setAttribute("disabled", "disabled");
    status.textContent = "sending…";
    try {
      const result = await api("/api/partner/wake", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      status.textContent = result.delivered
        ? "delivered; the reply appears in the pane below"
        : "not delivered: no Partner socket, or the connection was refused";
      if (result.delivered) input.value = "";
    } catch (error) {
      status.textContent = "failed: " + error.message;
    } finally {
      send.removeAttribute("disabled");
    }
  }

  send.addEventListener("click", submit);
  return el("div", { class: "chat" }, input, el("div", { class: "chat-bar" }, send, status));
}

function renderPartner(payload) {
  const pane = payload.pane || {};
  return [
    el("p", {
      class: "meta",
      text: (payload.file || "") + "  ·  state " + (payload.state || "—"),
    }),

    section(
      "PARTNER.md",
      el("article", { class: "card" }, orNotYet(payload.partner_md, (text) => markdown(text)))
    ),

    section("board", orNotYet(payload.__board, (board) => [errors(board.errors), boardTable(board)])),

    section(
      "chat",
      el(
        "article",
        { class: "card" },
        el("p", {
          class: "meta",
          text: "A message here goes through `hx wake partner`. An idle Partner starts a turn; " +
            "a busy one takes it as steering in the current turn.",
        }),
        chatBox(),
        el("p", { class: "tmux-note" },
          "Full control — slash commands, interrupts — stays ",
          el("code", { text: "tmux attach -t partner" }),
          ". This page observes and sends; it does not operate the fleet."
        )
      )
    ),

    section(
      "pane · " + (pane.session || "partner") + " (the Partner's replies)",
      el(
        "article",
        { class: "card" },
        el(
          "p",
          { class: "meta" },
          el("span", { class: "dot" + (pane.alive ? "" : " off"), text: pane.alive ? "● live" : "● dead" }),
          "  " + (pane.lines || []).length + " lines" + (pane.source ? "  ·  from the " + pane.source : "")
        ),
        pane.error ? el("p", { class: "pane-error", text: pane.error }) : null,
        orNotYet(pane.lines, (lines) => el("pre", { class: "pane", text: lines.join("\n") }))
      )
    ),
  ];
}

/* -- views ------------------------------------------------------------- */

const VIEWS = {
  board: { load: () => api("/api/board"), render: renderBoard },
  orders: { load: () => api("/api/orders"), render: renderOrders },
  archive: { load: () => api("/api/archive"), render: renderArchive },
  agent: {
    load: async () => {
      if (agentId) return api("/api/show/" + encodeURIComponent(agentId));
      const board = await api("/api/board");
      return { __picker: true, ids: (board.items || []).map((item) => item.id) };
    },
    render: renderAgent,
  },
  partner: {
    load: async () => {
      const [show, board] = await Promise.all([api("/api/show/partner"), api("/api/board")]);
      return Object.assign({}, show, { __board: board });
    },
    render: renderPartner,
  },
};

async function draw() {
  const spec = VIEWS[view];
  try {
    const payload = await spec.load();
    main.replaceChildren(...spec.render(payload).flat(Infinity).filter(Boolean));
    clearFail();
  } catch (error) {
    main.replaceChildren();
    fail(view + (view === "agent" && agentId ? " " + agentId : "") + ": " + error.message);
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

main.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-open]");
  if (!button) return;
  agentId = button.dataset.open;
  select(agentId === "partner" ? "partner" : "agent");
});

/* -- live updates ------------------------------------------------------ */

function listen() {
  const events = new EventSource("/api/events");
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
