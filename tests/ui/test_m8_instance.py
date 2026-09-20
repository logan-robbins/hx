"""The UI against real instances in the scenario packs' shapes.

`tests/scenario/m8/` and `m8b/` are the gtm lane's packs — the data spec 13's M8
runs on. Every observation point in both packs is built here and put through the
UI: the board and orders documents, and the views rendered by the real
`static/app.js`.

**Every instance is hermetic.** `hx board` matches a live tmux session by the id
itself, so a scratch instance on this machine would otherwise report the build
lane's real `partner` session as its own. Each instance is read through a private
tmux server (`HX_TMUX`, handoff/build-to-ui.md) that has no sessions at all, so
what the board says about liveness is a fact about the fixture and nothing else.
Nothing here can reach, or send to, a session another lane is running.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from hx.orders import parse_order
from .conftest import _serve, isolated_source, manifest
from .test_views_js import render

REPO = Path(__file__).resolve().parents[2]
SCENARIOS = REPO / "tests" / "scenario"

PANE_LOG = (
    "\x1b[2m> hx task\x1b[0m\n"
    "I have built everything that does not depend on the open question and committed it.\n"
    "\x1b[1;33mWriting the question into ## Open decision and stopping.\x1b[0m\n"
    "> hx complete decision\n"
    "HX-COMPLETE eng-002 decision\n"
)


def isolated(root: Path) -> InstanceSource:
    return isolated_source(root)


def pack_steps():
    """Every observation point of both packs: (pack, stem, states, worker_pod)."""
    from tests.scenario import test_m8_pack, test_m8b_pack

    for module, pack in ((test_m8_pack, "m8"), (test_m8b_pack, "m8b")):
        for stem, states in module.STEPS:
            yield pack, stem, states, module.POD


ALL_STEPS = list(pack_steps())
STEP_IDS = [f"{pack}-{stem}" for pack, stem, _, _ in ALL_STEPS]


def build_step(root: Path, pack: str, states: dict, worker_pod: str) -> Path:
    """An instance in that step's shape, with the pack's real orders and personas."""
    from tests.scenario import packlib

    packlib.build_instance(root, states, worker_pod=worker_pod)
    source = SCENARIOS / pack

    orders = root / "orders"
    orders.mkdir(parents=True, exist_ok=True)
    for order_file in sorted((source / "orders").glob("*.md")):
        shutil.copy(order_file, orders / order_file.name)
    for agents in sorted((source / "config").glob("*/AGENTS.md")):
        target = root / "config" / agents.parent.name
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy(agents, target / "AGENTS.md")

    # Record what `hx dispatch` records: the parsed `## Order` text, not the file.
    tasks = json.loads((root / "tasks.json").read_text())
    for item_id, record in tasks.items():
        order_file = orders / f"{item_id}.md"
        if order_file.is_file():
            parsed = parse_order(order_file)
            record["order"] = parsed.text
            record["after"] = parsed.after
    (root / "tasks.json").write_text(json.dumps(tasks, indent=2) + "\n", encoding="utf-8")
    return root


def expected_board(pack: str, stem: str) -> list[dict]:
    """The gtm lane's checked-in `hx board` text, parsed into columns (spec 08)."""
    rows = []
    for line in (SCENARIOS / pack / "expected" / f"{stem}.txt").read_text().strip().splitlines():
        path, after, outcome, subagents, goal = line.split("  ")
        rows.append({
            "file": path,
            "id": Path(path).name.rsplit("-", 1)[0],
            "after": [] if after == "-" else after.split(","),
            "outcome": None if outcome == "-" else outcome,
            "open_subagents": int(subagents),
            "has_goal": goal != "-",
        })
    return rows


# -- every step of both packs --------------------------------------------

@pytest.fixture(scope="session")
def step_roots(tmp_path_factory):
    """Each step built once, shared by the tests below."""
    pytest.importorskip("tests.scenario.packlib", reason="the scenario packs are the gtm lane's")
    roots = {}
    base = tmp_path_factory.mktemp("hx-steps")
    for pack, stem, states, worker_pod in ALL_STEPS:
        roots[(pack, stem)] = build_step(base / f"{pack}-{stem}", pack, states, worker_pod)
    return roots


