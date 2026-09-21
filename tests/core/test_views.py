"""`hx show`, `hx orders`, `hx archive`, `hx read`, `hx task` against CONTRACTS.md."""

from __future__ import annotations

import json

import pytest

from .conftest import wait_for

SHOW_KEYS = {
    "id", "pod", "role", "state", "file", "work_item", "task", "persona_path", "step_state",
    "context_file", "streams", "subagents", "metrics", "pane", "archive", "bench",
}
ORDERS_KEYS = {"root_abs", "ts", "orders", "graph", "errors"}
ORDER_ENTRY_KEYS = {
    "id", "pod", "path", "after", "order", "addenda", "record", "state", "ready",
    "waiting_on", "file_matches_record",
}
ARCHIVE_KEYS = {"root_abs", "ts", "items", "errors"}


def dispatched(instance, hx, orders, item_id="eng-001", **kwargs):
    orders(item_id, **kwargs)
    result = hx("dispatch", item_id, f"orders/{item_id}.md", cwd=instance)
    assert result.returncode == 0, result.stderr
    wait_for(
        lambda: "/goal" in (instance / "run" / item_id / "fake-input.log").read_text(),
        what=f"{item_id}'s goal",
    )
    return instance


# --- hx show ----------------------------------------------------------------------------------


def test_show_json_matches_contracts_on_a_dispatched_item(instance, hx, launched, orders):
    launched("eng-001")
    dispatched(instance, hx, orders, order="Stream the importer.")

    result = hx("show", "eng-001", "--json")
    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    assert set(document) == SHOW_KEYS, set(document) ^ SHOW_KEYS

    assert document["id"] == "eng-001"
    assert document["pod"] == "engineers" and document["role"] == "engineer"
    assert document["state"] == "working"
    assert document["file"] == "pods/engineers/eng-001-working.md"
    assert document["work_item"]["frontmatter"]["id"] == "eng-001"
    assert "Stream the importer." in document["work_item"]["body"]
    assert "Stream the importer." in document["task"]["order"]
    assert document["task"]["after"] == [] and document["task"]["addenda"] == []
    assert document["task"]["outcome"] is None and document["task"]["completed"] is None
    assert document["task"]["dispatched"]
    assert document["persona_path"] == "run/eng-001/persona.md"
    assert document["pane"]["session"] == "eng-001" and document["pane"]["alive"] is True
    assert document["pane"]["lines"]


def test_show_nulls_what_later_milestones_produce(instance, hx, launched, orders):
    launched("eng-001")
    dispatched(instance, hx, orders)
    document = json.loads(hx("show", "eng-001", "--json").stdout)
    assert document["step_state"] == {}, "the Companion writes step state at M5"
    assert document["context_file"]["text"] is None, "hx compose lands at M2"
    assert document["context_file"]["path"] == "run/eng-001/eng-001-main.context.md"
    assert document["metrics"] is None, "hx metrics lands at M7"
    assert document["subagents"] == {}
    assert document["archive"] and document["bench"] == []


def test_show_reads_the_streams(instance, hx, launched, orders):
    launched("eng-001")
    dispatched(instance, hx, orders)
    logs = instance / "logs" / "eng-001"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "eng-001-main.jsonl").write_text(
        "".join(json.dumps({"seq": n, "event": "post_tool"}) + "\n" for n in range(1, 61))
    )
    (logs / "eng-001-s001-closed.jsonl").write_text('{"seq": 1, "event": "open"}\n')
    state = instance / "state" / "eng-001"
    state.mkdir(parents=True, exist_ok=True)
    (state / "eng-001-s001.digest.md").write_text("what s001 found\n")

    document = json.loads(hx("show", "eng-001", "--json").stdout)
    by_handle = {stream["handle"]: stream for stream in document["streams"]}
    assert by_handle["eng-001-main"]["records"] == 60
    assert len(by_handle["eng-001-main"]["tail"]) == 50, "CONTRACTS.md: the last 50 records"
    assert by_handle["eng-001-main"]["open"] is True
    assert by_handle["eng-001-s001"]["open"] is False
    assert by_handle["eng-001-s001"]["digest"] == "what s001 found\n"


def test_show_for_the_partner_carries_partner_md(instance, hx, launched):
    launched("partner")
    (instance / "PARTNER.md").write_text("# Partner state\n\nnothing yet.\n")
    document = json.loads(hx("show", "partner", "--json").stdout)
    assert document["partner_md"] == "# Partner state\n\nnothing yet.\n"


