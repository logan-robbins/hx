"""`hx doctor` (spec 08, goal build-1 item 1).

It prints the resolved root, the Python version, the tmux version, and the pinned binary
from `config/claude.json` when present, and exits 0. `fail` is reserved for what M0 owns;
everything a later milestone brings is `warn` (handoff/orchestrator-to-build.md answer 5).
"""

from __future__ import annotations

import json

import pytest

from hx.doctor import FAIL, OK, WARN, bare_claude_version, run_checks


def statuses(checks):
    return {(check, status) for status, check, _ in checks}


def detail_for(checks, name):
    return [detail for _, check, detail in checks if check == name]


def test_bare_claude_version_strips_the_suffix():
    """CONTRACTS.md "Claude Code version strings"."""
    assert bare_claude_version("2.1.278 (Claude Code)\n") == "2.1.278"
    assert bare_claude_version("2.1.278") == "2.1.278"


def test_reports_root_python_tmux_and_git(instance):
    checks = run_checks(instance)
    names = {check for _, check, _ in checks}
    assert {"root", "python", "tmux", "git"} <= names
    assert detail_for(checks, "root") == [str(instance)]
    assert all(status != FAIL for status, check, _ in checks if check in ("python", "tmux", "git"))


def test_reports_the_pinned_binary(instance):
    checks = run_checks(instance)
    assert ("claude", OK) in statuses(checks), [c for c in checks if c[1] == "claude"]
    detail = next(c[2] for c in checks if c[1] == "claude")
    assert "(tested)" in detail, detail  # the fake's version is in the live-verified list


def _no_live_server(monkeypatch):
    """Live tmux sessions are environmental noise: these tests assert on a scratch
    instance, so point tmux at a server that cannot exist."""
    monkeypatch.setenv("HX_TMUX", "tmux -L hx-doctor-no-server")


def test_an_unpinned_instance_only_warns(instance, monkeypatch):
    _no_live_server(monkeypatch)
    (instance / "config" / "claude.json").unlink()
    checks = run_checks(instance)
    assert ("claude", WARN) in statuses(checks)
    assert not [c for c in checks if c[0] == FAIL]


def test_a_pinned_binary_that_is_gone_fails(instance):
    (instance / "config" / "claude.json").write_text(json.dumps({"bin": "/nope/claude", "version": "9"}))
    checks = run_checks(instance)
    assert ("claude", FAIL) in statuses(checks)


def test_missing_skeleton_files_are_warnings_not_failures(instance, monkeypatch):
    """The gtm lane owns these; doctor reports each missing one (goal build-1 item 2)."""
    _no_live_server(monkeypatch)
    (instance / "PARTNER.md").unlink()
    (instance / "templates" / "worker" / "harness.json").unlink()
    checks = run_checks(instance)
    warned = {detail for status, check, detail in checks if check == "skeleton" and status == WARN}
    assert "PARTNER.md missing" in warned
    assert "templates/worker/harness.json missing" in warned
    assert not [c for c in checks if c[0] == FAIL], [c for c in checks if c[0] == FAIL]


def test_a_missing_root_warns_and_says_what_to_run(run_hx, tmp_path):
    root = tmp_path / "not-yet"
    result = run_hx("doctor", env_extra={"HARNESS_ROOT": str(root)})
    assert result.returncode == 0
    assert "hx install --skeleton-only" in result.stdout


def test_a_broken_models_file_fails(instance):
    (instance / "config" / "models.json").write_text('{"m": {"window": 10, "threshold": 99}}')
    checks = run_checks(instance)
    assert ("models", FAIL) in statuses(checks)


def test_a_missing_token_fails(instance):
    """Tightened in build-4: an instance with no token cannot launch anything."""
    (instance / "seed" / "token").unlink()
    assert ("token", FAIL) in statuses(run_checks(instance))


def test_a_home_without_settings_fails(instance):
    home = instance / "run" / "partner" / "home"
    home.mkdir(parents=True)
    assert ("home:partner", FAIL) in statuses(run_checks(instance))




