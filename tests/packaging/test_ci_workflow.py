"""`.github/workflows/ci.yml` — validated, then checked for the things gtm-2 requires of it.

**Which validator:** `actionlint` when it is on PATH (the real thing: schema plus shellcheck of
every `run:` block), otherwise the minimal structural YAML parser below. There is no PyYAML in
this repo's virtualenv and there never will be — hx is stdlib-only and `ORCHESTRATION.md` says
not to add dependencies — and `tomllib` cannot read YAML. So the fallback parses the subset of
YAML a workflow actually uses (block mappings, sequences of mappings, scalars, flow sequences,
block scalars) and rejects anything outside it, which is enough to catch the failure that
matters: a workflow that GitHub cannot load, discovered months later when someone pushes.

Two things the fallback deliberately does not do, so nobody mistakes it for a YAML library:
it does not resolve anchors or multi-document streams (a workflow has neither), and it keeps
`on:` as the string key `on` rather than applying YAML 1.1's bare-`on`-is-true rule.
"""

import pathlib
import re
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"

_KEY = re.compile(r"^(?P<key>[A-Za-z_][\w.\-]*|'[^']*'|\"[^\"]*\"):(?:\s+(?P<value>.*))?$")
_BLOCK_SCALAR = re.compile(r"^[|>][+-]?$")


class YamlStructureError(AssertionError):
    pass


def _scalar(text: str):
    text = text.strip()
    if text.startswith("#"):
        return ""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_scalar(p) for p in inner.split(",")] if inner else []
    if text in ("true", "false"):
        return text == "true"
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def parse_minimal_yaml(text: str):
    """Parse the subset of YAML a GitHub workflow uses. Raises on anything else."""
    lines = []
    for n, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw:
            raise YamlStructureError(f"line {n}: tab character; YAML indentation must be spaces")
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        lines.append((n, len(raw) - len(raw.lstrip(" ")), raw.strip()))

    pos = 0

    def parse_block(indent: int):
        nonlocal pos
        container = None
        while pos < len(lines):
            n, ind, content = lines[pos]
            if ind < indent:
                break
            if ind > indent:
                raise YamlStructureError(f"line {n}: unexpected indent {ind}, expected {indent}")

            if content.startswith("- "):
                if container is None:
                    container = []
                elif not isinstance(container, list):
                    raise YamlStructureError(f"line {n}: sequence item inside a mapping")
                item = content[2:].strip()
                m = _KEY.match(item)
                if m:
                    # `- key: value` opens a mapping whose indent is past the dash.
                    lines[pos] = (n, ind + 2, item)
                    container.append(parse_block(ind + 2))
                else:
                    pos += 1
                    container.append(_scalar(item))
                continue

            m = _KEY.match(content)
            if not m:
                raise YamlStructureError(f"line {n}: not `key:`, `key: value` or `- item`: {content!r}")
            if container is None:
                container = {}
            elif not isinstance(container, dict):
                raise YamlStructureError(f"line {n}: mapping key inside a sequence")
            key = _scalar(m.group("key"))
            value = m.group("value")
            pos += 1
            if value is None or value.strip() == "":
                nxt = lines[pos] if pos < len(lines) else None
                if nxt is not None and nxt[1] > ind:
                    container[key] = parse_block(nxt[1])
                else:
                    container[key] = None
            elif _BLOCK_SCALAR.match(value.strip()):
                body = []
                while pos < len(lines) and lines[pos][1] > ind:
                    body.append(lines[pos][2])
                    pos += 1
                container[key] = "\n".join(body)
            else:
                container[key] = _scalar(value)
        return container if container is not None else {}

    doc = parse_block(0)
    if pos != len(lines):
        n = lines[pos][0]
        raise YamlStructureError(f"line {n}: trailing content the parser could not place")
    return doc


def workflow() -> dict:
    return parse_minimal_yaml(WORKFLOW.read_text())


# -------------------------------------------------------------------- the validator


def test_the_workflow_is_valid_yaml():
    actionlint = shutil.which("actionlint")
    if actionlint is not None:
        r = subprocess.run([actionlint, str(WORKFLOW)], capture_output=True, text=True, cwd=REPO)
        assert r.returncode == 0, f"actionlint:\n{r.stdout}{r.stderr}"
        return
    # Fallback: the minimal structural parser above. Named in the assertion so a failure says
    # which validator produced it.
    try:
        doc = workflow()
    except YamlStructureError as exc:
        raise AssertionError(f"minimal structural YAML check (actionlint absent): {exc}") from exc
    assert isinstance(doc, dict) and doc, "the workflow did not parse to a mapping"


