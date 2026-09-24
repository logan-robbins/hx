"""Mirror human UI chat to an optional project Smartypants hook."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path


def submit(root: Path, text: str) -> bool:
    config_file = root / "config" / "smartypants.json"
    if not config_file.is_file():
        return False
    try:
        config = json.loads(config_file.read_text(encoding="utf-8"))
        project = Path(config["project_root"])
        script = Path(config["hook_script"])
    except (OSError, ValueError, TypeError, KeyError):
        return False
    if not project.is_absolute() or not project.is_dir() or not script.is_file():
        return False

    payload = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": text})

    def invoke() -> None:
        env = {**os.environ, "SMARTPANTS_ROOT": str(project)}
        try:
            subprocess.run(
                ["node", str(script)], input=payload, text=True, cwd=project,
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=25, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass  # Diagram work must never block a delivered Partner message.

    threading.Thread(target=invoke, name="hx-smartypants", daemon=True).start()
    return True
