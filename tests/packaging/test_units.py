"""The boot and heartbeat unit templates, rendered and then linted (spec 17.2 step 5).

`src/hx/packaging/` ships inside the wheel so `hx install` can render these on a machine that
has only the installed tool. Checking the *templates* is not enough: a token that never gets
substituted, or a substitution that produces malformed XML or INI, is only visible after
rendering. So every test here renders first, with the same two keys and the same mechanism
`hx install` will use (`handoff/gtm-to-build.md`, gtm-2 entry 2):

    {HARNESS_ROOT}  absolute path of the instance
    {HX_BIN}        absolute path of the hx entry point (config/hx.json "hx_bin")

Substitution is **literal string replacement, not `str.format`** — these are plists, INI files
and shell-bearing comments, and a brace in a future comment would make `str.format` raise.

Then: `plutil -lint` on the plists where it exists (`plistlib` everywhere), an INI parse on the
systemd units, the 900 s heartbeat interval on both platforms, and that each unit execs
`hx up` or `hx heartbeat`.
"""

import configparser
import json
import pathlib
import plistlib
import re
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PACKAGING = REPO / "src" / "hx" / "packaging"

#: Stand-ins for what `hx install` substitutes. Deliberately not the paths of this machine.
SAMPLE_ROOT = "/srv/hx"
SAMPLE_BIN = "/opt/uv/tools/hx-harness/bin/hx"

HEARTBEAT_SECONDS = 900


def render(text: str) -> str:
    """Exactly what `hx install` does: replace the two tokens, literally."""
    return text.replace("{HARNESS_ROOT}", SAMPLE_ROOT).replace("{HX_BIN}", SAMPLE_BIN)


# Collection-time helpers: a missing directory must fail these tests, never interrupt
# collection. An exception raised while pytest builds parametrize lists aborts the whole
# session, taking every other lane's tests down with it (build lane, 2026-09-20).


def plists() -> list[pathlib.Path]:
    return sorted((PACKAGING / "launchd").glob("*.plist"))


def units() -> list[pathlib.Path]:
    directory = PACKAGING / "systemd"
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.suffix in (".service", ".timer"))


def _ids(paths):
    return [str(p.relative_to(REPO)) for p in paths]


def read_rendered_plist(path: pathlib.Path) -> dict:
    return plistlib.loads(render(path.read_text()).encode())


def read_rendered_unit(path: pathlib.Path) -> configparser.ConfigParser:
    """systemd units are INI. `#` starts a comment; duplicate keys are legal, so the parser is
    configured to keep the last one rather than raise."""
    parser = configparser.ConfigParser(
        strict=False, interpolation=None, comment_prefixes=("#", ";"),
    )
    parser.optionxform = str
    parser.read_string(render(path.read_text()), source=str(path))
    return parser


# ------------------------------------------------------------------ the files exist


def test_the_units_spec_17_2_names_all_ship_inside_the_package():
    # The parametrized tests below would silently pass on an empty list, so this one is what
    # actually catches a move or a deletion.
    assert (PACKAGING / "launchd").is_dir(), f"{PACKAGING / 'launchd'} is missing"
    assert (PACKAGING / "systemd").is_dir(), f"{PACKAGING / 'systemd'} is missing"
    assert [p.name for p in plists()] == ["com.hx.heartbeat.plist", "com.hx.up.plist"]
    assert [p.name for p in units()] == [
        "hx-heartbeat.service", "hx-heartbeat.timer", "hx-up.service",
    ]


def test_the_templates_live_in_the_package_not_the_repo_root():
    """`hx install` resolves them as package data; a repo-relative `packaging/` is not in the
    wheel. `packaging/` at the repo root keeps only the plan's scripts."""
    assert PACKAGING.is_dir(), f"{PACKAGING} is missing"
    root_packaging = REPO / "packaging"
    stray = sorted(
        p.name for p in (root_packaging.iterdir() if root_packaging.is_dir() else [])
        if p.is_dir() or p.suffix in (".plist", ".service", ".timer")
    )
    assert stray == [], f"unit templates left outside the wheel: {stray}"


