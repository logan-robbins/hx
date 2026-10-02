"""Local error transport for the installed Muse CLI, with no model service."""

import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def rejected_request(binary, home, work):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *args):
            pass

        def reply(self, status, body):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(200, b'{"object":"list","data":[{"id":"fixture-local","object":"model"}]}')

        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 <= size <= 2 * 1024 * 1024 or len(calls) >= 8:
                self.send_error(413)
                return
            self.rfile.read(size)
            calls.append(self.path)
            self.reply(400, b'{"error":{"type":"invalid_request_error","message":"HX local fixture rejected request"}}')

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {key: os.environ[key] for key in ("PATH", "TMPDIR") if key in os.environ}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home), XDG_DATA_HOME=str(home / "data"),
               MUSE_NO_AUTO_UPDATE="1", META_API_KEY="fixture-local-only")
    try:
        result = subprocess.run([binary, "exec", "--provider", "meta", "--base-url",
            "http://127.0.0.1:" + str(server.server_port) + "/v1", "--model", "fixture-local",
            "--max-model-steps", "1", "--no-foreign-personal-context", "--workspace", str(work),
            "Preserve this current task observation."], cwd=work, env=env,
            input="", capture_output=True, text=True, timeout=30)
        assert result.returncode != 0 and "HX local fixture rejected request" in result.stderr, result.stderr[-2000:]
        assert calls
        return result
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
