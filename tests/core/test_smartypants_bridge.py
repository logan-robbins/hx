from __future__ import annotations

import json
import time

from hx.smartypants_bridge import submit


def test_human_chat_is_forwarded_to_the_configured_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    instance = tmp_path / "instance"
    (instance / "config").mkdir(parents=True)
    script = tmp_path / "record.mjs"
    script.write_text(
        "import fs from 'node:fs';\n"
        "let input = ''; for await (const chunk of process.stdin) input += chunk;\n"
        "fs.writeFileSync(process.env.SMARTPANTS_ROOT + '/received.json', input);\n"
    )
    (instance / "config/smartypants.json").write_text(
        json.dumps({"project_root": str(project), "hook_script": str(script)})
    )

    assert submit(instance, "hello")
    received = project / "received.json"
    for _ in range(100):
        if received.is_file():
            break
        time.sleep(0.02)
    assert json.loads(received.read_text()) == {"hook_event_name": "UserPromptSubmit", "prompt": "hello"}
