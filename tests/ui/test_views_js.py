"""The views themselves (spec 16.2), rendered headlessly.

`tests/ui/js/render.js` runs `src/hx/ui/static/app.js` against the real
`static/index.html` in a DOM shim and prints what each screen produced. This is
the only check that the page renders exactly the fields CONTRACTS.md defines; it
skips where node is absent, so it never blocks the suite on a machine without a
JS runtime.

ui-8 restored autodev's shell, so the screens changed shape: the home page is
the fleet graph, the task board is cards in state columns, and the Agent page of
spec 16.2 is the drawer. Every assertion below is the ui-1..ui-7 assertion
pointed at where that fact is now rendered, not a new one — `views.agents[<id>]`
is still the agent, and still carries the work item, the step state, the
streams, the metrics and the pane.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import FIXTURES

REPO = Path(__file__).resolve().parents[2]
SKELETON_PARTNER = REPO / "src" / "hx" / "skeleton" / "PARTNER.md"
RENDER = Path(__file__).parent / "js" / "render.js"
STATIC = REPO / "src" / "hx" / "ui" / "static"

node = pytest.mark.skipif(shutil.which("node") is None, reason="no node on PATH")


def render(overrides=None, tmp_path=None, open_ids=None):
    """Run the real `static/app.js` under node and return what each screen produced.

    `open_ids` are the agents to open in the drawer, in turn; each lands in
    `views.agents[id]`.
    """
    if shutil.which("node") is None:
        pytest.skip("no node on PATH")
    argv = ["node", str(RENDER), str(FIXTURES), str(STATIC)]
    if overrides is not None:
        path = tmp_path / "overrides.json"
        path.write_text(json.dumps(overrides), encoding="utf-8")
        argv.append(str(path))
    elif open_ids:
        argv.append("")
    if open_ids:
        argv.append(",".join(open_ids))
    result = subprocess.run(argv, capture_output=True, text=True, check=False, cwd=REPO)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def rendered():
    return render()


def board_json():
    return json.loads((FIXTURES / "board.json").read_text())


def orders_json():
    return json.loads((FIXTURES / "orders.json").read_text())


def show_json(name="eng-001"):
    return json.loads((FIXTURES / f"show-{name}.json").read_text())


@node
@pytest.mark.parametrize("name", ["app.js", "domshim.js", "render.js"])
def test_the_javascript_parses(name):
    target = STATIC / name if name == "app.js" else RENDER.parent / name
    assert subprocess.run(["node", "--check", str(target)], capture_output=True).returncode == 0


@node
def test_the_page_pulls_nothing_from_a_cdn():
    """Spec 16.1: static vanilla JS and CSS from the package, no CDN, no build step."""
    for name in ("index.html", "app.js", "style.css"):
        text = (STATIC / name).read_text(encoding="utf-8")
        for marker in ("http://", "https://", "cdn.", "unpkg", "jsdelivr", "googleapis"):
            assert marker not in text, f"{name} reaches outside the package: {marker}"


# -- the shell (autodev's, restored) -------------------------------------

def test_every_screen_renders_without_an_error_banner(rendered):
    assert rendered["banner"] is None
    assert set(rendered["views"]) == {
        "overview", "board", "agentTable", "activity", "orders", "archive",
        "agents", "agent", "podFocus", "cardOpens", "partner",
    }


def test_the_sidebar_lists_the_instance_the_pods_and_the_across_pod_views(rendered):
    sidebar = rendered["views"]["overview"]["sidebar"]
    pods = sorted({item["pod"] for item in board_json()["items"]})
    assert [pod["text"].lstrip("›⌄") for pod in sidebar["pods"]] == pods
    assert sidebar["workspace"] == "hx", "the instance name is the HARNESS_ROOT's own"
    assert [link["nav"] for link in sidebar["links"]] == [
        "overview", "board", "agents", "activity", "orders", "archive", "partner",
    ]
    assert next(link for link in sidebar["links"] if link["nav"] == "overview")["selected"]


def test_the_top_level_tabs_are_graph_board_agents_and_partner(rendered):
    """Goal ui-8: no pod pages, and no Contract tab."""
    for view in ("overview", "board", "partner"):
        labels = [tab["label"] for tab in rendered["views"][view]["tabs"]]
        assert labels == ["Graph", "Task board", "Harness Agents", "Partner chat"], view
        assert "Contract" not in labels
    assert [tab["selected"] for tab in rendered["views"]["overview"]["tabs"]][0] is True


def test_the_breadcrumb_walks_instance_pod_agent(rendered):
    assert rendered["views"]["overview"]["breadcrumb"] == ["hx", "All Pods"]
    assert rendered["views"]["podFocus"]["breadcrumb"] == ["hx", "engineers"]
    assert rendered["views"]["agent"]["breadcrumb"] == ["hx", "engineers", "eng-001"]


def test_the_graph_is_the_home_page_and_sse_is_opened_at_load(rendered):
    urls = [request["url"] for request in rendered["requests"]]
    assert urls[0] == "/api/board"
    assert "/api/events" in urls, "SSE is opened at load; there is no poll"


def test_no_request_carries_a_token(rendered):
    """The token is in an HttpOnly cookie; the page cannot read it and never sends it."""
    for request in rendered["requests"]:
        assert "token" not in request["url"], request["url"]
        assert "Authorization" not in (request.get("headers") or {})
    for post in rendered["posted"]:
        assert "token" not in post["url"]


def test_every_fetch_sends_the_cookie(rendered):
    """`credentials: same-origin` is what carries the cookie on a fetch."""
    for request in rendered["requests"]:
        if not request.get("sse"):
            assert request["credentials"] == "same-origin", request["url"]
    for post in rendered["posted"]:
        assert post["credentials"] == "same-origin"


# -- the fleet graph (goal ui-8: hx is the graph) ------------------------

def test_the_partner_is_the_root_of_the_graph(rendered):
    """v1 cut: the Partner is not a board item, so it is drawn from nothing else."""
    graph = rendered["views"]["overview"]["graph"]
    partner = next(node for node in graph["nodes"] if node["agent"] == "partner")
    assert partner["name"] == "Partner"
    assert "manager" in partner["class"]
    assert all(partner["top"] < node["top"] for node in graph["nodes"] if node["agent"] != "partner")
    assert "no work item" in rendered["views"]["overview"]["text"]


def test_every_board_id_is_a_node_clustered_by_pod(rendered):
    board = board_json()
    graph = rendered["views"]["overview"]["graph"]
    assert [node["agent"] for node in graph["nodes"] if node["agent"] != "partner"] == [
        item["id"] for item in board["items"]
    ]
    pods = sorted({item["pod"] for item in board["items"]})
    assert [cluster["pod"] for cluster in graph["clusters"]] == pods
    for cluster in graph["clusters"]:
        count = sum(1 for item in board["items"] if item["pod"] == cluster["pod"])
        assert cluster["label"].endswith(str(count)), "the cluster is labelled with its size"


def test_every_agent_has_its_companion_beside_it(rendered):
    """CONTRACTS.md: every agent has exactly one Companion; it is not a board row."""
    graph = rendered["views"]["overview"]["graph"]
    workers = [node for node in graph["nodes"] if node["agent"] != "partner"]
    assert len(graph["companions"]) == len(workers)
    assert all(text.startswith("Companion") for text in graph["companions"])
    assert sum(1 for edge in graph["edges"] if edge["class"] == "companion-edge") == len(workers)


def test_the_partner_edges_are_the_dispatches_in_tasks_json(rendered):
    """`hx orders` reads tasks.json; a board id with no record has no dispatch edge."""
    dispatched = {order["id"] for order in orders_json()["orders"] if order["dispatched"]}
    graph = rendered["views"]["overview"]["graph"]
    handoffs = {edge["title"] for edge in graph["edges"] if edge["class"].startswith("handoff")}
    assert handoffs == {f"Partner dispatched {id}" for id in dispatched}
    others = {item["id"] for item in board_json()["items"]} - dispatched
    pending = {edge["title"] for edge in graph["edges"] if edge["class"] == "membership"}
    assert pending == {f"{id} has no dispatch in tasks.json" for id in others}


def test_every_node_says_what_that_agent_is_doing(rendered):
    """Goal ui-8 item 4: the open step's next action, without opening the agent."""
    graph = rendered["views"]["overview"]["graph"]
    lines = {node["agent"]: node["task"] for node in graph["nodes"]}
    main = show_json()["step_state"]["eng-001-main"]
    assert lines["eng-001"] == main["open_steps"][0]["next"]
    # res-001's Companion has written no state yet, so the first unchecked
    # `## Tasks` line stands in for it.
    assert lines["res-001"] == "Run one live session and capture the payload."
    assert all(line for line in lines.values())