@pytest.mark.parametrize("pack,stem,states,worker_pod", ALL_STEPS, ids=STEP_IDS)
def test_the_board_matches_the_packs_expected_board(step_roots, pack, stem, states, worker_pod):
    board = isolated(step_roots[(pack, stem)]).board()
    rows = {item["id"]: item for item in board["items"]}
    expected = expected_board(pack, stem)

    assert [item["id"] for item in board["items"]] == [row["id"] for row in expected], (
        "partner first, then by id (CONTRACTS.md)"
    )
    for row in expected:
        item = rows[row["id"]]
        assert item["file"] == row["file"]
        assert item["after"] == row["after"]
        assert item["outcome"] == row["outcome"]
        assert item["open_subagents"] == row["open_subagents"]
        assert (item["goal_ts"] is not None) is row["has_goal"]
        assert item["session_alive"] is False, "the private tmux server has no sessions"


@pytest.mark.parametrize("pack,stem,states,worker_pod", ALL_STEPS, ids=STEP_IDS)
def test_the_after_graph_matches_the_expected_board(step_roots, pack, stem, states, worker_pod):
    """The graph the Orders view draws, against the board the pack expects.

    The two are not the same list, and should not be. The board's `after` column
    comes from `tasks.json`, so it is empty until an item is dispatched; the
    Orders view reads the order *files*, so it shows a dependency the Partner has
    written but not yet dispatched. At M8 step 1 that is the whole difference:
    `orders/eng-002.md` already declares `after: [eng-001]` while the board shows
    `-`. So the graph must contain every edge the board claims, and every edge it
    draws must be one the orders document itself declares.
    """
    orders = isolated(step_roots[(pack, stem)]).orders()
    expected = expected_board(pack, stem)

    declared = sorted(
        (dep, entry["id"]) for entry in orders["orders"] for dep in entry["after"]
    )
    drawn = sorted((edge["from"], edge["to"]) for edge in orders["graph"]["edges"])
    assert drawn == declared, "the graph is exactly what the orders document declares"

    from_board = {(dep, row["id"]) for row in expected for dep in row["after"]}
    assert from_board <= set(drawn), "no dependency the board shows is missing from the graph"

    outcomes = {row["id"]: row["outcome"] for row in expected}
    for edge in orders["graph"]["edges"]:
        assert edge["met"] is (outcomes.get(edge["from"]) == "done")


@pytest.mark.parametrize("pack,stem,states,worker_pod", ALL_STEPS, ids=STEP_IDS)
def test_every_view_renders_at_every_step(step_roots, pack, stem, states, worker_pod, tmp_path):
    """The board, the orders, the archive, the Partner, and each agent in turn."""
    source = isolated(step_roots[(pack, stem)])
    ids = [item["id"] for item in source.board()["items"]]
    overrides = {
        "/api/board": source.board(),
        "/api/orders": source.orders(),
        "/api/archive": source.archive(),
    }
    for item_id in ids:
        overrides[f"/api/show/{item_id}"] = source.show(item_id)

    workers = [item_id for item_id in ids if item_id != "partner"]
    rendered = render(overrides, tmp_path, open_ids=workers)

    assert rendered["banner"] is None, f"{pack} {stem}: a view failed"
    assert len(rendered["views"]["board"]["rows"]) == len(ids)
    for item_id in workers:
        agent = rendered["views"]["agents"][item_id]
        assert agent["headings"][0] == f"{item_id} · work item"
        assert agent["headings"][-1] == f"pane · {item_id}"
    assert "PARTNER.md" in rendered["views"]["partner"]["headings"]
    board_rows = [row for row in rendered["views"]["partner"]["rows"] if len(row["cells"]) == 11]
    assert len(board_rows) == len(ids), "the whole board is on the Partner view"


# -- the M8 decision point, in detail ------------------------------------