def test_there_is_no_repo_check(instance):
    """`hx repo add`, the mirror and `config/repo.json` are gone (spec 14 D25)."""
    (instance / "config" / "repo.json").write_text('{"name": "product"}')
    assert [c for c in run_checks(instance) if c[1] == "repo"] == []


def test_a_token_readable_by_anyone_else_fails(instance):
    """CONTRACTS.md `seed/token`: mode 0600."""
    (instance / "seed" / "token").chmod(0o644)
    checks = run_checks(instance)
    assert ("token", FAIL) in statuses(checks)


def test_an_empty_token_fails(instance):
    (instance / "seed" / "token").write_text("\n")
    assert ("token", FAIL) in statuses(run_checks(instance))


def test_a_good_token_is_ok(instance):
    assert ("token", OK) in statuses(run_checks(instance))


def test_a_fresh_skeleton_root_says_what_is_still_missing(run_hx, tmp_path):
    """A `--skeleton-only` root is not a working instance: no token, no homes (spec 17.2)."""
    root = tmp_path / "scratch"
    created = run_hx("install", "--skeleton-only", "--root", str(root))
    assert created.returncode == 0, created.stderr
    result = run_hx("doctor", env_extra={"HARNESS_ROOT": str(root)})
    assert result.returncode == 1, result.stdout
    assert "seed/token" in result.stdout, "it names the one thing the human has to do"
    assert str(root) in result.stdout
    assert "tmux" in result.stdout and "python" in result.stdout


def test_json_form(run_hx, instance):
    result = run_hx("doctor", "--json", env_extra={"HARNESS_ROOT": str(instance)})
    assert result.returncode in (0, 1), result.stderr
    payload = json.loads(result.stdout)
    assert payload["root_abs"] == str(instance)
    assert {c["check"] for c in payload["checks"]} >= {"root", "python", "tmux", "git"}
    assert {c["status"] for c in payload["checks"]} <= {OK, WARN, FAIL}


# --- build-8 item 9: the pre-seeded first-launch state ------------------------------------------


def seed_home(instance, item_id="partner", **state):
    import json

    home = instance / "run" / item_id / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text("{}\n")
    if state:
        (home / ".claude.json").write_text(json.dumps(state))
    return home


def home_checks(instance, item_id="partner"):
    return [c for c in run_checks(instance) if c[1] == f"home:{item_id}"]


def test_doctor_fails_when_the_first_launch_state_is_missing(instance):
    """spec 08: each home's settings **and its pre-seeded first-launch state file**."""
    seed_home(instance)
    failures = [c for c in home_checks(instance) if c[0] == FAIL]
    assert failures and ".claude.json missing" in failures[0][2]


def test_doctor_fails_when_onboarding_is_not_marked_complete(instance):
    seed_home(instance, projects={"/w": {"hasTrustDialogAccepted": True}})
    failures = [c for c in home_checks(instance) if c[0] == FAIL]
    assert failures and "hasCompletedOnboarding" in failures[0][2]


def test_doctor_fails_when_no_workspace_trust_is_accepted(instance):
    seed_home(instance, hasCompletedOnboarding=True, projects={})
    failures = [c for c in home_checks(instance) if c[0] == FAIL]
    assert failures and "trust" in failures[0][2].lower()


def test_doctor_passes_a_properly_seeded_home(instance):
    seed_home(
        instance,
        hasCompletedOnboarding=True,
        projects={"/w": {"hasTrustDialogAccepted": True, "hasClaudeMdExternalIncludesApproved": True}},
    )
    assert [c for c in home_checks(instance) if c[0] == FAIL] == []


def _codex_home(instance, config_text):
    from hx.doctor import _codex_home

    home = instance / "run" / "eng-001" / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(config_text)
    return _codex_home(instance, home, "eng-001")


def test_codex_instance_without_a_token_fails(instance):
    from hx.doctor import _codex_instance

    assert ("codex-token", FAIL) in statuses(_codex_instance(instance, []))