def test_the_minimal_parser_rejects_what_it_should():
    """A validator that accepts everything proves nothing about the file it validated."""
    with pytest.raises(YamlStructureError, match="tab"):
        parse_minimal_yaml("jobs:\n\tbad: 1\n")
    with pytest.raises(YamlStructureError, match="unexpected indent"):
        parse_minimal_yaml("a: 1\n    b: 2\n")
    with pytest.raises(YamlStructureError, match="not `key:`"):
        parse_minimal_yaml("just a bare sentence\n")
    # And it accepts the shapes a workflow really uses.
    doc = parse_minimal_yaml(
        "name: x\n"
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "jobs:\n"
        "  t:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - name: run\n"
        "        run: |\n"
        "          echo hi\n"
    )
    assert doc["on"]["push"]["branches"] == ["main"]
    assert doc["jobs"]["t"]["steps"][1]["run"] == "echo hi"


# ------------------------------------------------------------------- what CI must do


def test_both_operating_systems_are_in_the_matrix():
    os_list = workflow()["jobs"]["test"]["strategy"]["matrix"]["os"]
    assert sorted(os_list) == ["macos-latest", "ubuntu-latest"], os_list
    assert workflow()["jobs"]["test"]["strategy"]["fail-fast"] is False


def test_the_test_job_installs_tmux_and_uv_and_builds_the_venv():
    steps = workflow()["jobs"]["test"]["steps"]
    blob = "\n".join(str(s) for s in steps)
    assert "tmux" in blob, "hx drives real tmux sessions; a runner without tmux proves nothing"
    assert "astral-sh/setup-uv" in blob, "uv is needed for the packaging path"
    assert "python -m venv .venv" in blob, "ORCHESTRATION.md: the shared venv is .venv"
    assert "pip install -e ." in blob and "pip install pytest" in blob


def test_the_baseline_is_recorded_before_any_test_runs():
    steps = workflow()["jobs"]["test"]["steps"]
    names = [s.get("name", s.get("uses", "")) for s in steps]
    record = next(i for i, n in enumerate(names) if "baseline" in n.lower())
    guard = next(i for i, n in enumerate(names) if "guard" in n.lower())
    suite = next(i for i, n in enumerate(names) if "full suite" in n.lower())
    assert record < guard < suite, names
    body = steps[record]["run"]
    assert "tools/claude-home-hash.sh" in body
    assert ".baseline/claude-home.manifest" in body


def test_ci_runs_every_lane_s_tests_as_required():
    """`tools/milestone-check.sh <lane>` is the lane form: guard plus that lane's own paths,
    the rest advisory. CI is nobody's lane, so it runs the whole suite as required. Its
    no-lane form means "everything required" as of 2026-09-20 and would work — but it has
    already meant something else once, and CI should state its own requirement rather than
    inherit it (orchestrator, gtm-6: "keep CI on plain pytest anyway")."""
    blob = "\n".join(str(s) for s in workflow()["jobs"]["test"]["steps"])
    assert "pytest -q" in blob, "CI does not run the full suite"
    assert "tools/milestone-check.sh" not in blob, (
        "the lane-scoped script would under-run in CI; run pytest over everything instead"
    )
    assert "tests/guard" in blob, "the guard tests must still run on their own first"


def test_the_package_job_runs_both_release_scripts_and_verifies_the_units():
    blob = "\n".join(str(s) for s in workflow()["jobs"]["package"]["steps"])
    assert "packaging/e2e-install.sh" in blob
    assert "packaging/e2e-deploy.sh" in blob
    assert "systemd-analyze --user verify" in blob, (
        "the rendered systemd units are checked by a real systemd nowhere else"
    )


def test_the_package_job_runs_the_end_to_end_script_and_uploads_the_wheel():
    steps = workflow()["jobs"]["package"]["steps"]
    blob = "\n".join(str(s) for s in steps)
    assert "packaging/e2e-install.sh" in blob
    upload = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert upload, "the wheel is not uploaded as an artifact"
    assert upload[0]["with"]["path"] == "dist/", upload[0]


def test_ci_reads_but_never_writes_and_needs_no_secrets():
    doc = workflow()
    assert doc["permissions"] == {"contents": "read"}, doc.get("permissions")
    text = WORKFLOW.read_text()
    assert "secrets." not in text, "CI needs no secrets; publishing is a separate, later workflow"