# -------------------------------------------------------------------- substitution


@pytest.mark.parametrize("path", plists() + units(), ids=_ids(plists() + units()))
def test_template_uses_both_tokens_and_no_host_path(path):
    text = path.read_text()
    assert "{HARNESS_ROOT}" in text, f"{path}: nothing templated on HARNESS_ROOT"
    for bad in ("/Users/", "/home/", "/opt/homebrew", "/srv/hx"):
        assert bad not in text, f"{path}: hard-coded host path {bad!r}"


@pytest.mark.parametrize("path", plists() + units(), ids=_ids(plists() + units()))
def test_rendering_leaves_no_token_behind(path):
    out = render(path.read_text())
    leftovers = sorted(set(re.findall(r"\{[A-Za-z_][A-Za-z0-9_]*\}", out)))
    assert leftovers == [], f"{path}: unsubstituted after rendering: {leftovers}"
    assert SAMPLE_ROOT in out, f"{path}: HARNESS_ROOT did not reach the rendered output"


def test_only_the_two_documented_tokens_exist_across_every_template():
    """If a third token appears, `hx install` has to learn about it — and it will not, because
    the contract in handoff/gtm-to-build.md names exactly two."""
    found = set()
    for path in plists() + units():
        found |= set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", path.read_text()))
    assert found == {"HARNESS_ROOT", "HX_BIN"}, sorted(found)


# -------------------------------------------------------------------------- launchd


@pytest.mark.parametrize("path", plists(), ids=_ids(plists()))
def test_rendered_plist_parses(path):
    data = read_rendered_plist(path)
    assert data["Label"] == path.stem, f"{path}: Label {data['Label']!r} != {path.stem!r}"
    assert data["ProgramArguments"][0] == SAMPLE_BIN, data["ProgramArguments"]
    assert data["EnvironmentVariables"]["HARNESS_ROOT"] == SAMPLE_ROOT
    assert data["WorkingDirectory"] == SAMPLE_ROOT


@pytest.mark.parametrize("path", plists(), ids=_ids(plists()))
def test_rendered_plist_passes_plutil_lint_when_available(path, tmp_path):
    plutil = shutil.which("plutil")
    if plutil is None:
        pytest.skip("plutil is macOS-only; plistlib parsing of the rendered file covers the rest")
    rendered = tmp_path / path.name
    rendered.write_text(render(path.read_text()))
    r = subprocess.run([plutil, "-lint", str(rendered)], capture_output=True, text=True)
    assert r.returncode == 0, f"{path}: {r.stdout}{r.stderr}"


def test_rendered_up_plist_runs_hx_up_at_load():
    data = read_rendered_plist(PACKAGING / "launchd" / "com.hx.up.plist")
    assert data["ProgramArguments"] == [SAMPLE_BIN, "up"], data["ProgramArguments"]
    assert data["RunAtLoad"] is True
    assert "StartInterval" not in data, "hx up runs at login, not on a timer"


def test_rendered_heartbeat_plist_runs_hx_heartbeat_every_900_seconds():
    data = read_rendered_plist(PACKAGING / "launchd" / "com.hx.heartbeat.plist")
    assert data["ProgramArguments"] == [SAMPLE_BIN, "heartbeat"], data["ProgramArguments"]
    assert data["StartInterval"] == HEARTBEAT_SECONDS, data.get("StartInterval")
    assert data["RunAtLoad"] is False, "the heartbeat waits for its first interval"


# -------------------------------------------------------------------------- systemd


@pytest.mark.parametrize("path", units(), ids=_ids(units()))
def test_rendered_systemd_unit_parses_as_ini(path):
    parser = read_rendered_unit(path)
    assert "Unit" in parser, f"{path}: no [Unit] section"
    assert parser["Unit"].get("Description"), f"{path}: [Unit] has no Description"
    expected = {".service": "Service", ".timer": "Timer"}[path.suffix]
    assert expected in parser, f"{path}: no [{expected}] section"
    for section in parser.sections():
        assert section in ("Unit", "Service", "Timer", "Install"), f"{path}: [{section}]"