def test_codex_instance_with_token_and_pin_passes(instance, tmp_path):
    from hx.doctor import _codex_instance

    token = instance / "seed" / "codex-token"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text("sk-test\n")
    token.chmod(0o600)
    probe = tmp_path / "codex"
    probe.write_text("#!/bin/sh\nexit 0\n")
    probe.chmod(0o755)
    (instance / "config" / "codex.json").write_text(
        json.dumps({"bin": str(probe), "version": "0.156.1"})
    )
    assert ("codex-token", OK) in statuses(_codex_instance(instance, []))
    assert ("codex", OK) in statuses(_codex_instance(instance, []))


def test_codex_instance_accepts_login_homes_without_a_token(instance):
    """ChatGPT-login homes need no seed token (spec 11)."""
    from hx.doctor import _codex_instance

    home = instance / "run" / "eng-001" / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text('{"auth_mode": "chatgpt"}\n')
    assert ("codex-token", OK) in statuses(_codex_instance(instance, ["eng-001"]))


CODEX_POSTURE = 'approval_policy = "never"\nsandbox_mode = "danger-full-access"\n'
CODEX_HOOKS = "[[hooks.SessionStart]]\n[[hooks.PostToolUse]]\n[[hooks.Stop]]\n"
TOOLBOX = "https://acct.services.ai.azure.com/api/projects/p/toolboxes/t/mcp?api-version=v1"


def test_codex_home_checks_posture_and_hooks(instance):
    good = _codex_home(instance, CODEX_POSTURE + 'web_search = "cached"\n' + CODEX_HOOKS)
    assert not [c for c in good if c[0] == FAIL]
    bad = _codex_home(instance, 'approval_policy = "never"\n')
    assert [c for c in bad if c[0] == FAIL]


def test_codex_home_without_a_search_guard_requires_cached_hosted_search(instance):
    """docs/codex-search-guard.md §4.6: a full-access sandbox would default it to live."""
    good = _codex_home(instance, CODEX_POSTURE + 'web_search = "cached"\n' + CODEX_HOOKS)
    assert (OK, "home:eng-001", "web search cached (hosted search, unguarded)") in good
    for posture in ('web_search = "live"\n', ""):
        bad = _codex_home(instance, CODEX_POSTURE + posture + CODEX_HOOKS)
        assert [detail for status, _, detail in bad if status == FAIL] == [
            "config.toml `web_search` is not `cached`; `hx launch` rewrites it"]


def test_codex_home_with_a_search_guard_requires_the_guarded_toolbox(instance):
    (instance / "config" / "codex.json").write_text(json.dumps({"search_guard": {
        "toolbox_url": TOOLBOX, "content_safety_endpoint": "https://cs.cognitiveservices.azure.com"}}))
    search = '[[hooks.{0}]]\nmatcher = "mcp__foundry__web_search"\n[[hooks.{0}.hooks]]\ntype = "command"\n'
    guarded = {"web_search": 'web_search = "disabled"\n', "server": f'[mcp_servers.foundry]\nurl = "{TOOLBOX}"\n',
               "pre": search.format("PreToolUse"), "post": search.format("PostToolUse")}
    good = _codex_home(instance, CODEX_POSTURE + "".join(guarded.values()) + CODEX_HOOKS)
    assert (OK, "home:eng-001", "web search guarded (foundry toolbox)") in good
    assert not [c for c in good if c[0] == FAIL]
    for broken in ({"web_search": 'web_search = "cached"\n'}, {"server": '[mcp_servers.foundry]\nurl = "x"\n'},
                   {"pre": ""}, {"post": search.format("PostToolUse").replace("mcp__foundry__", "")}):
        bad = _codex_home(instance, CODEX_POSTURE + "".join({**guarded, **broken}.values()) + CODEX_HOOKS)
        assert [detail for status, _, detail in bad if status == FAIL] == [
            "config.toml does not route web search through the guarded foundry toolbox that "
            "config/codex.json `search_guard` names; `hx launch` rewrites it"], broken