def test_show_of_an_unknown_id_is_not_found(instance, hx):
    result = hx("show", "eng-404", "--json")
    assert result.returncode == 2
    assert "unknown id" in result.stderr


def test_show_text_form(instance, hx, launched, orders):
    launched("eng-001")
    dispatched(instance, hx, orders)
    result = hx("show", "eng-001")
    assert result.returncode == 0
    assert result.stdout.startswith("eng-001  working  pod=engineers  role=engineer")
    assert "## Tasks" in result.stdout


def test_show_falls_back_to_the_pane_log_for_a_dead_session(instance, hx, launched, orders, tmux_server):
    import subprocess

    launched("eng-001")
    dispatched(instance, hx, orders)
    log = instance / "logs" / "eng-001" / "eng-001-pane.log"
    wait_for(lambda: log.is_file() and log.stat().st_size > 0, what="the pane log to fill")
    subprocess.run([*tmux_server, "kill-session", "-t", "=eng-001"], check=True)

    document = json.loads(hx("show", "eng-001", "--json").stdout)
    assert document["pane"]["alive"] is False
    assert document["pane"]["lines"], "the pane log is the fallback for a dead session"


# --- hx orders ---------------------------------------------------------------------------------


def test_orders_json_matches_contracts(instance, hx, launched, orders, agent):
    agent("eng-002")
    launched("eng-001", "eng-002")
    orders("eng-001")
    orders("eng-002", after=["eng-001"])
    assert hx("dispatch", "eng-001", "orders/eng-001.md", "eng-002", "orders/eng-002.md",
              cwd=instance).returncode == 0

    result = hx("orders", "--json")
    assert result.returncode == 0, result.stderr
    view = json.loads(result.stdout)
    assert set(view) == ORDERS_KEYS
    assert view["root_abs"] == str(instance) and view["ts"].endswith("Z")
    assert view["errors"] == []

    by_id = {entry["id"]: entry for entry in view["orders"]}
    assert set(by_id) == {"eng-001", "eng-002"}
    for entry in view["orders"]:
        assert set(entry) == ORDER_ENTRY_KEYS, set(entry) ^ ORDER_ENTRY_KEYS

    assert by_id["eng-001"]["state"] == "working"
    assert by_id["eng-001"]["ready"] is True and by_id["eng-001"]["waiting_on"] == []
    assert by_id["eng-002"]["state"] == "queued"
    assert by_id["eng-002"]["ready"] is False
    assert by_id["eng-002"]["waiting_on"] == ["eng-001"]
    assert by_id["eng-002"]["after"] == ["eng-001"]
    assert by_id["eng-002"]["record"]["outcome"] is None
    assert set(by_id["eng-002"]["record"]) == {
        "order", "after", "addenda", "outcome", "dispatched", "completed"
    }

    assert view["graph"]["edges"] == [{"from": "eng-001", "to": "eng-002", "met": False}]
    nodes = {node["id"]: node for node in view["graph"]["nodes"]}
    assert nodes["eng-002"]["state"] == "queued" and nodes["eng-002"]["ready"] is False


def test_orders_flags_a_file_edited_after_dispatch(instance, hx, launched, orders):
    launched("eng-001")
    dispatched(instance, hx, orders, order="The original order.")
    view = json.loads(hx("orders", "--json").stdout)
    assert view["orders"][0]["file_matches_record"] is True

    orders("eng-001", order="Edited after dispatch.")
    view = json.loads(hx("orders", "--json").stdout)
    assert view["orders"][0]["file_matches_record"] is False


def test_an_order_never_dispatched_has_a_null_record(instance, hx, launched, orders):
    launched("eng-001")
    orders("eng-001")
    view = json.loads(hx("orders", "--json").stdout)
    entry = view["orders"][0]
    assert entry["record"] is None
    assert entry["state"] is None and entry["file_matches_record"] is None


def test_orders_carries_the_addendum(instance, hx, launched, orders):
    launched("eng-001")
    dispatched(instance, hx, orders)
    assert hx("complete", "blocked", harness_id="eng-001").returncode == 0
    (instance / "orders" / "eng-001.addendum.md").write_text("Do it the other way.\n")
    assert hx("resume", "eng-001", "orders/eng-001.addendum.md", cwd=instance).returncode == 0

    entry = json.loads(hx("orders", "--json").stdout)["orders"][0]
    assert len(entry["addenda"]) == 1
    assert entry["addenda"][0]["path"] == "orders/eng-001.addendum.md"
    assert "Do it the other way." in entry["addenda"][0]["text"]
    assert entry["record"]["addenda"][0]["text"] == "Do it the other way."


