"""`hx plan partition` reproduces a hand-partitioned synthetic repository.

Four scopes with known fan-in: `round_cents` is called from billing and ledger (a
provider); `tax` and `report` are shared inside one scope (built once, by the smaller
caller); a merge rule pairs ledger/close.py with `report`; and the jobs scope's smallest
callers need each other, so their two families are one unit. docs/ lies outside every
scope and adds no fan-in.
"""

import json
import tempfile
import threading
from pathlib import Path

import pytest

from hx import appmap, code_index, code_snapshot, planning, plan_partition, repo_service
from hx.continuity_store import ContinuityStore
from hx.errors import ValidationError
from .test_appmap import git

SOURCES = {
    "shared/money.py": "def round_cents(x):\n    return fmt(x)\n",
    "shared/fmt.py": "def fmt(x):\n    return x\n",
    "billing/refund.py": "def refund():\n    return tax() + round_cents(2)\n",
    "billing/invoice.py": "def invoice():\n    return tax() + round_cents(1)\n# Invoices total every line.\n",
    "billing/tax.py": "def tax():\n    return 0\n",
    "ledger/post.py": "def post():\n    return round_cents(3) + journal()\n",
    "ledger/journal.py": "def journal():\n    return 1\n",
    "ledger/report.py": "def report():\n    return 2\n",
    "ledger/audit.py": "def audit():\n    return report()\n",
    "ledger/close.py": "def close():\n    return report() + 1\n# Closing posts the period.\n",
    "jobs/v.py": "def v():\n    return u()\n",
    "jobs/p.py": "def p():\n    return u() + s()\n",
    "jobs/q.py": "def q():\n    return s() + v() + 0\n",
    "jobs/u.py": "def u():\n    return 0\n",
    "jobs/s.py": "def s():\n    return 0\n",
    "docs/tool.py": "def tool():\n    return round_cents(4)\n",
}
SCOPES = ["billing", "ledger", "shared", "jobs"]
MERGE = [{"id": "close-report", "kind": "calls", "from": {"prefix": "ledger", "suffix": "close.py"},
          "to": {"name": "report"}}]
POLICY = {"reviewer": "qa-001", "required_checks": ["unit"], "role": "backend-engineer"}


@pytest.fixture
def partitioned(tmp_path):
    repo = tmp_path / "repo"
    for path, text in SOURCES.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    git(repo, "init", "-q")
    info = appmap.initialize(repo)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "synthetic partition")
    with ContinuityStore(tmp_path / "ledger") as store:
        (store.root / "config").mkdir(exist_ok=True)
        (store.root / "config/hx.json").write_text(json.dumps({"partition": {"merge": MERGE}}))
        (store.root / "config/planning.json").write_text(json.dumps(POLICY))
        code_index.index_repo(store.root, repo)
        yield store, repo.resolve(), info


def test_partition_reproduces_providers_families_pairs_and_waves(partitioned, tmp_path, capsys, monkeypatch):
    store, repo, info = partitioned
    # The link query reads postings only: no global JSON views.
    activate = code_snapshot.activate

    def selected(*args, **kwargs):
        result = activate(*args, **kwargs)
        args[0].create_function("hx_unpack", 1, lambda value: pytest.fail("partition walked global JSON views"))
        return result

    monkeypatch.setattr(code_snapshot, "activate", selected)
    out = tmp_path / "plan.json"
    assert planning.main(["partition", "--repo", str(repo), "--scope", *SCOPES, "--out", str(out)], store.root, env={}) == 0
    report = json.loads(capsys.readouterr().out)
    monkeypatch.undo()

    assert (report["repository"], report["scopes"], report["files"], report["truncated"]) == (
        info["repo_id"], sorted(SCOPES), 15, False)
    [provider] = report["providers"]
    assert {key: provider[key] for key in ("path", "name", "qualified", "fan_in", "scopes")} == {
        "path": "shared/money.py", "name": "round_cents", "qualified": "round_cents", "fan_in": 3,
        "scopes": ["billing", "ledger"]}
    assert {family["head"]: family["files"] for family in report["families"]} == {
        "shared/money.py": ["shared/fmt.py", "shared/money.py"],
        "billing/refund.py": ["billing/refund.py", "billing/tax.py"],
        "billing/invoice.py": ["billing/invoice.py"],
        "ledger/post.py": ["ledger/journal.py", "ledger/post.py"],
        "ledger/audit.py": ["ledger/audit.py", "ledger/report.py"],
        "ledger/close.py": ["ledger/close.py"],
        "jobs/p.py": ["jobs/p.py", "jobs/s.py"],
        "jobs/q.py": ["jobs/q.py", "jobs/u.py", "jobs/v.py"],
    }
    assert [f["head"] for f in report["families"] if f["provider"]] == ["shared/money.py"]
    assert report["pairs"] == [{"rule": "close-report", "from_path": "ledger/close.py",
                                "to_path": "ledger/report.py", "name": "report"}]
    assert report["cycles"] == [{"unit": "jobs.p.py", "heads": ["jobs/p.py", "jobs/q.py"]}]
    units = {unit["id"]: unit for unit in report["units"]}
    assert {name: unit["files"] for name, unit in units.items()} == {
        "shared.money.py": ["shared/fmt.py", "shared/money.py"],
        "billing.refund.py": ["billing/refund.py", "billing/tax.py"],
        "billing.invoice.py": ["billing/invoice.py"],
        "ledger.post.py": ["ledger/journal.py", "ledger/post.py"],
        "ledger.audit.py": ["ledger/audit.py", "ledger/close.py", "ledger/report.py"],
        "jobs.p.py": ["jobs/p.py", "jobs/q.py", "jobs/s.py", "jobs/u.py", "jobs/v.py"],
    }
    assert units["billing.invoice.py"]["needs"] == [{"unit": "billing.refund.py", "need": "hard"},
                                                    {"unit": "shared.money.py", "need": "contract"}]
    assert units["billing.refund.py"]["needs"] == units["ledger.post.py"]["needs"] == [
        {"unit": "shared.money.py", "need": "contract"}]
    assert units["shared.money.py"]["provides"] == ["round_cents"]
    assert report["waves"] == [["billing.refund.py", "jobs.p.py", "ledger.audit.py", "ledger.post.py", "shared.money.py"],
                               ["billing.invoice.py"]]
    assert (report["width"], report["depth"]) == (5, 2)

    # The draft is a dependency packet plan the validator accepts with the same hard-need waves.
    plan = json.loads(out.read_text())
    assert report["file"] == str(out.resolve()) and plan["repository"] == info["repo_id"]
    tasks = {task["id"]: task for task in plan["tasks"]}
    assert [o["id"] for o in tasks["shared.money.py"]["outputs"]] == ["source", "contract"]
    assert tasks["shared.money.py"]["outputs"][1]["paths"] == ["shared/money.py"]
    assert tasks["billing.invoice.py"]["prerequisites"] == [
        {"task_id": "billing.refund.py", "output_id": "source", "version": 1, "need": "hard"},
        {"task_id": "shared.money.py", "output_id": "contract", "version": 1, "need": "contract",
         "provider_output": "source", "provider_version": 1, "stand_in": False}]
    result = planning.validate(store, plan)
    assert result["valid"] and result["hard_dependency_levels"] == report["waves"]
    assert max(map(len, result["hard_dependency_levels"])) == report["width"]