@pytest.mark.parametrize("search_guard, named", [
    ({"toolbox_url": TOOLBOX}, "content_safety_endpoint"),
    ({"content_safety_endpoint": "https://cs.example"}, "toolbox_url"),
    ({}, "toolbox_url and content_safety_endpoint"),
    ("on", "toolbox_url and content_safety_endpoint"),
])
def test_codex_instance_names_a_missing_search_guard_key(instance, search_guard, named):
    from hx.doctor import _codex_instance

    (instance / "config" / "codex.json").write_text(json.dumps({"bin": "/bin/sh", "search_guard": search_guard}))
    checks = _codex_instance(instance, [])
    assert (FAIL, "codex", f"config/codex.json `search_guard` lacks {named}; install.sh refuses it") in checks


def test_a_versioned_binary_path_fails(instance):
    """A launcher's auto-update deletes the old version; a path that names one dies with it."""
    (instance / "config" / "meta.json").write_text(json.dumps({
        "bin": str(instance / "muse-bin-1.4.3-R5018.1" / "muse"), "version": "1.4.3"}))
    checks = run_checks(instance)
    assert ("bin-path", FAIL) in statuses(checks), detail_for(checks, "bin-path")


def test_a_stable_launcher_path_passes(instance):
    launcher = instance / "bin" / "muse"
    launcher.parent.mkdir(exist_ok=True)
    launcher.write_text("#!/bin/sh\n")
    (instance / "config" / "meta.json").write_text(json.dumps({"bin": str(launcher), "version": "1.4.3"}))
    checks = run_checks(instance)
    assert "bin-path" not in {check for _, check, _ in checks}


def test_reports_the_learnings_directory(instance):
    checks = run_checks(instance)
    assert ("learnings", OK) in statuses(checks), detail_for(checks, "learnings")
    assert "0 rule(s) (none), 0 pending" in detail_for(checks, "learnings")[0]


def test_an_instance_on_a_shared_directory_is_not_asked_for_its_own_skeleton_files(instance, tmp_path):
    """A fleet instance points `learnings` at the shared directory; its unused local
    LEARNINGS/ skeleton is not a warning (docs/multi-instance.md §4)."""
    import shutil
    shared = tmp_path / "fleet" / "LEARNINGS"
    shutil.copytree(instance / "LEARNINGS", shared)
    shutil.rmtree(instance / "LEARNINGS")
    hx_json = instance / "config" / "hx.json"
    recorded = json.loads(hx_json.read_text()) if hx_json.is_file() else {}
    recorded["learnings"] = str(shared)
    hx_json.write_text(json.dumps(recorded))
    checks = run_checks(instance)
    assert not [d for s, c, d in checks if c == "skeleton" and "LEARNINGS/" in d], \
        [d for s, c, d in checks if c == "skeleton" and "LEARNINGS/" in d]
    assert ("learnings", OK) in statuses(checks)
    assert str(shared) in detail_for(checks, "learnings")[0]




def test_complete_context_past_one_hook_string_bound_is_delivered_without_warning(instance, monkeypatch):
    """Automatic full transport has multiple native strings and no model file walk."""
    from hx import learn, persona
    largest = max(persona.boundary(instance, item_id)["bytes"] for item_id in ("eng-001", "partner"))
    monkeypatch.setattr(persona, "HOOK_BYTES_BY_FLAVOR", {"claude": largest + 200})
    assert ("hook-size", WARN) not in statuses(run_checks(instance))
    proposal = learn.propose(instance, signal="x" * 400, check="c", evidence="e", category="build",
                             role="engineer", env={"HARNESS_ID": "eng-001"})
    learn.accept(instance, proposal["id"], env={"HARNESS_ID": "partner"})
    checks = run_checks(instance)
    assert ("hook-size", WARN) not in statuses(checks)
    assert 'x' * 400 in persona.boundary(instance, 'eng-001')['text']