M8_DECISION = next(
    (states for stem, states in __import__(
        "tests.scenario.test_m8_pack", fromlist=["STEPS"]
    ).STEPS if stem == "04-eng-002-decision"),
    None,
)


@pytest.fixture(scope="module")
def m8_root(tmp_path_factory):
    """M8 step 4, with a pane log so the dead-session fallback is exercised."""
    pytest.importorskip("tests.scenario.packlib", reason="the scenario packs are the gtm lane's")
    root = build_step(tmp_path_factory.mktemp("hx-m8"), "m8", M8_DECISION, "engineers")
    log = root / "logs" / "eng-002" / "eng-002-pane.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(PANE_LOG, encoding="utf-8")

    before = manifest(root)
    yield root
    after = manifest(root)
    changed = sorted(
        name for name in set(before) | set(after)
        if before.get(name) != after.get(name) and name != "run/ui-token"
    )
    assert not changed, f"the UI wrote to the M8 instance: {changed}"


@pytest.fixture(scope="module")
def m8(m8_root):
    server = _serve(isolated(m8_root))
    handle = next(server)
    try:
        yield handle
    finally:
        server.close()


def test_the_pack_is_placed(m8_root):
    assert sorted(p.name for p in (m8_root / "orders").glob("*.md")) == [
        "eng-001.md", "eng-002.addendum.md", "eng-002.md", "partner.md",
    ]
    for item_id in ("eng-001", "eng-002"):
        agents = m8_root / "config" / item_id / "AGENTS.md"
        assert agents.is_file()
        assert "## UPDATES BELOW ONLY" in agents.read_text()


def test_show_eng_002_carries_the_decision(m8):
    status, show = m8.client.json("/api/show/eng-002")
    assert status == 200
    assert show["state"] == "complete"
    assert show["task"]["outcome"] == "decision"
    assert show["task"]["after"] == ["eng-001"]
    assert "--lang" in show["task"]["order"], "the pack's real order"


def test_the_pane_falls_back_to_the_log(m8):
    _, show = m8.client.json("/api/show/eng-002")
    pane = show["pane"]
    assert pane["alive"] is False
    assert pane["source"] == "log"
    assert pane["lines"] == [
        "> hx task",
        "I have built everything that does not depend on the open question and committed it.",
        "Writing the question into ## Open decision and stopping.",
        "> hx complete decision",
        "HX-COMPLETE eng-002 decision",
    ], "ANSI stripped, in order"


def test_a_partner_with_no_log_says_the_session_is_gone(m8):
    _, show = m8.client.json("/api/show/partner")
    assert show["pane"]["source"] == "none"
    assert show["pane"]["lines"] == []


def test_archive_is_empty_at_this_point(m8):
    status, archive = m8.client.json("/api/archive")
    assert status == 200
    assert all(not item["bench"] and not item["archive"] for item in archive["items"])


# -- the standing rule about other lanes' sessions -----------------------

def test_the_wake_cannot_reach_a_session_this_lane_did_not_create(m8, m8_root):
    """ui-5 item 7: never send into a live tmux session on this machine.

    `hx wake` reaches the Partner only through `run/partner/socket.json` inside
    `HARNESS_ROOT`. A scratch instance has none, so there is nothing to connect
    to however many real `partner` sessions are running here.
    """
    assert not (m8_root / "run" / "partner" / "socket.json").exists()
    response = m8.client.post("/api/partner/wake", {"text": "eng-002 needs a decision"})
    assert response.status == 200
    assert json.loads(response.body) == {"delivered": False, "status": "no-socket"}


def test_the_board_of_a_scratch_instance_is_read_through_a_private_tmux(m8_root):
    """Why every instance here is isolated, asserted rather than assumed.

    Read through the default server, this fixture's board would report whatever
    sessions happen to be running under those ids on this machine — the build
    lane runs a real one called `partner`. Through the private server it reports
    the fixture.
    """
    through_private = isolated(m8_root).board()
    assert all(item["session_alive"] is False for item in through_private["items"])
