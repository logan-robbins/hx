"""Deterministic local Responses transport for the actual installed Codex CLI.

This is a provider fixture, not a substitute native executable or a model. It
requests one fixed printf command, then finishes; credentials are not loaded.
"""

import json
import os
import shlex
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def run(binary, home, work, scratch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            if size > 2 * 1024 * 1024 or len(requests) >= 2:
                self.send_error(413)
                return
            body = json.loads(self.rfile.read(size))
            requests.append({"path": self.path, "authorization": bool(self.headers.get("Authorization"))})
            if len(requests) == 1:
                items = [{"type": "reasoning", "id": "reason_contract", "summary": [{"type": "summary_text", "text": "PRIVATE REASONING"}],
                          "encrypted_content": "PRIVATE CIPHERTEXT"},
                         {"type": "function_call", "id": "fc_contract", "call_id": "call_contract", "name": "exec_command",
                          "arguments": json.dumps({"cmd": "printf native-tool-receipt", "max_output_tokens": 100}), "status": "completed"}]
            else:
                items = [{"type": "message", "id": "msg_contract", "role": "assistant", "status": "completed",
                          "content": [{"type": "output_text", "text": "Native transcript contract observed.", "annotations": []}]}]
            response = {"id": "response_contract_" + str(len(requests)), "object": "response", "created_at": 1, "status": "completed",
                "model": body.get("model"), "output": items, "usage": {"input_tokens": 12, "output_tokens": 6, "total_tokens": 18,
                    "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}
            events = [("response.created", {"response": {**response, "status": "in_progress", "output": []}})]
            for index, item in enumerate(items):
                events.append(("response.output_item.added", {"output_index": index, "item": item}))
                if item["type"] == "message":
                    events.append(("response.output_text.delta", {"output_index": index, "content_index": 0,
                        "item_id": item["id"], "delta": item["content"][0]["text"]}))
                events.append(("response.output_item.done", {"output_index": index, "item": item}))
            events.append(("response.completed", {"response": response}))
            wire = "".join("event: " + kind + "\ndata: " + json.dumps({"type": kind, **data}) + "\n\n" for kind, data in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    events_path = scratch / "native-hooks.jsonl"
    hook = scratch / "record-hook.py"
    hook.write_text("import json,sys,pathlib\np=pathlib.Path(" + repr(str(events_path)) + ")\n"
                    'with p.open("a") as out: out.write(json.dumps(json.load(sys.stdin))+"\\n")\n')
    command = shlex.join([sys.executable, str(hook)])
    config = ('model="gpt-5.4"\nmodel_provider="contract"\n[model_providers.contract]\n'
              'name="Local contract fixture"\nbase_url="http://127.0.0.1:' + str(server.server_port) + '/v1"\n'
              'wire_api="responses"\nrequires_openai_auth=false\nsupports_websockets=false\n')
    for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"):
        config += "\n[[hooks." + event + "]]\n[[hooks." + event + '.hooks]]\ntype="command"\ncommand=' + json.dumps(command) + "\n"
    (home / "config.toml").write_text(config)
    env = {key: os.environ[key] for key in ("PATH", "TMPDIR") if key in os.environ}
    env.update(HOME=str(scratch), CODEX_HOME=str(home))
    try:
        result = subprocess.run([binary, "exec", "--dangerously-bypass-hook-trust", "--skip-git-repo-check", "--ignore-rules",
            "--json", "-C", str(work), "Return the short contract observation."], env=env,
            input="", text=True, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr[-2000:]
        assert len(requests) == 2 and all(not request["authorization"] for request in requests)
        return [json.loads(line) for line in events_path.read_text().splitlines()]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
