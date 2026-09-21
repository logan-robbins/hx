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

from hx.errors import ValidationError
from hx.orders import parse_order
from .conftest import _serve, isolated_source, manifest, pack_instance_is_stale
from .test_views_js import render

REPO = Path(__file__).resolve().parents[2]
SCENARIOS = REPO / "tests" / "scenario"
PACK_M8 = SCENARIOS / "m8"


class StalePack(Exception):
    """The scenario pack is still pre-cut; skip rather than fail (ui-7)."""


#: What a pack mid-cut raises: an order with `after:` frontmatter, a `tasks.json`
#: with a field hx dropped, or a `packlib` whose `State` tuple has changed width
#: while its `STEPS` have not. All of them mean "wait for the gtm lane", never
#: "the UI is wrong" — reported in handoff/ui-to-gtm.md.
PRE_CUT = (StalePack, ValidationError, ValueError, TypeError, KeyError, IndexError)

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

    # `packlib` writes `tasks.json` in the v1 shape itself now, so nothing here
    # rewrites it. The order text it records is a placeholder rather than the
    # pack's real order, which these structural tests do not depend on; the
    # verbatim-order assertions live in the fixture tests.
    return root


def expected_board(pack: str, stem: str) -> list[dict]:
    """The gtm lane's checked-in `hx board` text, parsed into columns.

    v1 cut (`hx.board.render_text`): `id  pod  state  outcome  <dispatched>
    alive|dead  subagents=N  context=N  seams=N`, with `-` for an absent value.
    No `after` column and no Partner row, because neither exists any more.
    """
    rows = []
    for line in (SCENARIOS / pack / "expected" / f"{stem}.txt").read_text().strip().splitlines():
        parts = line.split()
        fields = dict(part.split("=", 1) for part in parts if "=" in part)
        item_id, pod, state, outcome, dispatched, alive = parts[:6]
        rows.append({
            "id": item_id,
            "pod": pod,
            "state": state,
            "outcome": None if outcome == "-" else outcome,
            "dispatched": None if dispatched == "-" else dispatched,
            "session_alive": alive == "alive",
            "open_subagents": int(fields.get("subagents", 0)),
        })
    return rows


# -- every step of both packs --------------------------------------------

@pytest.fixture(scope="session")
def step_roots(tmp_path_factory):
    """Each step built once, shared by the tests below."""
    pytest.importorskip("tests.scenario.packlib", reason="the scenario packs are the gtm lane's")
    roots = {}
    base = tmp_path_factory.mktemp("hx-steps")
    try:
        for pack, stem, states, worker_pod in ALL_STEPS:
            roots[(pack, stem)] = build_step(base / f"{pack}-{stem}", pack, states, worker_pod)
    except PRE_CUT as exc:
        pytest.skip(f"the scenario pack is still pre-cut: {type(exc).__name__}: {exc}")
    stale = pack_instance_is_stale(next(iter(roots.values())))
    if stale:
        pytest.skip(stale)
    return roots


@pytest.mark.parametrize("pack,stem,states,worker_pod", ALL_STEPS, ids=STEP_IDS)
def test_the_board_matches_the_packs_expected_board(step_roots, pack, stem, states, worker_pod):
    board = isolated(step_roots[(pack, stem)]).board()
    rows = {item["id"]: item for item in board["items"]}
    expected = expected_board(pack, stem)

    assert [item["id"] for item in board["items"]] == [row["id"] for row in expected], (
        "by id; the Partner is not an item (v1 cut)"
    )
    for row in expected:
        item = rows[row["id"]]
        assert item["pod"] == row["pod"]
        assert item["state"] == row["state"]
        assert item["outcome"] == row["outcome"]
        assert item["open_subagents"] == row["open_subagents"]
        assert (item["dispatched"] is not None) is (row["dispatched"] is not None)
        assert item["session_alive"] is False, "the private tmux server has no sessions"


@pytest.mark.parametrize("pack,stem,states,worker_pod", ALL_STEPS, ids=STEP_IDS)
def test_orders_lists_every_dispatched_id(step_roots, pack, stem, states, worker_pod):
    """v1 cut: one entry per `tasks.json` id, no graph — sequencing is the
    Partner's own judgement now, so there is no dependency to draw."""
    source = isolated(step_roots[(pack, stem)])
    orders = source.orders()
    assert set(orders) == {"root_abs", "ts", "orders"}
    assert "graph" not in orders

    # `hx orders` reads `tasks.json`, which is the control plane. That is not
    # the same as the board's `dispatched`, which comes from the work item's
    # frontmatter — an item can carry a dispatch stamp in its body without
    # having a `tasks.json` record, and the pack's idle items do.
    tasks = json.loads((step_roots[(pack, stem)] / "tasks.json").read_text())
    assert {entry["id"] for entry in orders["orders"]} == set(tasks)


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
    # v1 cut: the Partner is not a board item, but its view still exists and
    # still reads `/api/show/partner`.
    overrides["/api/show/partner"] = source.show("partner")

    rendered = render(overrides, tmp_path, open_ids=ids)

    assert rendered["banner"] is None, f"{pack} {stem}: a screen failed"
    # ui-8: the board is cards in state columns and the fleet is the graph; the
    # Agent page of spec 16.2 is the drawer, opened from either.
    assert sorted(card["agent"] for card in rendered["views"]["board"]["cards"]) == sorted(ids)
    assert [node["agent"] for node in rendered["views"]["overview"]["graph"]["nodes"]] == [
        "partner", *ids
    ]
    assert sorted(row["agent"] for row in rendered["views"]["agentTable"]["rows"]) == sorted(ids)
    for item_id in ids:
        agent = rendered["views"]["agents"][item_id]
        assert agent["headings"] == [item_id]
        labels = [entry["label"] for entry in agent["labels"]]
        assert "Goal" in labels
        session_labels = [entry["label"] for entry in rendered["views"]["sessions"][item_id]["labels"]]
        assert any(label.startswith(f"Pane · {item_id}") for label in session_labels)
    assert "PARTNER.md" in rendered["views"]["partner"]["headings"]
    fleet = [row for row in rendered["views"]["partner"]["rows"] if row["agent"]]
    assert len(fleet) == len(ids), "the whole fleet is on the Partner page"


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
    try:
        root = build_step(tmp_path_factory.mktemp("hx-m8"), "m8", M8_DECISION, "engineers")
    except PRE_CUT as exc:
        pytest.skip(f"the scenario pack is still pre-cut: {type(exc).__name__}: {exc}")
    stale = pack_instance_is_stale(root)
    if stale:
        pytest.skip(stale)
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
    """v1 cut: no `orders/partner.md` — the Partner has no work item."""
    placed = sorted(p.name for p in (m8_root / "orders").glob("*.md"))
    assert placed == sorted(p.name for p in (PACK_M8 / "orders").glob("*.md"))
    assert "partner.md" not in placed
    for item_id in ("eng-001", "eng-002"):
        agents = m8_root / "config" / item_id / "AGENTS.md"
        assert agents.is_file()
        assert "## UPDATES BELOW ONLY" in agents.read_text()


def test_show_eng_002_carries_the_decision(m8):
    """v1 cut: a `task` block with no `after` — sequencing is the Partner's."""
    status, show = m8.client.json("/api/show/eng-002")
    assert status == 200
    assert show["state"] == "complete"
    assert show["task"]["outcome"] == "decision"
    assert "after" not in show["task"]


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