def test_the_graph_has_the_demos_zoom_and_fit_controls(rendered):
    assert rendered["views"]["overview"]["graph"]["zoom"] == "100%"
    assert "Fit" in rendered["views"]["overview"]["text"]


def test_a_pod_entry_zooms_the_graph_rather_than_opening_a_pod_page(rendered):
    """Goal ui-8: there are no pod pages."""
    focus = rendered["views"]["podFocus"]
    assert focus["heading"] == rendered["views"]["overview"]["heading"], "still the graph"
    engineers = next(c for c in focus["graph"]["clusters"] if c["pod"] == "engineers")
    assert "focused" in engineers["class"]
    assert [agent["text"] for agent in focus["sidebar"]["agents"]] == [
        item["id"] for item in board_json()["items"] if item["pod"] == "engineers"
    ], "the pod's agents are listed under it in the sidebar"


# -- the task board ------------------------------------------------------

def test_the_columns_are_the_state_and_the_outcome(rendered):
    """v1 cut: `idle | working | complete`, and one column per outcome."""
    labels = [column["label"] for column in rendered["views"]["board"]["columns"]]
    assert [label.rstrip("0123456789") for label in labels] == [
        "Idle", "Working", "Complete · done", "Complete · decision",
        "Complete · blocked", "Complete · exhausted",
    ]


def test_one_card_per_work_item_in_its_own_column(rendered):
    board = board_json()
    cards = rendered["views"]["board"]["cards"]
    assert {card["agent"] for card in cards} == {item["id"] for item in board["items"]}
    columns = {column["label"].rstrip("0123456789"): column for column in
               ({"label": c["label"], "cards": c["cards"]} for c in rendered["views"]["board"]["columns"])}
    assert columns["Working"]["cards"] == sum(1 for i in board["items"] if i["state"] == "working")
    assert columns["Complete · done"]["cards"] == sum(
        1 for i in board["items"] if i["outcome"] == "done"
    )


def test_a_card_carries_that_agents_own_tasks_checklist(rendered):
    """Goal ui-8: the agent's `## Tasks` list is what is shown, not a counter."""
    show = show_json()
    card = next(c for c in rendered["views"]["board"]["cards"] if c["agent"] == "eng-001")
    body_tasks = [
        line.strip()
        for line in work_item_section(show, "Tasks").splitlines()
        if line.strip().startswith("- [")
    ]
    assert len(card["tasks"]) == len(body_tasks) == 4
    for rendered_task, source in zip(card["tasks"], body_tasks):
        assert rendered_task["done"] is source.startswith("- [x]")
        assert source.split("] ", 1)[1].replace("`", "") in rendered_task["text"].replace("`", "")
    assert [t["done"] for t in card["tasks"]] == [True, True, False, False]


def test_a_card_is_titled_with_the_orders_first_line_and_aged_from_dispatched(rendered):
    order = next(o for o in orders_json()["orders"] if o["id"] == "eng-001")
    card = next(c for c in rendered["views"]["board"]["cards"] if c["agent"] == "eng-001")
    assert card["title"] == order["order"].splitlines()[1]
    assert card["time"].endswith("ago")
    idle = next(c for c in rendered["views"]["board"]["cards"] if c["agent"] == "eng-002")
    assert idle["time"] == "Not dispatched"
    assert idle["title"] == "No order dispatched"
    assert idle["noTasks"], "an item with no `## Tasks` says so rather than showing nothing"