def test_partition_scopes_default_to_top_level_directories_and_rules_are_structural(partitioned, tmp_path):
    store, repo, _ = partitioned
    report = plan_partition.report(store.root, repo, None, [])
    # docs/ is a scope of its own now, so round_cents reaches three.
    assert report["scopes"] == ["billing", "docs", "jobs", "ledger", "shared"] and report["files"] == 16
    assert report["providers"][0]["scopes"] == ["billing", "docs", "ledger"] and report["pairs"] == []
    for bad in ({"partition": {"merge": [{"id": "x", "from": {"pattern": "*.py"}}]}},
                {"partition": {"merge": [{"id": "x"}]}},
                {"partition": {"lanes": []}},
                {"partition": {"merge": [{"id": "x", "kind": "fetch", "to": {"name": "a"}}]}}):
        (store.root / "config/hx.json").write_text(json.dumps(bad))
        with pytest.raises(ValidationError, match="partition"):
            plan_partition.merge_rules(store.root)
    with pytest.raises(ValidationError, match="outside the product checkout"):
        (store.root / "config/hx.json").write_text("{}")
        planning.main(["partition", "--repo", str(repo), "--out", str(repo / "plan.json")], store.root, env={})
    assert not (repo / "plan.json").exists()


def test_link_query_bounds_the_graph_and_the_draft_refuses_a_truncated_one(partitioned, monkeypatch):
    store, repo, _ = partitioned
    from hx import code_queries

    monkeypatch.setattr(code_queries, "LINKS", 3)
    original = code_queries.links
    monkeypatch.setattr(code_queries, "links", lambda conn, **kw: original(conn, **kw, limit=code_queries.LINKS))
    report = plan_partition.report(store.root, repo, SCOPES, [])
    assert report["truncated"] and report["links"] == 3
    with pytest.raises(ValidationError, match="truncated"):
        plan_partition.draft(report, dict(POLICY), "partition")


def test_a_shared_repository_service_answers_the_partition(partitioned, tmp_path):
    store, repo, _ = partitioned
    local = plan_partition.report(store.root, repo, SCOPES, MERGE)
    short = tempfile.TemporaryDirectory(prefix="hx-partition-test-", dir="/tmp")
    path = Path(short.name) / "service.sock"
    (tmp_path / "service").mkdir()
    server = repo_service.RepositoryServer(path, tmp_path / "service", workers=2)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        repo_service.request(path, {"method": "register", "repo": str(repo)})
        instance = tmp_path / "instance"
        (instance / "config").mkdir(parents=True)
        (instance / "config/hx.json").write_text(json.dumps({"repository_service": str(path), "partition": {"merge": MERGE}}))
        assert repo_service.code_request(instance, ["index", "--repo", str(repo)])["exit_code"] == 0
        shared = plan_partition.report(instance, repo, SCOPES, plan_partition.merge_rules(instance))
        assert {k: shared[k] for k in ("providers", "units", "waves", "pairs", "cycles")} == {
            k: local[k] for k in ("providers", "units", "waves", "pairs", "cycles")}
        assert not (instance / "state/code-index/index.db").exists()
        with pytest.raises(ValidationError, match="partition"):
            repo_service.request(path, {"method": "partition", "repository": str(repo), "scopes": None,
                                        "merge": [{"id": "x", "from": {"glob": "*"}}]})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        short.cleanup()
