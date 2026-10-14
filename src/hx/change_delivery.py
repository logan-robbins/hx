"""Intent-bound review, optimistic landing, continuous validation and release proof."""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import time
from pathlib import Path

from .caller import caller
from .continuity_store import Conflict, ContinuityStore, canonical, digest
from .errors import Refused, ValidationError

SCHEMA = """
CREATE TABLE IF NOT EXISTS delivery_changes (
 change_id TEXT NOT NULL,revision INTEGER NOT NULL,head TEXT NOT NULL,
 status TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(change_id,revision));
CREATE TABLE IF NOT EXISTS delivery_reviews (
 change_id TEXT NOT NULL,revision INTEGER NOT NULL,reviewer TEXT NOT NULL,
 decision TEXT NOT NULL,intent_hash TEXT NOT NULL,payload TEXT NOT NULL,
 PRIMARY KEY(change_id,revision,reviewer));
CREATE TABLE IF NOT EXISTS delivery_batches (
 batch_id TEXT PRIMARY KEY,repository TEXT NOT NULL,head TEXT NOT NULL,
 status TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS delivery_events (
 seq INTEGER PRIMARY KEY,kind TEXT NOT NULL,identity TEXT NOT NULL,payload TEXT NOT NULL,
 UNIQUE(kind,identity));
"""


def initialize(db):
    for statement in SCHEMA.split(";"):
        if statement.strip():
            db.execute(statement)
    from . import delivery_validation
    delivery_validation.initialize(db)


def _git(repo, *args):
    result = subprocess.run(["git", "--no-optional-locks", "--literal-pathspecs", "-C", str(repo), *args],
                            capture_output=True, timeout=60)
    if result.returncode:
        raise Conflict("delivery Git operation refused: " + result.stderr.decode(errors="replace")[:1000])
    if len(result.stdout) > 262144:
        raise Conflict("delivery metadata exceeds bound; split the change")
    return result.stdout


def _commit(repo, revision):
    if not isinstance(revision, str) or not revision or revision.startswith("-"):
        raise ValidationError("delivery revision is invalid")
    return _git(repo, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}").decode().strip()


def _policy(root):
    path = Path(root) / "config/delivery.json"
    if path.exists() and path.stat().st_size>262144:
        raise ValidationError("delivery policy exceeds 256 KiB")
    body = json.loads(path.read_text()) if path.exists() else {}
    lists = {"reviewers", "sensitive_paths", "sensitive_reviewers", "post_merge_checks", "pre_deploy_checks", "build_checks", "artifact_paths"}
    if not isinstance(body,dict) or body.keys() - lists - {"check_definitions","validation_resources"}:
        raise ValidationError("delivery policy has unknown fields")
    for key in lists & body.keys():
        value=body[key]
        if not isinstance(value,list) or len(value)>128 or any(not isinstance(x,str) or not x for x in value):
            raise ValidationError("delivery policy lists require at most 128 strings")
    from . import checks
    definitions=body.get("check_definitions",{})
    if not isinstance(definitions,dict) or len(definitions)>32:
        raise ValidationError("delivery requires at most 32 exact check definitions")
    for name,recipe in definitions.items():
        checks.validate(recipe)
        if name!=recipe['id']:
            raise ValidationError("delivery check key differs from its recipe ID")
    resources=body.get("validation_resources",[])
    if not isinstance(resources,list) or len(resources)>32:
        raise ValidationError("validation resources exceed the configured boundary")
    for resource in resources:
        if not isinstance(resource,dict) or resource.keys()!={'id','mode'} or not isinstance(resource['id'],str) or not resource['id'] or resource['mode'] not in ('shared','exclusive'):
            raise ValidationError("validation resource requires identity and shared or exclusive mode")
    return body


def _role(root, actor):
    if actor == "operator":
        return "operator"
    path = Path(root) / "config" / actor / "harness.json"
    try:
        return json.loads(path.read_text()).get("role")
    except (OSError, ValueError):
        return None