def test_a_card_says_what_the_agent_is_doing(rendered):
    main = show_json()["step_state"]["eng-001-main"]
    card = next(c for c in rendered["views"]["board"]["cards"] if c["agent"] == "eng-001")
    assert card["progress"] == main["open_steps"][0]["next"]


def test_a_board_card_opens_the_agent(rendered):
    opened = rendered["views"]["cardOpens"]
    assert opened["hidden"] is False
    assert opened["heading"] == opened["hash"].split("agent=")[1]


# -- the agent table -----------------------------------------------------

def test_the_agent_table_has_a_column_per_contract_field(rendered):
    assert rendered["views"]["agentTable"]["headers"] == [
        "Harness Agent", "Pod", "Role", "State", "Current work", "Session", "Seams",
    ]


def test_the_agent_table_has_one_row_per_id_and_no_partner(rendered):
    """v1 cut: the Partner is not a board row — it has no work item."""
    rows = rendered["views"]["agentTable"]["rows"]
    board = board_json()
    assert len(rows) == len(board["items"])
    assert not any(row["agent"] == "partner" for row in rows)
    for row, item in zip(rows, board["items"]):
        assert row["agent"] == item["id"]
        assert item["file"] in row["cells"][0]
        assert row["cells"][1] == item["pod"]
        assert row["cells"][2] == item["role"]


def test_a_dead_session_is_visible_in_the_table(rendered):
    """With no invariants in v1 this column is the only sign."""
    rows = {row["agent"]: row["cells"] for row in rendered["views"]["agentTable"]["rows"]}
    assert rows["res-001"][5] == "none"
    assert rows["eng-001"][5] == "alive"


def test_an_id_with_no_stream_reads_dash_not_zero(rendered):
    rows = {row["agent"]: row["cells"] for row in rendered["views"]["agentTable"]["rows"]}
    assert rows["eng-002"][6] == "—", "seams"
    assert rows["eng-001"][6] == "2"


def test_the_state_and_outcome_are_one_pill(rendered):
    rows = {row["agent"]: row["cells"] for row in rendered["views"]["agentTable"]["rows"]}
    assert rows["eng-000"][3] == "complete: done"
    assert rows["eng-003"][3] == "complete: decision"
    assert rows["eng-002"][3] == "idle"


def test_every_row_says_what_that_agent_is_doing(rendered):
    main = show_json()["step_state"]["eng-001-main"]
    rows = {row["agent"]: row["cells"] for row in rendered["views"]["agentTable"]["rows"]}
    assert rows["eng-001"][4] == main["open_steps"][0]["next"]
    assert all(cells[4] for cells in rows.values())


# -- the Agent page, which is the drawer (spec 16.2) ---------------------

def labels_of(view):
    return [entry["label"] for entry in view["labels"]]


def work_item_section(show, name):
    """The named `## ` section of the work-item body."""
    current, lines = None, []
    for line in show["work_item"]["body"].splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            continue
        if current == name:
            lines.append(line)
    return "\n".join(lines).strip()


def test_the_agent_page_has_every_section_spec_16_2_names(rendered):
    agent = rendered["views"]["agent"]
    assert agent["hidden"] is False
    assert agent["headings"] == ["eng-001"]
    labels = labels_of(agent)
    for name in ("Work item", "Frontmatter", "Order", "Addenda", "Tasks", "Deliverables",
                 "Commands", "Open decision", "Digest", "Step state", "Context file",
                 "Streams", "Subagents", "Metrics", "Persona"):
        assert name in labels, name
    assert any(label.startswith("Pane · eng-001") for label in labels)
    for name in ("open steps", "closed steps", "working set", "blockers", "dead ends", "tail"):
        assert name in agent["subheadings"], name


def test_the_agent_page_renders_the_work_item_sections(rendered):
    show = show_json()
    agent = rendered["views"]["agent"]
    text = agent["text"]
    assert "Add `--require-done`" in show["task"]["order"]
    assert "--require-done" in text, "the order is rendered"
    addendum = show["task"]["addenda"][0]["text"].replace("`", "")
    assert addendum in text.replace("`", ""), "the addendum is rendered beneath the order"

    tasks = {item["text"] for item in agent["listItems"] if item["class"].startswith("task")}
    body_tasks = [line.strip("- ").strip()
                  for line in work_item_section(show, "Tasks").splitlines() if line.strip()]
    assert len(body_tasks) == 4
    for line in body_tasks:
        label = line.replace("[x]", "").replace("[ ]", "").strip().replace("`", "")
        assert any(label in task for task in tasks), label


def test_the_agent_page_renders_step_state(rendered):
    """Every field of the build-6 schema the human needs (ui-7 item 1)."""
    agent = rendered["views"]["agent"]
    text = agent["text"].replace("`", "")
    main = show_json()["step_state"]["eng-001-main"]

    assert main["goal"].replace("`", "") in text
    for constraint in main["constraints"]:
        assert constraint in text
    for decision in main["decisions"]:
        assert decision["d"].replace("`", "") in text
        assert decision["why"].replace("`", "") in text, "a decision shows its reason"
    for step in main["open_steps"]:
        assert step["intent"].replace("`", "") in text
        assert step["next"].replace("`", "") in text, "an open step shows its next action"
    commits = [c["text"] for c in agent["code"] if c["class"] == "commit"]
    for step in main["closed_steps"]:
        assert step["outcome"].replace("`", "") in text
        if step["commit"]:
            assert step["commit"] in commits, "a closed step shows its commit"
    for note in main["working_set"]["files"]:
        assert note["note"].replace("`", "") in text, "a working-set file shows why it was read"
    assert main["working_set"]["last_failure"] in text
    assert main["working_set"]["hypothesis"] in text
    assert main["dead_ends"][0] in text
    assert "caught up through record " + str(main["seq"]) in text