@pytest.mark.parametrize(
    "path", [p for p in units() if p.suffix == ".service"],
    ids=_ids([p for p in units() if p.suffix == ".service"]),
)
def test_rendered_systemd_service_execs_the_installed_binary(path):
    svc = read_rendered_unit(path)["Service"]
    command = svc["ExecStart"].split()
    assert command[0] == SAMPLE_BIN, svc["ExecStart"]
    assert command[1] in ("up", "heartbeat"), svc["ExecStart"]
    assert svc["Environment"] == f"HARNESS_ROOT={SAMPLE_ROOT}", svc.get("Environment")
    assert svc["WorkingDirectory"] == SAMPLE_ROOT, svc.get("WorkingDirectory")
    assert svc["Type"] == "oneshot", svc.get("Type")


def test_rendered_up_service_runs_hx_up_and_lets_tmux_outlive_it():
    svc = read_rendered_unit(PACKAGING / "systemd" / "hx-up.service")["Service"]
    assert svc["ExecStart"] == f"{SAMPLE_BIN} up"
    # The agents' tmux sessions must survive the oneshot unit exiting.
    assert svc.get("KillMode") == "none", svc.get("KillMode")
    assert svc.get("RemainAfterExit") == "yes", svc.get("RemainAfterExit")


def test_rendered_heartbeat_timer_fires_every_900_seconds():
    timer = read_rendered_unit(PACKAGING / "systemd" / "hx-heartbeat.timer")["Timer"]
    assert timer["OnUnitActiveSec"] == str(HEARTBEAT_SECONDS), timer.get("OnUnitActiveSec")
    assert timer["OnBootSec"] == str(HEARTBEAT_SECONDS), timer.get("OnBootSec")
    assert timer["Unit"] == "hx-heartbeat.service", timer.get("Unit")


def test_rendered_heartbeat_service_execs_hx_heartbeat():
    svc = read_rendered_unit(PACKAGING / "systemd" / "hx-heartbeat.service")["Service"]
    assert svc["ExecStart"] == f"{SAMPLE_BIN} heartbeat"


def test_only_the_up_service_and_the_timer_are_enabled_directly():
    # hx-heartbeat.service is triggered by its timer and must carry no [Install].
    assert "Install" in read_rendered_unit(PACKAGING / "systemd" / "hx-up.service")
    assert "Install" in read_rendered_unit(PACKAGING / "systemd" / "hx-heartbeat.timer")
    assert "Install" not in read_rendered_unit(PACKAGING / "systemd" / "hx-heartbeat.service")


# ------------------------------------------------ both platforms agree on the clock


def test_both_platforms_heartbeat_at_the_same_interval():
    launchd = read_rendered_plist(PACKAGING / "launchd" / "com.hx.heartbeat.plist")
    systemd = read_rendered_unit(PACKAGING / "systemd" / "hx-heartbeat.timer")["Timer"]
    assert launchd["StartInterval"] == int(systemd["OnUnitActiveSec"]) == HEARTBEAT_SECONDS


def test_both_platforms_cover_hx_up_and_hx_heartbeat_and_nothing_else():
    commands = set()
    for path in plists():
        commands.add(tuple(read_rendered_plist(path)["ProgramArguments"][1:]))
    for path in (p for p in units() if p.suffix == ".service"):
        commands.add(tuple(read_rendered_unit(path)["Service"]["ExecStart"].split()[1:]))
    assert commands == {("up",), ("heartbeat",)}, sorted(commands)


# ---------------------------------------------------------- tested Claude versions


def test_tested_claude_versions_ships_in_the_package_and_parses():
    path = PACKAGING / "tested-claude-versions.json"
    data = json.loads(path.read_text())
    assert set(data) == {"versions"}, sorted(data)
    versions = data["versions"]
    assert isinstance(versions, list) and versions, "the tested list must not be empty"
    for v in versions:
        assert isinstance(v, str) and re.fullmatch(r"\d+\.\d+\.\d+", v), (
            f"{v!r}: bare versions only, as CONTRACTS.md 'Claude Code version strings' pins"
        )
    assert len(set(versions)) == len(versions), "duplicate versions in the tested list"
