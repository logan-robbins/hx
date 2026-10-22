"""Published contract declarations answer every consumer checkout before merge.

`publish-contract` registers the published files' declarations under the repository
namespace with lane `contract` and the ledger row as provenance; a consumer worktree
that has not merged the stub answers `type|signature|contract NAME` from them, then
from its own index once it merges, and `--expect exists` holds throughout.
"""

import json
import tempfile
import threading
from pathlib import Path

from hx import code_index, code_stubs, planning, repo_service, unit_execution as units
from hx import dependency_packets as packets
from .test_appmap import git, mapped  # noqa: F401  (fixture)
from .test_checks import definition
from .test_dependency_packets import binding, packet, packet_plan, run_receipts
from .test_map_updates import active  # noqa: F401  (fixture)
from .test_unit_execution import commit, fleet  # noqa: F401  (fixture)

API = "class Adapter:\n    def adapt(self, value):\n        return value\n"


def producer_plan(fleet, tmp_path):
    store, repo = fleet[:2]
    consumer = tmp_path / "consumer"
    git(repo, "worktree", "add", "-qb", "consumer", str(consumer))
    producer = packet(repo, "producer", paths=["api.py"])
    producer["outputs"].append({"id": "interface", "version": 1, "kind": "contract", "paths": ["api.py"],
                                "obligation": "The immutable API names the Adapter type."})
    return producer, consumer.resolve()


def publish(fleet, producer):
    store, repo = fleet[:2]
    planning.apply(store, packet_plan(fleet, [producer]))
    run = units.assign(store, "producer", 1, "eng-001")["run_id"]
    check = definition(f"from pathlib import Path; assert Path('api.py').read_text() == {API!r}")
    check["id"] = "contract-check"
    check["source"] = {"complete": True, "inputs": ["api.py"], "dependencies": []}
    body = binding(planning.assignment(store, "producer"), recipes={"unit": definition(), "contract-check": check})
    body["coverage"]["output.interface"] = ["contract-check"]
    proposal = packets.bind_checks(store, run, body)
    packets.approve_binding(store, run, proposal["binding_hash"], "qa-001")
    (repo / "api.py").write_text(API)
    commit(repo)
    return run, packets.publish_contract(store, run, "interface", run_receipts(store, run, ["contract-check"]))


def read(root, checkout, *argv):
    return code_index.execute([*argv, "--repo", str(checkout)], root)


def test_consumer_sees_the_stub_before_merge_and_the_real_symbol_after_index(fleet, tmp_path):
    store, repo = fleet[:2]
    producer, consumer = producer_plan(fleet, tmp_path)
    code_index.index_repo(store.root, consumer)
    base = git(consumer, "rev-parse", "HEAD")
    status, _ = read(store.root, consumer, "type", "Adapter", "--expect", "exists")
    assert status == 3

    _, proof = publish(fleet, producer)
    provenance = {"ledger": str(store.root.resolve()), "plan_id": "continuation", "task_id": "producer",
                  "task_revision": 1, "output_id": "interface", "version": 1, "commit": proof["commit"]}
    assert not (consumer / "api.py").exists()
    status, rows = read(store.root, consumer, "type", "Adapter", "--expect", "exists")
    assert status == 0
    [stub] = rows
    assert (stub["lane"], stub["kind"], stub["path"], stub["stale"]) == ("contract", "class", "api.py", False)
    assert stub["provenance"] == provenance and stub["repo"] == fleet[2]["repo_id"]
    status, rows = read(store.root, consumer, "signature", "Adapter.adapt", "--expect", "exists")
    assert status == 0 and rows[0]["signature"] == "def adapt(self, value)"
    status, rows = read(store.root, consumer, "contract", "Adapter", "--expect", "exists")
    assert status == 0 and rows[0]["provenance"] == provenance and rows[0]["superseded"] is False
    status, hits = read(store.root, consumer, "search", "Adapter")
    assert status == 0 and hits[0]["path"] == "api.py" and hits[0]["lane"] == "contract"

    # Merged and indexed, the consumer answers from its own snapshot; the contract stays published.
    git(consumer, "merge", "-q", "--ff-only", proof["commit"])
    code_index.index_repo(store.root, consumer)
    status, rows = read(store.root, consumer, "type", "Adapter", "--expect", "exists")
    assert status == 0
    assert [(r["lane"], r["path"]) for r in rows] == [("syntax", "api.py")] and not rows[0]["stale"]
    status, rows = read(store.root, consumer, "contract", "Adapter", "--expect", "exists")
    assert status == 0 and rows[0]["superseded"] is True
    # Historical reads answer the immutable snapshot only.
    status, _ = read(store.root, consumer, "type", "Adapter", "--expect", "exists", "--at", base)
    assert status == 3


