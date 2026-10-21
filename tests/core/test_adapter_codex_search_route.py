"""The Codex search route around the guard (docs/codex-search-guard.md): the toolbox header
helper, the installer wiring both ways, and the real `codex exec` scenario."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ADAPTER = Path(__file__).resolve().parents[2] / "src" / "hx" / "skeleton" / "adapters" / "codex"
HEADERS = ADAPTER / "toolbox_headers.py"


def _headers(tmp_path, az_script=None, **env):
    """Run toolbox_headers.py with only a fake `az` (or none) on PATH."""
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir(exist_ok=True)
    if az_script is not None:
        (fakebin / "az").write_text("#!/bin/sh\n" + az_script)
        (fakebin / "az").chmod(0o755)
    return subprocess.run([sys.executable, str(HEADERS)], env={"PATH": str(fakebin), **env},
                          capture_output=True, text=True, timeout=30, check=False)


def test_headers_prefer_the_environment_token(tmp_path):
    done = _headers(tmp_path, "exit 9\n", FOUNDRY_TOOLBOX_TOKEN="stub-toolbox-token")
    assert (done.returncode, done.stderr) == (0, "")
    assert json.loads(done.stdout) == {"Authorization": "Bearer stub-toolbox-token"}


def test_headers_fall_back_to_an_az_token_for_the_foundry_resource(tmp_path):
    record = tmp_path / "az-argv"
    done = _headers(tmp_path, f'printf "%s\\n" "$@" > {record}\necho stub-az-token\n')
    assert json.loads(done.stdout) == {"Authorization": "Bearer stub-az-token"}
    assert record.read_text().split() == [
        "account", "get-access-token", "--resource", "https://ai.azure.com", "--query", "accessToken", "-o", "tsv"]


@pytest.mark.parametrize("az_script", [None, "echo 'az: not logged in (stub)' >&2\nexit 1\n", "exit 0\n"])
def test_headers_fail_with_one_stderr_line_and_no_stdout(tmp_path, az_script):
    done = _headers(tmp_path, az_script)
    assert (done.returncode, done.stdout) == (1, "")
    assert done.stderr.startswith("toolbox_headers: ") and done.stderr.count("\n") == 1