def test_evidence_seqs_are_shown(rendered):
    """`ev` is the Companion's pointer back into the raw stream (spec 07.2)."""
    main = show_json()["step_state"]["eng-001-main"]
    text = rendered["views"]["agent"]["text"]
    for step in main["open_steps"] + main["closed_steps"] + main["decisions"]:
        if step.get("ev"):
            assert "ev " + ", ".join(str(n) for n in step["ev"]) in text


def test_blockers_render_on_the_stream_that_has_them(rendered):
    blockers = show_json()["step_state"]["eng-001-s001"]["blockers"]
    assert blockers, "the subagent stream has a blocker"
    for blocker in blockers:
        assert blocker in rendered["views"]["agent"]["text"]


def test_the_budget_bar_reads_against_the_default(rendered):
    """Spec 10: chars/4, the same estimate hx evicts on."""
    compact = json.dumps(show_json()["step_state"]["eng-001-main"], separators=(",", ":"))
    used = -(-len(compact) // 4)
    text = rendered["views"]["agent"]["text"]
    assert f"{used:,} of 10,000 tokens (est.)" in text
    assert "budget: the templates/worker default" in text, (
        "hx show does not carry state_budget_tokens, so say where the number came from"
    )


def test_the_agent_page_renders_the_context_file_with_its_seam(rendered):
    show = show_json()
    text = rendered["views"]["agent"]["text"]
    assert show["context_file"]["path"] in text
    assert "seam 12:50:00Z" in text
    plain = text.replace("`", "")
    for line in (
        "Prefer same-directory renames; spec 08 forbids timeouts anywhere.",
        "Also refuse an id with no config/<id>/ directory; exit 1 and name it.",
        "open: st7 refuse an id with no config/<id>/",
    ):
        assert line in plain, line


def test_the_agent_page_renders_every_stream_tail(rendered):
    show = show_json()
    text = rendered["views"]["agent"]["text"]
    for stream in show["streams"]:
        assert stream["handle"] in text
        assert stream["path"] in text
        if stream.get("digest"):
            first = stream["digest"].strip().splitlines()[0].replace("`", "")
            assert first in text.replace("`", ""), stream["handle"]


def test_a_seam_shows_its_context_file_size(tmp_path):
    """Spec 7.4. The record shape is build-7's; the rendering is here."""
    agent = seam_agent(tmp_path, show_with_seam())
    assert "context file 2184 B" in agent["text"]


def test_the_agent_page_renders_subagents_and_metrics_and_the_pane(rendered):
    show = show_json()
    text = rendered["views"]["agent"]["text"]
    for claude_id, handle in show["subagents"].items():
        assert claude_id in text and handle in text
    assert rendered["views"]["agent"]["metrics"] is not None, "hx metrics is rendered"
    for line in show["pane"]["lines"]:
        assert line in text


def test_an_absent_value_reads_not_yet(rendered):
    """The goal: render `null` as "not yet", never as an empty box."""
    assert rendered["views"]["agent"]["notYet"] > 0
    assert "not yet" in rendered["views"]["agent"]["text"]


def test_the_agent_page_names_the_work_items_file_and_the_persona(rendered):
    show = show_json()
    text = rendered["views"]["agent"]["text"]
    assert show["file"] in text
    assert show["persona_path"] in text


# -- the Partner (spec 16.2) ---------------------------------------------

def test_the_partner_page_renders_partner_md_the_board_and_chat(rendered):
    partner = rendered["views"]["partner"]
    show = show_json("partner")

    assert "PARTNER.md" in partner["headings"]
    assert "Partner" in partner["headings"], "the chat panel"
    assert "Partner pane" in partner["headings"], "where the replies come from"

    for line in show["partner_md"].splitlines():
        body = line.lstrip("#- ").strip()
        if body:
            assert body.replace("`", "") in partner["text"].replace("`", ""), body

    fleet = [row for row in partner["rows"] if row["agent"]]
    assert len(fleet) == len(board_json()["items"]), "the whole board is on the Partner page"


def test_the_partner_page_says_where_full_control_is(rendered):
    """Spec 16.2: the page says so."""
    text = rendered["views"]["partner"]["text"]
    assert "tmux attach -t partner" in text
    assert "slash commands" in text and "interrupts" in text


def test_the_chat_box_posts_the_trimmed_text_and_nothing_else(rendered):
    assert rendered["posted"] == [
        {
            "url": "/api/partner/wake",
            "body": {"text": "eng-003 complete: decision; hx read eng-003"},
            "credentials": "same-origin",
        }
    ]


def test_the_chat_box_reports_delivery_and_clears(rendered):
    chat = rendered["chat"]
    assert chat["cleared"] is True
    assert chat["said"] == ["Delivered. The reply appears in the pane below."]
    assert "eng-003 complete: decision" in chat["messages"][0], "the message stays in the log"
    assert chat["error"] == ""


@node
@pytest.mark.parametrize(
    "status, said",
    [
        ("no-socket", "it has not started a session"),
        ("refused", "socket refused the connection"),
    ],
)
def test_an_undelivered_message_says_which_kind(tmp_path, status, said):
    """CONTRACTS.md gives three answers; two of them are different problems."""
    rendered = render({"__wake__": {"delivered": False, "status": status}}, tmp_path)
    assert said in rendered["chat"]["said"][0]
    assert rendered["chat"]["cleared"] is False, "an undelivered message is not thrown away"


def test_the_partner_drawer_has_no_work_item(rendered):
    """v1 cut: `hx show partner --json` is `{id, partner_md, pane, streams}`."""
    partner = rendered["views"]["agents"]["partner"]
    assert partner["hidden"] is False
    assert "no work item · no task · no step state" in partner["text"]
    assert "Work item" not in labels_of(partner)
    assert "PARTNER.md" in labels_of(partner)


def test_escape_closes_the_drawer(rendered):
    assert rendered["drawerClosed"] is True


# -- orders and archive (hx-only views; spec 16.2 lists both) ------------

def test_the_orders_view_shows_every_order_and_addendum(rendered):
    """v1 cut: one panel per dispatched id, rendered as markdown."""
    orders = orders_json()["orders"]
    view = rendered["views"]["orders"]
    assert view["headings"] == ["Dispatched work"] + [order["id"] for order in orders]
    text = view["text"].replace("`", "")
    for order in orders:
        assert order["id"] in text
        assert order["order"].splitlines()[1].replace("`", "") in text, order["id"]
        for addendum in order["addenda"]:
            assert addendum["text"].replace("`", "") in text


def test_the_orders_view_has_no_after_graph_and_no_file_badge(rendered):
    view = rendered["views"]["orders"]
    labels = [badge["text"] for badge in view["badges"]]
    assert "file edited since dispatch" not in labels
    assert "order file missing" not in labels
    assert not any("after" in label for label in labels)


def test_an_order_opens_its_agent(rendered):
    orders = orders_json()["orders"]
    assert rendered["views"]["orders"]["openable"] == [order["id"] for order in orders]


def test_the_archive_view_shows_benched_bodies_and_dispatches(rendered):
    archive = json.loads((FIXTURES / "archive.json").read_text())
    view = rendered["views"]["archive"]
    text = view["text"]
    assert view["headings"] == ["Per agent"] + [item["id"] for item in archive["items"]]
    for item in archive["items"]:
        for entry in item["bench"] + item["archive"]:
            assert entry["path"] in text
            assert entry["digest"].strip().splitlines()[0].replace("`", "") in text.replace("`", "")


# -- activity ------------------------------------------------------------

def test_activity_is_the_stream_tails_newest_first(rendered):
    """Goal ui-8: activity is the stream tails with their seam markers."""
    text = rendered["views"]["activity"]["text"]
    for handle in ("eng-001-main", "eng-001-s001", "eng-000-main", "res-001-main"):
        assert handle in text, handle
    assert "seam" in text


# -- metrics (spec 07.4, CONTRACTS.md `hx metrics --json`) ---------------

def metrics_document():
    return json.loads((FIXTURES / "metrics-eng-001.json").read_text())


#: `hx seam` is build-7, so no real run writes a `seam` record yet and the
#: regenerated fixture has none. The rendering is still needed — CONTRACTS.md's
#: metrics document is keyed on seam `seq`, and spec 16.2 requires the marker.
SEAM_SEQ = 412


def show_with_seam(**overrides):
    show = show_json()
    main = next(s for s in show["streams"] if s["handle"] == "eng-001-main")
    record = {
        "seq": SEAM_SEQ,
        "ts": "2026-09-20T13:09:40Z",
        "stream": "eng-001-main",
        "event": "seam",
        "prompt_version": {"base": "9c1f2ab", "role": "4d80e17"},
        "context_file_bytes": 2184,
        "context_tokens": 48211,
        "ref": {"transcript": "run/eng-001/home/projects/hx/a7c21f.jsonl", "tool_use_id": None},
    }
    record.update(overrides)
    main["tail"] = main["tail"] + [record]
    return show


def seam_agent(tmp_path, show):
    return render({"/api/show/eng-001": show}, tmp_path)["views"]["agent"]


def test_the_metrics_table_has_a_column_per_contract_field(rendered):
    table = rendered["views"]["agent"]["metrics"]
    assert table is not None, "metrics render as a table, not as raw JSON"
    assert table["headers"] == [
        "seq", "ts", "source", "prompt", "ctx tokens before", "context file",
        "working set", "turns", "tool calls", "ctx-file reads", "working-set reads", "other",
    ]


def test_one_row_per_seam_in_order(rendered):
    seams = metrics_document()["seams"]
    rows = rendered["views"]["agent"]["metrics"]["rows"]
    assert len(rows) == len(seams)
    for row, seam in zip(rows, seams):
        cells = [cell["text"] for cell in row["cells"]]
        assert cells[0] == str(seam["seq"])
        assert cells[2] == seam["source"]
        assert cells[3] == seam["prompt_version"]
        assert cells[4] == f"{seam['context_tokens_before']:,}"
        assert cells[5] == f"{seam['context_file_bytes']:,} B"
        assert cells[6] == str(seam["working_set_size"])
        next_10 = seam["next_10_turns"]
        assert cells[7] == str(next_10["turns"])
        assert cells[8] == str(next_10["tool_calls"])
        assert cells[9] == str(next_10["reads_of_context_file"])
        assert cells[10] == str(next_10["reads_of_working_set"])
        assert cells[11] == str(next_10["other"])


def test_a_seam_is_marked_when_it_did_not_hand_over_cleanly(rendered):
    """Red on `reads_of_context_file != 1` or any `reads_of_working_set`."""
    seams = metrics_document()["seams"]
    rows = rendered["views"]["agent"]["metrics"]["rows"]
    for row, seam in zip(rows, seams):
        next_10 = seam["next_10_turns"]
        bad = next_10["reads_of_context_file"] != 1 or next_10["reads_of_working_set"] > 0
        assert ("bad-row" in row["class"]) is bad, seam["seq"]
    assert sum(1 for row in rows if "bad-row" in row["class"]) == 3, "the fixture has three"


@pytest.mark.parametrize(
    "seq, offending, why",
    [
        (203, ["3"], "re-read 3 working-set files"),
        (318, ["0"], "never read its context file"),
        (401, ["2", "1"], "read the context file 2 times; re-read 1 working-set file"),
    ],
)
def test_the_offending_number_is_marked_and_explained(rendered, seq, offending, why):
    rows = rendered["views"]["agent"]["metrics"]["rows"]
    row = next(r for r in rows if r["cells"][0]["text"] == str(seq))
    assert [c["text"] for c in row["cells"] if "bad-cell" in c["class"]] == offending
    assert row["title"] == why, "hovering says why, so the colour is not the only signal"


def test_a_clean_seam_marks_nothing(rendered):
    rows = rendered["views"]["agent"]["metrics"]["rows"]
    for seq in ("96", "412"):
        row = next(r for r in rows if r["cells"][0]["text"] == seq)
        assert row["class"] == ""
        assert row["title"] is None
        assert not [c for c in row["cells"] if "bad-cell" in c["class"]]


def test_a_stream_that_ended_early_shows_its_real_turn_count(rendered):
    rows = rendered["views"]["agent"]["metrics"]["rows"]
    row = next(r for r in rows if r["cells"][0]["text"] == "412")
    turns = row["cells"][7]
    assert turns["text"] == "3", "next_10_turns.turns is fewer than 10 when the stream ended sooner"
    assert "short" in turns["class"], "and is marked as a partial window"


def test_the_totals_row_matches_the_contract_totals(rendered):
    totals = metrics_document()["totals"]
    cells = [cell["text"] for cell in rendered["views"]["agent"]["metrics"]["totals"]["cells"]]
    assert cells[0] == "totals"
    assert cells[2] == f"{totals['seams']} seams"
    assert cells[8] == str(totals["tool_calls"])
    assert cells[9] == str(totals["reads_of_context_file"])
    assert cells[10] == str(totals["reads_of_working_set"])
    assert cells[11] == str(totals["other"])


def test_the_totals_row_marks_the_fleet_level_waste(rendered):
    """One context-file read per seam is the target; any working-set read is waste."""
    totals = rendered["views"]["agent"]["metrics"]["totals"]
    marked = [cell["text"] for cell in totals["cells"] if "bad-cell" in cell["class"]]
    assert marked == ["4"], "5 ctx-file reads over 5 seams is right; 4 working-set reads is not"


def test_the_metrics_section_says_how_many_seams_were_dirty(rendered):
    warn = rendered["views"]["agent"]["metrics"]["warn"]
    assert len(warn) == 1
    assert warn[0].startswith("3 of 5 seams did not hand over cleanly")
    assert not rendered["views"]["agent"]["metrics"]["ok"]


def test_a_clean_run_says_so_instead(tmp_path):
    show = show_json()
    for seam in show["metrics"]["seams"]:
        seam["next_10_turns"]["reads_of_context_file"] = 1
        seam["next_10_turns"]["reads_of_working_set"] = 0
    show["metrics"]["totals"]["reads_of_context_file"] = len(show["metrics"]["seams"])
    show["metrics"]["totals"]["reads_of_working_set"] = 0
    agent = render({"/api/show/eng-001": show}, tmp_path)["views"]["agent"]
    assert agent["metrics"]["ok"] == ["every seam handed over cleanly."]
    assert not agent["metrics"]["warn"]
    assert not [row for row in agent["metrics"]["rows"] if row["class"]]
    assert not [c for c in agent["metrics"]["totals"]["cells"] if "bad-cell" in c["class"]]


def test_an_agent_with_no_seams_yet_reads_not_yet(tmp_path):
    show = show_json()
    show["metrics"] = {"id": "eng-001", "stream": "eng-001-main", "dispatched": None,
                       "seams": [], "totals": {"seams": 0, "tool_calls": 0,
                                               "reads_of_context_file": 0,
                                               "reads_of_working_set": 0, "other": 0}}
    agent = render({"/api/show/eng-001": show}, tmp_path)["views"]["agent"]
    assert agent["metrics"] is None
    assert agent["notYet"] > 0


# -- seams in the stream tail (spec 16.2) --------------------------------

def test_a_seam_record_shows_the_turns_that_followed_it(tmp_path):
    """Spec 16.2: the marker carries the context file size and the ten turns after."""
    followups = seam_agent(tmp_path, show_with_seam())["followups"]
    assert len(followups) == 1, "one seam record in the fixture's tails"
    text = followups[0]["text"]
    seam = next(s for s in metrics_document()["seams"] if s["seq"] == SEAM_SEQ)
    next_10 = seam["next_10_turns"]
    assert text.startswith("next 3 turns: "), "the real window, not a hardcoded 10"
    assert f"{next_10['tool_calls']} tool calls" in text
    assert f"{next_10['reads_of_context_file']} ctx-file" in text
    assert f"{next_10['reads_of_working_set']} working-set" in text
    assert f"{next_10['other']} other" in text


def test_a_clean_seam_marker_is_not_marked(tmp_path):
    agent = seam_agent(tmp_path, show_with_seam())
    assert agent["followups"][0]["class"] == "followup"
    assert agent["followups"][0]["title"] is None


def test_a_dirty_seam_marker_is_marked_and_explained(tmp_path):
    show = show_with_seam()
    seam = next(s for s in show["metrics"]["seams"] if s["seq"] == SEAM_SEQ)
    seam["next_10_turns"]["reads_of_working_set"] = 2
    followup = seam_agent(tmp_path, show)["followups"][0]
    assert "bad" in followup["class"]
    assert followup["title"] == "re-read 2 working-set files"
    assert "2 working-set" in followup["text"]


def test_a_seam_with_no_metrics_entry_says_not_yet(tmp_path):
    """The tail can outrun the metrics document; it must not render a wrong number."""
    show = show_with_seam()
    show["metrics"]["seams"] = [s for s in show["metrics"]["seams"] if s["seq"] != SEAM_SEQ]
    agent = seam_agent(tmp_path, show)
    assert agent["followups"][0]["text"] == "next 10 turns: not yet"
    assert "context file 2184 B" in agent["text"], "the size still comes from the record itself"


def test_only_the_seam_record_gets_a_follow_up(tmp_path):
    agent = seam_agent(tmp_path, show_with_seam())
    assert len(agent["followups"]) == 1, "one seam in the tail, one follow-up"


# -- the views against a real instance -----------------------------------

def test_every_view_renders_against_a_real_instance(instance_root, tmp_path):
    """Fixtures are hand-written; a real instance is full of nulls and empties.

    `hx show --json` on a fresh instance returns `metrics: null`, no streams, no
    step state and an empty context file, which is exactly the shape the
    hand-written fixtures do not have. The screens have to survive it.
    """
    from .conftest import archive_is_broken, isolated_source

    broken = archive_is_broken(instance_root)
    if broken:
        pytest.skip(broken)

    source = isolated_source(instance_root)
    overrides = {
        "/api/board": source.board(),
        "/api/orders": source.orders(),
        "/api/archive": source.archive(),
        "/api/show/partner": source.show("partner"),
        "/api/show/eng-001": source.show("eng-001"),
    }
    rendered = render(overrides, tmp_path)

    assert rendered["banner"] is None, "no screen failed"

    agent = rendered["views"]["agent"]
    labels = labels_of(agent)
    for name in ("Work item", "Step state", "Context file", "Streams", "Subagents", "Metrics"):
        assert name in labels, name
    assert any(label.startswith("Pane · eng-001") for label in labels)
    assert agent["metrics"] is None, "a fresh instance has no seams yet"
    assert agent["notYet"] > 0, "and the empty sections say so"

    partner = rendered["views"]["partner"]
    assert "PARTNER.md" in partner["headings"]
    assert "tmux attach -t partner" in partner["text"]
    fleet = [row for row in partner["rows"] if row["agent"]]
    assert len(fleet) == len(overrides["/api/board"]["items"])

    ids = [item["id"] for item in overrides["/api/board"]["items"]]
    assert ids == ["eng-001"], "v1 cut: the Partner is not a board item"
    assert [card["agent"] for card in rendered["views"]["board"]["cards"]] == ids
    assert [node["agent"] for node in rendered["views"]["overview"]["graph"]["nodes"]] == [
        "partner", *ids
    ], "the Partner is the root even when it is not a board item"


# -- markdown -------------------------------------------------------------

def test_the_skeleton_partner_md_tables_render_as_tables(tmp_path):
    """`PARTNER.md`'s fleet table was showing as raw pipes."""
    show = show_json("partner")
    show["partner_md"] = SKELETON_PARTNER.read_text()
    partner = render({"/api/show/partner": show}, tmp_path)["views"]["partner"]

    assert "|---|" not in partner["text"], "no separator row leaked through as text"
    assert "| id |" not in partner["text"]
    for header in ("what it is for", "asked in chat", "what landed"):
        assert header in partner["headers"], f"{header!r} is a table header now"


def test_a_table_renders_one_cell_per_header(tmp_path):
    show = show_json("partner")
    show["partner_md"] = (
        "# Fleet\n\n"
        "| id | role | notes |\n"
        "|---|:---:|---:|\n"
        "| eng-001 | engineer | first |\n"
        "| eng-002 | engineer |\n"
    )
    rendered = render({"/api/show/partner": show}, tmp_path)
    rows = [row for row in rendered["views"]["partner"]["rows"] if len(row["cells"]) == 3]
    assert rows[0]["cells"] == ["eng-001", "engineer", "first"]
    assert len(rows[1]["cells"]) == 3, "a short row is padded rather than shifting the columns"
    assert rows[1]["cells"][2] == ""


def test_a_lone_pipe_line_is_still_a_paragraph(tmp_path):
    """Only a header followed by a rule row is a table."""
    show = show_json("partner")
    show["partner_md"] = "Use `a | b` to pipe.\n"
    rendered = render({"/api/show/partner": show}, tmp_path)
    assert "Use a | b to pipe." in rendered["views"]["partner"]["text"]


def test_the_context_file_renders_as_markdown(tmp_path):
    """build-3: it has a fixed section order and is meant to be read, not dumped."""
    show = show_json()
    show["context_file"] = {
        "path": "run/eng-001/eng-001-main.context.md",
        "seam_ts": "2026-09-20T12:50:00Z",
        "text": (
            "# Context for eng-001-main\n\n"
            "## Memory\n_source: `config/eng-001/AGENTS.md`_\n\nPrefer same-directory renames.\n\n"
            "## Tasks\n- [x] Read spec 08.\n- [ ] Refuse an unknown id.\n\n"
            "## Step state\n_none yet_\n"
        ),
    }
    agent = render({"/api/show/eng-001": show}, tmp_path)["views"]["agent"]

    text = agent["text"]
    for heading in ("Context for eng-001-main", "Memory", "Step state"):
        assert heading in text, heading
    assert "_source:" not in text, "the source line is rendered, not shown as raw markdown"
    assert "config/eng-001/AGENTS.md" in [code["text"] for code in agent["code"]]
    context_tasks = [item for item in agent["listItems"] if item["class"].startswith("task")]
    assert len(context_tasks) >= 2, "the `## Tasks` section renders as checkboxes"
    assert not any(block["text"].startswith("# Context for") for block in agent["pre"])


# -- pane wording --------------------------------------------------------

def test_a_pane_with_no_session_and_no_log_says_so(tmp_path):
    show = show_json()
    show["pane"] = {"session": "eng-001", "alive": False, "lines": [], "source": "none", "error": "gone"}
    agent = render({"/api/show/eng-001": show}, tmp_path)["views"]["agent"]
    assert "no session, no log" in agent["text"]
    assert "from the none" not in agent["text"]
    assert "gone" in agent["text"], "the capture error is named"


@pytest.mark.parametrize("source, said", [("session", "from the session"), ("log", "from the log")])
def test_the_other_two_pane_sources_keep_their_wording(tmp_path, source, said):
    show = show_json()
    show["pane"] = {"session": "eng-001", "alive": source == "session",
                    "lines": ["a line"], "source": source, "error": None}
    agent = render({"/api/show/eng-001": show}, tmp_path)["views"]["agent"]
    assert said in agent["text"]


# -- ui-7 item 2: real digests replace the placeholder --------------------

def test_a_closed_stream_shows_the_companions_digest(rendered):
    """build-6 overwrites `_pending companion_` with a real digest."""
    show = show_json()
    closed = [s for s in show["streams"] if not s["open"] and not s["handle"].endswith("-main")]
    assert len(closed) == 2, "the fixture has two closed subagent streams"
    text = rendered["views"]["agent"]["text"].replace("`", "")
    for stream in closed:
        assert stream["digest"].strip() != "_pending companion_"
        for line in stream["digest"].strip().splitlines():
            body = line.lstrip("- ").strip().replace("`", "")
            if body:
                assert body in text, f"{stream['handle']}: {body}"


def test_the_subagents_table_shows_the_real_digest(rendered):
    rows = [row for row in rendered["views"]["agent"]["rows"] if len(row["cells"]) == 4]
    assert rows, "the subagents table"
    digests = [row["cells"][3] for row in rows]
    assert not any("pending companion" in d for d in digests)
    assert any("Surveyed the exit-code assertions" in d for d in digests)


# -- ui-7 item 3: turn.background_tasks -----------------------------------

def test_background_tasks_say_the_agent_stopped_with_work_running(rendered):
    tasks = show_json()["turn"]["background_tasks"]
    assert tasks, "the fixture has background work"
    text = rendered["views"]["agent"]["text"]
    assert "Stopped with work still running" in text
    for task_id in tasks:
        assert task_id in text


def test_an_empty_background_tasks_says_nothing(tmp_path):
    show = show_json()
    show["turn"] = {"ts": "2026-09-20T13:09:40Z", "background_tasks": []}
    agent = render({"/api/show/eng-001": show}, tmp_path)["views"]["agent"]
    assert "Stopped with work still running" not in agent["text"]
    assert "last turn 13:09:40Z" in agent["text"]


def test_no_turn_yet_says_so(tmp_path):
    """`turn` is null until a turn has ended (CONTRACTS.md)."""
    show = show_json()
    show["turn"] = None
    board = board_json()
    for item in board["items"]:
        item["turn_ts"] = None
    agent = render({"/api/show/eng-001": show, "/api/board": board}, tmp_path)["views"]["agent"]
    assert "no turn yet" in agent["text"]
    assert "Stopped with work still running" not in agent["text"]


# -- ui-7 item 4: the Agent page stays recoverable ------------------------

def test_an_unreadable_agent_says_so_in_place(tmp_path):
    """The orchestrator's browser finding: no dead end, and no blanked page."""
    board = board_json()
    # A null override makes that one endpoint fail, as an unknown id would.
    rendered = render({"/api/board": board, "/api/show/eng-001": None}, tmp_path,
                      open_ids=["eng-001"])
    agent = rendered["views"]["agents"]["eng-001"]

    assert "eng-001 could not be read" in agent["text"]
    assert "read failure rather than a missing agent" in agent["text"], (
        "the board still lists it, so say which kind of failure this is"
    )
    assert rendered["banner"] is None, (
        "one unreadable agent no longer takes the whole page down: it is reported in place"
    )
    assert agent["sidebar"]["agents"], "every other id is still one click away"


def test_an_id_the_board_does_not_list_says_so(tmp_path):
    board = board_json()
    board["items"] = [item for item in board["items"] if item["id"] != "eng-001"]
    # Reached from the Orders view: the order is still recorded in tasks.json
    # while the work item behind it has gone.
    rendered = render({"/api/board": board, "/api/show/eng-001": None}, tmp_path,
                      open_ids=["eng-001"])
    agent = rendered["views"]["agents"]["eng-001"]
    assert "The board does not list this id" in agent["text"]
    assert agent["openable"], "the ids that do exist are still offered"


# -- narrow width (CSS only, so asserted as rules rather than pixels) -----

STYLE = STATIC / "style.css"


def css() -> str:
    return STYLE.read_text(encoding="utf-8")


def test_the_board_scrolls_in_its_own_container():
    """Six columns do not fit a phone; the page body must not scroll sideways."""
    text = css()
    block = text.split(".board-wrap {")[1].split("}")[0]
    assert "overflow-x: auto" in block
    assert "grid-template-columns: repeat(6" in text, "one column per state and outcome"


def test_the_sidebar_collapses_before_the_content_does():
    """autodev's shell: the sidebar becomes icons, then hides, as the width drops."""
    text = css()
    assert "@media (max-width: 1150px)" in text
    assert "@media (max-width: 760px)" in text
    narrow = text.split("@media (max-width: 760px)")[1]
    assert ".sidebar" in narrow


def test_every_screen_keeps_a_side_gutter():
    """Nothing may sit flush against a 375 px edge."""
    text = css()
    assert "main {" in text
    main = text.split("\nmain {")[1].split("}")[0]
    assert "padding" in main


def test_the_graph_viewport_scrolls_rather_than_the_page():
    block = css().split(".graph-viewport {")[1].split("}")[0]
    assert "overflow: auto" in block
    assert "height:" in block


# -- build-7's "render the string" ---------------------------------------

def test_an_unknown_state_gets_its_own_column(tmp_path):
    """`handoff/build-to-ui.md` build-7: nothing validates the filename suffix.

    A HarnessAgent may rename its own work item, so a state the six known
    columns do not cover must still put its card on the board rather than drop
    it.
    """
    board = board_json()
    board["items"][0]["state"] = "reviewing"
    board["items"][0]["file"] = "pods/engineers/eng-000-reviewing.md"
    rendered = render({"/api/board": board}, tmp_path)["views"]["board"]
    labels = [column["label"].rstrip("0123456789") for column in rendered["columns"]]
    assert "reviewing" in labels, "the unknown state is a column of its own"
    assert {card["agent"] for card in rendered["cards"]} == {item["id"] for item in board["items"]}


def test_an_unknown_state_reads_as_itself_in_the_table(tmp_path):
    board = board_json()
    board["items"][0]["state"] = "reviewing"
    rendered = render({"/api/board": board}, tmp_path)["views"]["agentTable"]
    row = next(r for r in rendered["rows"] if r["agent"] == board["items"][0]["id"])
    assert row["cells"][3] == "reviewing"