def test_a_new_producer_revision_or_contract_version_removes_its_stubs(fleet, tmp_path):
    store, repo = fleet[:2]
    producer, consumer = producer_plan(fleet, tmp_path)
    code_index.index_repo(store.root, consumer)
    run, proof = publish(fleet, producer)
    ledger = str(store.root.resolve())
    later = code_stubs.publication(store.root, {"payload": {"repository": fleet[2]["repo_id"], "plan_id": "continuation"}},
                                   {**proof, "version": 2}, [dict(name="Renamed", qualified="Renamed", kind="class", path="api.py",
                                                                  line=1, end_line=1, signature="class Renamed:", lane="contract")])
    assert code_stubs.send(store.root, later) == {"registered": 1, "removed": 2}
    assert read(store.root, consumer, "type", "Adapter", "--expect", "absent")[0] == 0
    assert read(store.root, consumer, "type", "Renamed", "--expect", "exists")[0] == 0

    units.stop(store, run)
    code_index.index_repo(store.root, repo)
    outputs = [dict(output, version=output["version"] + 1) for output in producer["outputs"]]
    revised = packet_plan(fleet, [dict(producer, expected_revision=1, outputs=outputs,
                                       behavior="The API names a renamed adapter type.")])
    revised["expected_revision"] = 1
    assert planning.apply(store, revised)["task_versions"] == {"producer": 2}
    assert read(store.root, consumer, "type", "Renamed", "--expect", "absent")[0] == 0
    assert code_stubs.apply(store.root, {"ledger": ledger, "prune": {"producer": 2}}) == {"removed": 0}


def test_a_shared_repository_service_holds_the_stubs_for_every_instance(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "main.py").write_text("def feature():\n    return 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial")
    service = tmp_path / "service"
    service.mkdir()
    short = tempfile.TemporaryDirectory(prefix="hx-stub-test-", dir="/tmp")
    path = Path(short.name) / "service.sock"
    server = repo_service.RepositoryServer(path, service, workers=2)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        identity = repo_service.request(path, {"method": "register", "repo": str(repo)})["namespace"]
        instances = []
        for name in ("one", "two"):
            instance = tmp_path / name
            (instance / "config").mkdir(parents=True)
            (instance / "config/hx.json").write_text(json.dumps({"repository_service": str(path)}))
            instances.append(instance)
        assert repo_service.code_request(instances[0], ["index", "--repo", str(repo)])["exit_code"] == 0
        stub = dict(name="Adapter", qualified="Adapter", kind="interface", path="api.cs", line=1, end_line=1,
                    signature="public interface Adapter", lane="contract")
        operation = code_stubs.publication(instances[0], {"payload": {"repository": identity, "plan_id": "wave"}},
                                           {"producer": "provider", "task_revision": 1, "id": "api", "version": 1,
                                            "commit": git(repo, "rev-parse", "HEAD")}, [stub])
        assert code_stubs.send(instances[0], operation) == {"registered": 1, "removed": 0}
        assert not (instances[0] / "state/contract-stubs").exists() or not any((instances[0] / "state/contract-stubs").iterdir())
        for instance in instances:
            answer = repo_service.code_request(instance, ["type", "Adapter", "--expect", "exists", "--repo", str(repo)])
            assert answer["exit_code"] == 0 and answer["result"][0]["provenance"]["task_id"] == "provider"
            assert not (instance / "state/code-index/index.db").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        short.cleanup()