def test_complete_context_total_overflow_names_every_affected_agent(instance, monkeypatch):
    from hx import token_controls
    original = token_controls.limits
    def small(root, agent):
        return {**original(root, agent), 'threshold': 1}
    monkeypatch.setattr(token_controls, 'limits', small)
    checks = run_checks(instance)
    assert ("hook-size", WARN) in statuses(checks)
    detail = '\n'.join(detail_for(checks, 'hook-size'))
    assert 'partner:' in detail and 'eng-001:' in detail
    assert 'complete' in detail and 'budget' in detail


# --- skills (TOOL_SKILL_PLAN step 1) -----------------------------------------------------------


def _skill(directory, name, *, description="Use when testing.", body="Body.\n", extra=""):
    (directory / name).mkdir(parents=True, exist_ok=True)
    text = f"---\nname: {name}\ndescription: {description}\n{extra}---\n\n{body}"
    (directory / name / "SKILL.md").write_text(text)


def test_the_shipped_skills_pass_the_skills_row(instance, monkeypatch):
    monkeypatch.delenv("HX_SKILLS_DIR", raising=False)
    checks = run_checks(instance)
    assert [s for s, c, _ in checks if c == "skills"] == [OK], detail_for(checks, "skills")
    assert "valid" in detail_for(checks, "skills")[0]


def test_a_malformed_skill_fails_the_skills_row(instance, monkeypatch, tmp_path):
    skills = tmp_path / "skills"
    _skill(skills, "hx-good", extra="metadata:\n  hx-audience: worker\n  hx-roles: \"*\"\n")
    _skill(skills, "Bad--Name")                               # uppercase and a double hyphen
    _skill(skills, "hx-mismatch")
    (skills / "hx-mismatch" / "SKILL.md").write_text("---\nname: hx-other\ndescription: d\n---\nb\n")
    _skill(skills, "hx-long", description="x" * 1025)          # description over 1,024
    _skill(skills, "hx-big", body="y" * (32 * 1024 + 1))       # file over 32 KiB
    _skill(skills, "hx-ext", extra="model: opus\n")            # not a portable field
    _skill(skills, "hx-leaky", body="Then run `hx dispatch eng-002 goal.md` yourself.\n")
    names = ["hx-good", "Bad--Name", "hx-mismatch", "hx-long", "hx-big", "hx-ext", "hx-leaky"]
    (skills / "manifest.json").write_text(json.dumps({
        name: {"audiences": ["worker"], "roles": ["*"], "optional": False}
        for name in names if name != "Bad--Name"
    }))
    monkeypatch.setenv("HX_SKILLS_DIR", str(skills))
    checks = run_checks(instance)
    rows = [(s, d) for s, c, d in checks if c == "skills"]
    assert rows and all(status == FAIL for status, _ in rows), rows
    text = "\n".join(d for _, d in rows)
    assert "Bad--Name: not in manifest.json" in text
    assert "Bad--Name: name 'Bad--Name' must be 1-64 lowercase" in text
    assert "hx-mismatch: name 'hx-other' does not match its directory" in text
    assert "hx-long: description is 1025 characters" in text
    assert "hx-big: SKILL.md is" in text and "32 KiB" in text
    assert "hx-ext: non-portable field(s) model" in text
    assert "hx-leaky: a worker carries it, and it names `hx dispatch`" in text
    assert "hx-good" not in text


def test_config_hx_json_skills_must_name_optional_shipped_skills(instance, monkeypatch):
    monkeypatch.delenv("HX_SKILLS_DIR", raising=False)
    hx_json = instance / "config" / "hx.json"
    hx_json.write_text(json.dumps({**json.loads(hx_json.read_text()), "skills": ["hx-nope", "hx-worker"]}))
    text = "\n".join(detail_for(run_checks(instance), "skills"))
    assert "names `hx-nope`, which does not ship" in text
    assert "names `hx-worker`, which is not optional" in text


# --- skill isolation (TOOL_SKILL_PLAN step 7, docs/two-worlds.md) -------------------------------


def _isolation(checks):
    return [(status, detail) for status, check, detail in checks if check == "skills-isolation"]


def _flavor(instance, item_id, flavor):
    harness = instance / "config" / item_id / "harness.json"
    harness.write_text(json.dumps({**json.loads(harness.read_text()), "flavor": flavor}))


