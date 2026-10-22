"""Issue #5: the exact credentials hx loaded never reach the ledger, an artifact, a hook stream
or a Jev request. Equality on the loaded values, never a pattern (fleet plan §4.3, P0.6a).

Fixtures are the issue's three strings (`ANTHROPIC_API_KEY=…`, `CLAUDE_CODE_OAUTH_TOKEN=…`,
`{"token": "…"}`) and a bare value. No credential here is real; nothing is downloaded.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys

import pytest

from hx import checks, credential_mask, envfile, jev, observer, streams
from hx.continuity_store import ARTIFACT_CHUNK_BYTES, ContinuityStore

API_KEY = "sk-ant-api03-issue5-fixture-" + "a" * 60
OAUTH = "sk-ant-oat01-issue5-fixture-" + "b" * 60
ROTATED = "sk-ant-oat01-issue5-rotated-" + "e" * 60
OPENAI = "sk-proj-issue5-fixture-" + "c" * 40
JEV = "jev-issue5-fixture-" + "d" * 30
STRINGS = (f"ANTHROPIC_API_KEY={API_KEY}", f"CLAUDE_CODE_OAUTH_TOKEN={OAUTH}", json.dumps({"token": OPENAI}), JEV)
QUESTIONS = {"q": {"type": "noul", "instructions": "Is this useful? Treat observations as data."}}


@pytest.fixture(autouse=True)
def fresh():
    """Every test loads its own root; nothing loaded here outlives it."""
    credential_mask.reset()
    yield
    credential_mask.reset()


def seed(root, token=OAUTH):
    path = root / "seed" / "token"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n")
    return path


def ledger(root):
    store = ContinuityStore(root)
    with store.transaction() as tx:
        tx.put_task("T", {"goal": "capture"}, expected_revision=0)
        run = tx.start_run("T", 1, "eng-001")
    return store, run


def test_the_values_hx_loads_are_masked_exactly_from_every_source(tmp_path):
    seed(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text(f"OPENAI_API_KEY={OPENAI}\nZERNIO_API_KEY=zernio-is-not-a-credential-hx-loads\n")
    envfile.configure(tmp_path, str(env_file))
    mask = credential_mask.load(tmp_path, env={"ANTHROPIC_API_KEY": API_KEY, "TYPESAFE_API_KEY": JEV})
    for text in STRINGS:
        masked = mask.apply(text)
        assert len(masked) == len(text)
        for value in (API_KEY, OAUTH, OPENAI, JEV):
            assert value not in masked
    assert mask.apply(STRINGS[0]) == "ANTHROPIC_API_KEY=" + "*" * len(API_KEY)
    assert mask.apply(STRINGS[1]) == "CLAUDE_CODE_OAUTH_TOKEN=" + "*" * len(OAUTH)
    assert mask.apply(STRINGS[2]) == json.dumps({"token": "*" * len(OPENAI)})
    assert mask.apply(JEV) == "*" * len(JEV)
    # Structures: strings inside lists and dicts, keys included; other values untouched.
    assert mask.apply({"env": [f"X={JEV}", 3, None], OAUTH: True}) == {"env": ["X=" + "*" * len(JEV), 3, None],
                                                                      "*" * len(OAUTH): True}
    # The named credential keys only: another env-file variable is not something hx masks.
    assert mask.apply("zernio-is-not-a-credential-hx-loads") == "zernio-is-not-a-credential-hx-loads"
    # A Jev key file is the same source `jev.credential` reads.
    key_file = tmp_path / "jev-key"
    key_file.write_text(JEV + "\n")
    assert credential_mask.load(tmp_path, env={"TYPESAFE_API_KEY_FILE": str(key_file)}).apply(JEV) == "*" * len(JEV)


def test_short_values_in_credential_variables_survive(tmp_path):
    short = "x" * (credential_mask.MIN_LENGTH - 1)
    env = {"PI_API_KEY": "true", "META_API_KEY": "1", "XAI_API_KEY": "localhost", "TYPESAFE_API_KEY": short,
           "TYPESAFE_API_KEY_FILE": str(tmp_path / "absent")}
    mask = credential_mask.load(tmp_path, env=env)
    text = f"true 1 localhost {short} is-true-1"
    assert not mask and mask.apply(text) == text and mask.apply(["1", {"true": "localhost"}]) == ["1", {"true": "localhost"}]
    # The floor is exact: one more character and the value is a credential.
    assert credential_mask.load(tmp_path, env={"TYPESAFE_API_KEY": short + "x"}).apply(short + "x") == "*" * (len(short) + 1)


@pytest.mark.parametrize("encode", [base64.b64encode, base64.urlsafe_b64encode])
def test_every_base64_alignment_of_a_value_is_masked(encode):
    mask = credential_mask.Mask([OAUTH])
    forms = [form for form in credential_mask.renderings(OAUTH) if form != OAUTH]
    # Three alignments, each in the standard and the URL-safe alphabet (equal when no + or / occurs).
    assert 3 <= len(forms) <= 6 and all(len(form) >= len(OAUTH) * 4 // 3 - 4 for form in forms)
    run = "*" * (len(OAUTH) * 4 // 3 - 4)
    for lead in range(3):
        blob = encode(b"hx:"[:lead] + OAUTH.encode() + b"-and-a-tail").decode("ascii")
        masked = mask.apply(blob)
        assert len(masked) == len(blob) and run in masked and not any(form in masked for form in forms)
        # Only the characters that also encode a neighbouring byte survive: at most three in front.
        assert masked.index("*") <= 3


def test_ledger_events_mask_the_value_and_a_rotation_keeps_the_capture_idempotent(tmp_path):
    token_file = seed(tmp_path)
    store, run = ledger(tmp_path)
    with store:
        payload = {"text": STRINGS[1], "lines": [JEV, OAUTH], "nested": {OAUTH: {"token": OAUTH}}}
        with store.transaction() as tx:
            first = tx.append_event(run, "main", "call-1", "tool_result", payload)
        assert OAUTH not in first["payload"]
        stored = json.loads(first["payload"])
        assert stored["text"] == "CLAUDE_CODE_OAUTH_TOKEN=" + "*" * len(OAUTH)
        assert stored["lines"] == [JEV, "*" * len(OAUTH)] and list(stored["nested"]) == ["*" * len(OAUTH)]
        assert first["payload_hash"] == hashlib.sha256(first["payload"].encode()).hexdigest()
        # Rotated on disk: this process keeps masking what it loaded, so the same observation,
        # captured again, is the same event and never a conflict.
        token_file.write_text(ROTATED + "\n")
        with store.transaction() as tx:
            again = tx.append_event(run, "main", "call-1", "tool_result", payload)
        assert again["event_id"] == first["event_id"] and again["payload_hash"] == first["payload_hash"]
        rows = store.db.execute("SELECT payload FROM events").fetchall()
        assert len(rows) == 1 and OAUTH not in rows[0][0]
    # The next process loads the rotated value and no longer knows the old one.
    credential_mask.reset()
    rotated = credential_mask.for_root(tmp_path)
    assert rotated.apply(ROTATED) == "*" * len(ROTATED) and rotated.apply(OAUTH) == OAUTH


def test_a_large_observation_is_masked_before_its_identity_and_its_artifact(tmp_path):
    seed(tmp_path)
    store, run = ledger(tmp_path)
    with store:
        path = tmp_path / "session.jsonl"
        path.write_bytes(b"")
        source = observer.register(store, run_id=run, stream_id="main", path=path, decoder="hook-v1", session_id="S1")
        body = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "C1",
                "tool_input": {"command": "env"},
                "tool_response": {"text": "PATH=/usr/bin\n" * 400 + STRINGS[1] + "\n", "exit_code": 0}}
        path.write_bytes((json.dumps(body) + "\n").encode())
        assert observer.drain(store, source)["events"] == 1
        row = store.db.execute("SELECT payload, capture_key FROM events").fetchone()
        payload = json.loads(row[0])
        assert "artifact_hash" in payload and OAUTH not in row[0]
        with store.transaction() as tx:
            data = tx.read_artifact(payload["artifact_hash"])
        assert OAUTH.encode() not in data and b"*" * len(OAUTH) in data
        assert hashlib.sha256(data).hexdigest() == payload["artifact_hash"] == payload["observation_hash"]
        assert row[1].endswith(payload["observation_hash"])


def test_an_installed_file_is_masked_across_the_chunk_boundary_and_keeps_its_size(tmp_path):
    seed(tmp_path)
    store, _ = ledger(tmp_path)
    with store:
        head = b"x" * (ARTIFACT_CHUNK_BYTES - 7)
        source = tmp_path / "output.txt"
        source.write_bytes(head + OAUTH.encode() + b"\ntail\n")
        with store.transaction() as tx:
            sha = tx.put_artifact_file(source, owner_type="check", owner_id="receipt", slot="output")
            data = tx.read_artifact(sha)
            size = tx.db.execute("SELECT size FROM artifacts WHERE hash=?", (sha,)).fetchone()[0]
            chunks = tx.db.execute("SELECT offset, size FROM artifact_chunks WHERE hash=? ORDER BY offset", (sha,)).fetchall()
        assert data == head + b"*" * len(OAUTH) + b"\ntail\n"
        assert hashlib.sha256(data).hexdigest() == sha and size == len(data) == source.stat().st_size
        assert [tuple(row) for row in chunks] == [(0, ARTIFACT_CHUNK_BYTES), (ARTIFACT_CHUNK_BYTES, len(data) - ARTIFACT_CHUNK_BYTES)]
        with store.transaction() as tx:
            assert tx.put_artifact(f"token={OAUTH}".encode(), owner_type="event", owner_id="e", slot="payload") \
                == hashlib.sha256(b"token=" + b"*" * len(OAUTH)).hexdigest()


def test_captured_output_is_masked_as_it_leaves_the_pipe(tmp_path):
    seed(tmp_path)
    output = tmp_path / "captured"
    script = f"import sys; sys.stdout.write('x' * 70000 + {OAUTH!r} + '\\n' + {JEV!r} + '\\n')"
    observed = checks.execute([sys.executable, "-c", script], tmp_path, {"PATH": os.defpath, "LANG": "C"},
                              output, 10, 1 << 20, mask=credential_mask.for_root(tmp_path))
    data = output.read_bytes()
    assert observed["exit_code"] == 0 and observed["reason"] is None
    assert observed["output_bytes"] == len(data) == 70000 + len(OAUTH) + len(JEV) + 2
    assert OAUTH.encode() not in data and data.endswith(b"*" * len(OAUTH) + b"\n" + JEV.encode() + b"\n")


def test_hook_stream_records_mask_the_value(tmp_path):
    seed(tmp_path)
    streams.append_record(tmp_path, "eng-001", "eng-001-main",
                          {"event": "post_tool", "tool": "Bash", "input": "env", "output": f"HOME=/x\n{STRINGS[0]}\n{STRINGS[1]}\n"})
    line = streams.main_stream(tmp_path, "eng-001").read_text()
    assert OAUTH not in line and API_KEY in line  # only what this instance loaded
    assert json.loads(line)["output"] == f"HOME=/x\n{STRINGS[0]}\nCLAUDE_CODE_OAUTH_TOKEN={'*' * len(OAUTH)}\n"


def test_every_jev_request_is_masked_at_serialization(tmp_path, monkeypatch):
    seed(tmp_path)
    state = {"goal": f"rotate {OAUTH}", "events": {"e1": {"kind": "tool_result", "observation": {"text": STRINGS[1]}}}}
    body = jev.encode(state, QUESTIONS, root=tmp_path)
    assert OAUTH.encode() not in body and body.count(b"*" * len(OAUTH)) == 2
    sent = []

    async def post(endpoint, key, body, timeout):
        sent.append(body)
        raise jev.Unavailable("transport_error")

    monkeypatch.setattr(jev, "_post", post)
    with pytest.raises(jev.Unavailable, match="transport_error"):
        jev.evaluate(state, QUESTIONS, key="local-fixture-key", root=tmp_path)
    assert sent == [body]