def _event(tx, kind, identity, body):
    tx._change()
    tx.db.execute("INSERT OR IGNORE INTO delivery_events(kind,identity,payload) VALUES(?,?,?)", (kind, identity, canonical({**body, "recorded_at": time.time()})))
    tx.enqueue("projection", f"delivery:{kind}:{identity}", {"delivery": kind, "identity": identity})


def _change(tx, change_id):
    row = tx.db.execute("SELECT * FROM delivery_changes WHERE change_id=? ORDER BY revision DESC LIMIT 1", (change_id,)).fetchone()
    if row is None:
        raise ValidationError("unknown delivery change")
    return {**dict(row), "payload": json.loads(row["payload"])}


def _completion(store, run_id, *, head=None, required=()):
    from .checks import receipt_intact
    row = store.db.execute("SELECT payload FROM unit_completions WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise Conflict("delivery requires a covered work packet with published output proof")
    result = json.loads(row[0])
    current = store.db.execute("SELECT r.task_id,r.task_revision,h.revision FROM runs r JOIN task_heads h ON h.task_id=r.task_id WHERE r.run_id=?", (run_id,)).fetchone()
    if current is None or current['task_revision']!=current['revision']:
        raise Conflict("delivery packet assignment revision changed")
    from .native_producer import require_drained
    from .continuity_store import Transaction
    if store.db.in_transaction:
        require_drained(Transaction(store),run_id)
    else:
        with store.transaction() as tx:
            require_drained(tx,run_id)
    if head is not None and result["commit"] != head:
        raise Conflict("delivery evidence names a different source snapshot")
    if set(required) - set(result["checks"]):
        raise Conflict("delivery proof omits required boundary checks")
    for check, receipt in result["checks"].items():
        record = store.db.execute("SELECT * FROM receipts WHERE receipt_id=? AND run_id=? AND check_id=?", (receipt, run_id, check)).fetchone()
        if record is None or not receipt_intact(store, record):
            raise Conflict("delivery check evidence is missing, invalid, or corrupt")
    return result


def _classify(repo, base, head, paths):
    """Conservative semantic categories; unsupported syntax remains implementation."""
    if paths and all(Path(p).suffix.lower() in {".md", ".rst", ".txt"} for p in paths):
        return "documentation"
    if any("migration" in p.lower() or Path(p).suffix.lower() == ".sql" for p in paths):
        return "migration"
    if paths and all(p.endswith(".py") for p in paths):
        try:
            same = all(ast.dump(ast.parse(_git(repo, "show", base + ":" + p), type_comments=True)) ==
                       ast.dump(ast.parse(_git(repo, "show", head + ":" + p), type_comments=True)) for p in paths)
            if same:
                return "comments"
        except (Conflict, SyntaxError, ValueError):
            pass
    from .code_extract import extract
    for path in paths:
        try:
            before = extract(_git(repo, "show", base + ":" + path).decode(errors="replace"), path)[0]
            after = extract(_git(repo, "show", head + ":" + path).decode(errors="replace"), path)[0]
        except Conflict:
            return "interface"
        api = lambda rows: sorted((r["qualified"], r["signature"]) for r in rows)
        if api(before) != api(after):
            return "interface"
    return "implementation"


def submit(store, body, *, env=None):
    required = {"id", "run_id", "base", "head", "behavior"}
    if not isinstance(body, dict) or body.keys() != required or any(not isinstance(v, str) or not v.strip() for v in body.values()):
        raise ValidationError("change requires id, run_id, base, head and behavior")
    actor = caller(env) or "operator"
    proof = _completion(store, body["run_id"])
    run = store.db.execute("SELECT r.worker_id,t.payload FROM runs r JOIN tasks t ON t.task_id=r.task_id AND t.revision=r.task_revision WHERE run_id=?", (body["run_id"],)).fetchone()
    if actor not in {run["worker_id"], "operator"}:
        raise Refused("a worker can submit only its own covered packet")
    task = json.loads(run["payload"])
    if body["behavior"] != task["goal"]:
        raise Conflict("review intent must preserve the assigned packet behavior")
    repo = Path(task["workdir"])
    head, base = _commit(repo, body["head"]), _commit(repo, body["base"])
    if proof["commit"] != head:
        raise Conflict("submitted change is not the packet's accepted snapshot")
    _git(repo, "merge-base", "--is-ancestor", base, head)
    paths = [os.fsdecode(p) for p in _git(repo, "diff", "--name-only", "--no-renames", "-z", base, head).split(b"\0") if p]
    if set(paths) - set(proof["changed_paths"]):
        raise Conflict("review change contains work outside its covered packet")
    payload = {**body, "head": head, "base": base, "repository": task["repository"], "repo": str(repo),
               "submitter": run["worker_id"], "paths": paths, "kind": _classify(repo, base, head, paths),
               "contracts": task["map_inputs"], "evidence": proof, "obligations": task.get("prerequisites", [])}
    with store.transaction() as tx:
        existing = tx.db.execute("SELECT * FROM delivery_changes WHERE change_id=? ORDER BY revision DESC LIMIT 1", (body["id"],)).fetchone()
        if existing and digest(json.loads(existing["payload"])) == digest(payload):
            return _change(tx, body["id"])
        if existing and existing["status"] == "landed":
            raise Conflict("landed intent cannot be rewritten; submit a new change")
        revision = existing["revision"] + 1 if existing else 1
        tx.db.execute("INSERT INTO delivery_changes VALUES(?,?,?,'submitted',?)", (body["id"], revision, head, canonical(payload)))
        _event(tx, "change_submitted", f"{body['id']}:{revision}", payload)
        return _change(tx, body["id"])


def review(store, change_id, *, decision, expected_revision, reason, env=None):
    actor = caller(env) or "operator"
    if decision not in {"approve", "reject"} or not isinstance(reason, str) or not reason.strip():
        raise ValidationError("review needs approve/reject and an intent judgment")
    policy = _policy(store.root)
    with store.transaction() as tx:
        change = _change(tx, change_id)
        if change["revision"] != expected_revision or (change["status"] == "landed" and decision != "reject"):
            raise Conflict("review revision changed or already landed")
        if actor == change["payload"]["submitter"]:
            raise Refused("implementation author cannot independently approve their own change")
        allowed = policy.get("reviewers")
        if _role(store.root,actor) not in {"qa-engineer","release-engineer","operator"} or (allowed is not None and actor not in allowed):
            raise Refused("caller is not a configured delivery reviewer")
        if decision=="approve":
            _completion(store, change["payload"]["run_id"], head=change["head"])
        tx.db.execute("INSERT OR REPLACE INTO delivery_reviews VALUES(?,?,?,?,?,?)", (change_id, expected_revision, actor, decision, digest(change["payload"]), canonical({"reason": reason, "head": change["head"]})))
        _event(tx, "change_reviewed", f"{change_id}:{expected_revision}:{actor}:{decision}", {"change": change_id, "revision": expected_revision, "reviewer": actor, "decision": decision})
        return _change(tx, change_id)


def _approved(store, change):
    policy = _policy(store.root)
    rows = store.db.execute("SELECT * FROM delivery_reviews WHERE change_id=? AND revision=? AND intent_hash=?", (change["change_id"], change["revision"], digest(change["payload"]))).fetchall()
    rows=[row for row in rows if row['reviewer']!=change['payload']['submitter'] and _role(store.root,row['reviewer']) in {'qa-engineer','release-engineer','operator'} and (policy.get('reviewers') is None or row['reviewer'] in policy['reviewers'])]
    if not rows or any(r["decision"] == "reject" for r in rows):
        raise Conflict("change lacks current independent intent approval")
    approved = {r["reviewer"] for r in rows if r["decision"] == "approve" and r["reviewer"] != change["payload"]["submitter"]
                and _role(store.root,r["reviewer"]) in {"qa-engineer","release-engineer","operator"}
                and (policy.get("reviewers") is None or r["reviewer"] in policy["reviewers"])}
    if not approved:
        raise Conflict("independent intent approval was revoked by current review policy")
    sensitive = change["payload"]["kind"] in {"interface", "migration"} or any(p == prefix or p.startswith(prefix.rstrip("/") + "/") for p in change["payload"]["paths"] for prefix in policy.get("sensitive_paths", []))
    if sensitive:
        required = policy.get("sensitive_reviewers")
        if required and not set(required) <= approved:
            raise Conflict("semantic approval requirements are not satisfied")
        if required is None and not any(_role(store.root, a) in {"release-engineer", "operator"} for a in approved):
            raise Conflict("interface/migration changes require release approval")


def land(store, change_id, *, repository, branch="main", expected_head, env=None):
    actor = caller(env) or "operator"
    if _role(store.root, actor) not in {"release-engineer", "operator"}:
        raise Refused("landing belongs to the configured release authority")
    repo = Path(repository).resolve()
    if _git(repo, "rev-parse", "--is-bare-repository").strip() != b"true":
        raise ValidationError("landing requires a bare integration repository; workers keep isolated working copies")
    _git(repo, "check-ref-format", "refs/heads/" + branch)
    with store.transaction() as tx:
        change = _change(tx, change_id)
    previous=store.db.execute("SELECT batch_id,payload,status FROM delivery_batches WHERE json_extract(payload,'$.change')=? AND json_extract(payload,'$.revision')=? ORDER BY rowid DESC LIMIT 1",(change_id,change['revision'])).fetchone()
    if previous and (change['status']=='landed' or previous['status']=='prepared'):
        journal=json.loads(previous['payload'])
        if reconcile(store,previous['batch_id'])['landed']:
            return {'change':change_id,'head':journal['head'],'batch':previous['batch_id'],'integration':previous['status'],'deployable':False}
    _approved(store, change)
    _completion(store, change["payload"]["run_id"], head=change["head"])
    # Acquire exact producer objects; local repository transfer, no remote push.
    _git(repo, "fetch", "--no-tags", "--no-write-fetch-head", change["payload"]["repo"], change["head"])
    expected = _commit(repo, expected_head)
    current = _commit(repo, "refs/heads/" + branch)
    if expected != current:
        raise Conflict("integration head advanced; reconstruct the candidate against current dependencies")
    tree = _git(repo, "merge-tree", "--write-tree", current, change["head"]).decode().splitlines()[0]
    candidate = _git(repo, "-c", "user.name=HX delivery", "-c", "user.email=hx@localhost", "commit-tree", tree,
                     "-p", current, "-p", change["head"], "-m", change["payload"]["behavior"]).decode().strip()
    batch = digest({"repository": change["payload"]["repository"], "head": candidate})
    intent = {"change": change_id, "revision": change["revision"], "base": current, "head": candidate,
              "repo": str(repo), "branch": branch, "run_id": change["payload"]["run_id"]}
    # Durably record the candidate before the brief CAS. Retry reconciles the ref.
    with store.transaction() as tx:
        if _change(tx, change_id)["revision"] != change["revision"]:
            raise Conflict("change was revised during candidate construction")
        _approved(store, change)
        tx.db.execute("INSERT OR IGNORE INTO delivery_batches VALUES(?,?,?,'prepared',?)", (batch, change["payload"]["repository"], candidate, canonical(intent)))
        _event(tx, "landing_prepared", batch, intent)
    with store.transaction() as tx:
        if _change(tx, change_id)["revision"] != change["revision"]:
            raise Conflict("change was revised before publication")
        _approved(store, change)
        _git(repo, "update-ref", "refs/heads/" + branch, candidate, current)
        tx.db.execute("UPDATE delivery_batches SET status='pending' WHERE batch_id=?", (batch,))
        tx.db.execute("UPDATE delivery_changes SET status='landed' WHERE change_id=? AND revision=?", (change_id, change["revision"]))
        _event(tx, "change_landed", batch, intent)
        return {"change": change_id, "head": candidate, "batch": batch, "integration": "pending", "deployable": False}


def validate(store, batch_id, run_id, *, boundary="integration",env=None):
    from . import delivery_validation as validation
    validation.authority(store,env)
    fixed=store.db.execute('SELECT 1 FROM delivery_validations WHERE batch_id=?',(batch_id,)).fetchone()
    if fixed:
        return validation.attest_unit(store,batch_id,run_id,env=env)
    row=store.db.execute('SELECT * FROM delivery_batches WHERE batch_id=?',(batch_id,)).fetchone()
    if row is None or row['status']=='prepared':
        raise Conflict('candidate has not landed')
    # Preserve the exact snapshot refusal before reading a boundary policy.
    _completion(store,run_id,head=row['head'])
    payload=json.loads(row['payload'])
    frozen=validation.candidate(store,payload['repo'],row['head'],branch=payload['branch'],boundary=boundary,env=env,_pinned=True)
    result=validation.attest_unit(store,frozen['batch'],run_id,env=env)
    with store.transaction() as tx:
        tx._change()
        tx.db.execute('UPDATE delivery_batches SET status=? WHERE batch_id=?',('deployable' if boundary=='deploy' else 'verified',batch_id))
    return result


def reconcile(store, batch_id, *, env=None):
    from .delivery_validation import authority
    authority(store,env)
    if store.db.execute('SELECT 1 FROM delivery_validations WHERE batch_id=?',(batch_id,)).fetchone():
        from .delivery_validation import reconcile as recover_validation
        return recover_validation(store,batch_id,env=env)
    if store.db.execute('SELECT 1 FROM delivery_reverts WHERE revert_id=?',(batch_id,)).fetchone():
        from .delivery_recovery import reconcile as recover
        return recover(store,batch_id,env=env)
    row = store.db.execute("SELECT * FROM delivery_batches WHERE batch_id=?", (batch_id,)).fetchone()
    if row is None:
        raise ValidationError("unknown candidate")
    body = json.loads(row["payload"])
    current = _commit(body["repo"], "refs/heads/" + body["branch"])
    if current == row["head"] or subprocess.run(["git", "-C", body["repo"], "merge-base", "--is-ancestor", row["head"], current], capture_output=True).returncode == 0:
        with store.transaction() as tx:
            tx.db.execute("UPDATE delivery_batches SET status='pending' WHERE batch_id=? AND status='prepared'", (batch_id,))
            tx.db.execute("UPDATE delivery_changes SET status='landed' WHERE change_id=? AND revision=?", (body["change"], body["revision"]))
            _event(tx, "change_landed", batch_id, body)
        return {"batch": batch_id, "landed": True}
    return {"batch": batch_id, "landed": False, "current_head": current, "reason": "publication not observed; no blind replay"}


def status(store, *, change_id=None, limit=20):
    if not 1 <= limit <= 100:
        raise ValidationError("delivery status limit must be 1–100")
    if change_id:
        with store.transaction() as tx:
            return _change(tx, change_id)
    return {"changes": [{**dict(r), "payload": json.loads(r["payload"])} for r in store.db.execute("SELECT * FROM delivery_changes ORDER BY rowid DESC LIMIT ?", (limit,))],
            "candidates": [{**dict(r), "payload": json.loads(r["payload"])} for r in store.db.execute("SELECT * FROM delivery_batches ORDER BY rowid DESC LIMIT ?", (limit,))],
            "validation_snapshots": _validation_status(store,limit),
            "repairs": [json.loads(r[0]) for r in store.db.execute('SELECT payload FROM delivery_repairs ORDER BY rowid DESC LIMIT ?',(limit,))]}


def _validation_status(store,limit):
    from .delivery_validation import view
    return [view(store,row[0]) for row in store.db.execute('SELECT batch_id FROM delivery_validations ORDER BY rowid DESC LIMIT ?',(limit,))]


def main(argv, root, *, env=None):
    parser = argparse.ArgumentParser(prog="hx change", epilog="example: hx change status")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("submit", "review", "land", "validate", "reconcile", "status", "candidate", "run-validation", "fail", "revert"):
        examples={"candidate":"candidate --repo /srv/integration.git --expected-head SHA", "run-validation":"run-validation BATCH --snapshot /tmp/fixed-source", "fail":"fail BATCH --receipt RECEIPT", "revert":"revert CHANGE --batch BATCH --repo /srv/integration.git --expected-head SHA"}
        p = sub.add_parser(name, epilog="example: hx change "+examples.get(name,name))
        p.add_argument("--root", help=argparse.SUPPRESS)
        if name == "submit":
            p.add_argument("--file", required=True)
        elif name == "candidate":
            p.add_argument('--repo',required=True)
            p.add_argument('--branch',default='main')
            p.add_argument('--expected-head',required=True)
            p.add_argument('--boundary',choices=['integration','deploy'],default='integration')
            p.add_argument('--historical',action='store_true',help='Freeze an authoritative ancestor to drain a bounded pending frontier')
            p.add_argument('--retry',help='Create a distinct explicit attempt after this fixed snapshot was safely reconciled')
        elif name in {'run-validation','fail'}:
            p.add_argument('batch')
            if name=='run-validation':
                p.add_argument('--snapshot',required=True)
            else:
                p.add_argument('--receipt',required=True)
                p.add_argument('--baseline')
        elif name == "status":
            p.add_argument("--change")
            p.add_argument("--limit", type=int, default=20)
        elif name in {"validate", "reconcile"}:
            p.add_argument("batch")
            if name == "validate":
                p.add_argument("--run", required=True)
                p.add_argument("--boundary", choices=["integration", "deploy"], default="integration")
        else:
            p.add_argument("change")
            if name == "review":
                p.add_argument("--decision", choices=["approve", "reject"], required=True)
                p.add_argument("--revision", type=int, required=True)
                p.add_argument("--reason", required=True)
            else:
                if name=="revert":
                    p.add_argument("--batch",required=True)
                p.add_argument("--repo", required=True)
                p.add_argument("--branch", default="main")
                p.add_argument("--expected-head", required=True)
    args = parser.parse_args(argv)
    with ContinuityStore(root) as store:
        if args.command == "submit":
            result = submit(store, json.loads(Path(args.file).read_text()), env=env)
        elif args.command == "review":
            result = review(store, args.change, decision=args.decision, expected_revision=args.revision, reason=args.reason, env=env)
        elif args.command == "land":
            result = land(store, args.change, repository=args.repo, branch=args.branch, expected_head=args.expected_head, env=env)
        elif args.command == "validate":
            result = validate(store, args.batch, args.run, boundary=args.boundary,env=env)
        elif args.command=='candidate':
            from .delivery_validation import candidate
            result=candidate(store,args.repo,args.expected_head,branch=args.branch,boundary=args.boundary,env=env,_pinned=args.historical,retry=args.retry)
        elif args.command=='run-validation':
            from .delivery_validation import run_validation
            result=run_validation(store,args.batch,args.snapshot,env=env)
        elif args.command=='fail':
            from .delivery_recovery import fail
            result=fail(store,args.batch,args.receipt,baseline_receipt=args.baseline,env=env)
        elif args.command=='revert':
            from .delivery_recovery import revert
            result=revert(store,args.change,args.batch,args.repo,args.expected_head,branch=args.branch,env=env)
        elif args.command == "reconcile":
            result = reconcile(store, args.batch, env=env)
        else:
            result = status(store, change_id=args.change, limit=args.limit)
    print(json.dumps(result))
    return 1 if args.command=='run-validation' and not result['ready'] else 0