@pytest.mark.parametrize("relative", [".claude/skills", ".agents/skills", ".pi/skills"])
def test_a_worker_workdir_with_checkout_skills_warns(instance, monkeypatch, tmp_path, relative):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (instance / "wt" / "eng-001" / relative / "deploy").mkdir(parents=True)
    rows = _isolation(run_checks(instance))
    assert [status for status, _ in rows] == [WARN], rows
    assert "eng-001" in rows[0][1] and relative in rows[0][1]


def test_no_isolation_row_when_nothing_could_leak(instance, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home" / ".agents" / "skills" / "mine").mkdir(parents=True)  # claude-only fleet
    assert _isolation(run_checks(instance)) == []


@pytest.mark.parametrize("flavor", ["codex", "pi"])
def test_a_host_agents_skills_dir_warns_while_a_codex_or_pi_worker_is_configured(
        instance, monkeypatch, tmp_path, flavor):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    _flavor(instance, "eng-001", flavor)
    (home / ".agents" / "skills").mkdir(parents=True)
    assert _isolation(run_checks(instance)) == [], "an empty directory loads nothing"
    (home / ".agents" / "skills" / "mine").mkdir()
    rows = _isolation(run_checks(instance))
    assert [status for status, _ in rows] == [WARN], rows
    assert ".agents/skills" in rows[0][1] and "eng-001" in rows[0][1]


def test_the_partners_directory_is_not_a_worker_workdir(instance, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    harness = instance / "config" / "partner" / "harness.json"
    partner_dir = tmp_path / "partner-wd"
    (partner_dir / ".claude" / "skills").mkdir(parents=True)
    harness.write_text(json.dumps({**json.loads(harness.read_text()), "workdir": str(partner_dir)}))
    assert _isolation(run_checks(instance)) == []


def test_the_shipped_skills_pass_the_lint_one_by_one():
    """Every shipped skill passes `validate_skill`, the worker lint for its audiences, and the
    format rules of TOOL_SKILL_PLAN §Skills To Add that the new skills carry."""
    from hx import skill_manifest
    from hx.cli import AUDIENCES

    directory = skill_manifest.package_dir()
    manifest = skill_manifest.load(directory)
    for name, row in manifest.items():
        path = directory / name / "SKILL.md"
        assert skill_manifest.validate_skill(directory / name) == [], name
        if any(a in skill_manifest.WORKER_AUDIENCES for a in row["audiences"]):
            assert skill_manifest.refused_mentions(
                path.read_text(), AUDIENCES, partner_sections="partner" in row["audiences"]) == [], name
    for name in ("hx-signoff", "hx-conversion"):
        text = (directory / name / "SKILL.md").read_text()
        fields, _ = skill_manifest.parse_frontmatter(text, name)
        assert len(text.encode()) <= skill_manifest.FACADE_BYTES, name
        assert {"hx-audience", "hx-roles", "hx-loads-when"} <= set(fields["metadata"]), name
        for banned in ("context: fork", "disable-model-invocation", "\npaths:", "!`"):
            assert banned not in text, (name, banned)


def test_a_partner_only_section_is_exempt_only_in_a_skill_the_partner_also_carries(tmp_path):
    from hx import skill_manifest
    from hx.cli import AUDIENCES

    text = ("# x\n\n## Workers\n\nRun `hx code contract X`.\n\n"
            "## Partner only: publish\n\n```bash\n# a comment, not a heading\n"
            "hx code contracts --register c.jsonl\n```\n\n## Back to workers\n\nRun `hx task`.\n")
    assert skill_manifest.refused_mentions(text, AUDIENCES) == ["code contracts"]
    assert skill_manifest.refused_mentions(text, AUDIENCES, partner_sections=True) == []
    leaked = text + "\nAnd `hx code contracts --register d.jsonl` here.\n"
    assert skill_manifest.refused_mentions(leaked, AUDIENCES, partner_sections=True) == ["code contracts"]