# --- hx archive ---------------------------------------------------------------------------------


def test_archive_json_matches_contracts(instance, hx, launched, orders):
    launched("eng-001")
    dispatched(instance, hx, orders)
    assert hx("complete", "done", harness_id="eng-001").returncode == 0
    assert hx("bench", "eng-001").returncode == 0

    result = hx("archive", "--json")
    assert result.returncode == 0, result.stderr
    view = json.loads(result.stdout)
    assert set(view) == ARCHIVE_KEYS
    by_id = {item["id"]: item for item in view["items"]}
    assert set(by_id["eng-001"]) == {"id", "pod", "bench", "archive"}

    assert len(by_id["eng-001"]["bench"]) == 1
    bench = by_id["eng-001"]["bench"][0]
    assert set(bench) == {"ts", "path", "digest"}
    assert bench["path"].startswith("pods/engineers/archive/eng-001-")
    assert bench["digest"] == "pending companion"

    assert len(by_id["eng-001"]["archive"]) == 1
    assert by_id["eng-001"]["archive"][0]["path"].startswith("archive/eng-001/")


def test_archive_of_a_fresh_instance_has_empty_lists(instance, hx, launched):
    launched("eng-001")
    view = json.loads(hx("archive", "--json").stdout)
    by_id = {item["id"]: item for item in view["items"]}
    assert by_id["eng-001"]["bench"] == [] and by_id["eng-001"]["archive"] == []


def test_show_and_archive_agree(instance, hx, launched, orders):
    """CONTRACTS.md: `hx archive` is the whole-fleet form of what `hx show` returns per id."""
    launched("eng-001")
    dispatched(instance, hx, orders)
    assert hx("complete", "done", harness_id="eng-001").returncode == 0
    assert hx("bench", "eng-001").returncode == 0

    document = json.loads(hx("show", "eng-001", "--json").stdout)
    view = json.loads(hx("archive", "--json").stdout)
    entry = {item["id"]: item for item in view["items"]}["eng-001"]
    assert entry["bench"] == document["bench"]
    assert entry["archive"] == document["archive"]


# --- hx read and hx task ---------------------------------------------------------------------


def test_read_prints_the_digest_and_the_open_decision(instance, hx, launched, orders):
    launched("eng-001")
    dispatched(instance, hx, orders)
    work_item = instance / "pods" / "engineers" / "eng-001-working.md"
    work_item.write_text(
        work_item.read_text().replace("## Open decision", "## Open decision\n\nFlat list or a map?")
    )
    assert hx("complete", "decision", harness_id="eng-001").returncode == 0

    result = hx("read", "eng-001")
    assert result.returncode == 0, result.stderr
    assert "## Digest" in result.stdout
    # spec 10's final pass: for `decision`, the question comes first, because that is what the
    # Partner's addendum has to answer.
    digest = result.stdout.split("## Digest", 1)[1]
    assert digest.lstrip().startswith("**Decision needed:**")
    assert "## Open decision" in result.stdout and "Flat list or a map?" in result.stdout


def test_read_full_prints_the_whole_body(instance, hx, launched, orders):
    launched("eng-001")
    dispatched(instance, hx, orders, order="The whole order.")
    result = hx("read", "eng-001", "--full")
    assert result.returncode == 0
    assert "The whole order." in result.stdout and "## Standing instructions" in result.stdout


def test_read_refuses_an_item_that_has_not_completed(instance, hx, launched, orders):
    launched("eng-001")
    dispatched(instance, hx, orders)
    result = hx("read", "eng-001")
    assert result.returncode == 1 and "not `complete`" in result.stderr


def test_task_prints_the_order_and_its_addenda(instance, hx, launched, orders):
    launched("eng-001")
    dispatched(instance, hx, orders, order="The dispatched order.")
    result = hx("task", harness_id="eng-001")
    assert result.returncode == 0, result.stderr
    assert "The dispatched order." in result.stdout and "### Checks" in result.stdout

    assert hx("complete", "blocked", harness_id="eng-001").returncode == 0
    (instance / "orders" / "eng-001.addendum.md").write_text("And also this.\n")
    assert hx("resume", "eng-001", "orders/eng-001.addendum.md", cwd=instance).returncode == 0

    result = hx("task", harness_id="eng-001")
    assert "The dispatched order." in result.stdout
    assert "## Order addendum" in result.stdout and "And also this." in result.stdout


def test_task_before_a_dispatch_says_so(instance, hx, launched):
    launched("eng-001")
    result = hx("task", harness_id="eng-001")
    assert result.returncode == 2 and "not been dispatched" in result.stderr
