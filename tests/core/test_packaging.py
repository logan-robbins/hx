"""`hx install` end to end, the mirror, the sparse worktrees, `hx push`, `hx upgrade`.

Spec 17.2 (all six steps), 17.3, 17.6, 08. Everything runs against a fake `claude` and a HOME
under `tmp_path`: the user's real `~/.claude` is never read, which `tests/guard` also proves
from the outside and `test_no_test_reads_the_users_claude_home` proves from the inside.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import SRC, clean_env

TESTED = json.loads((SRC / "hx" / "packaging" / "tested-claude-versions.json").read_text())["versions"]


def git(*args, cwd=None, check=True):
    return subprocess.run(
        ["git", *args], cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=check
    )


@pytest.fixture
def product_repo(tmp_path):
    """A bare product repo whose working tree carries its own `.claude/` (spec 17.3)."""
    source = tmp_path / "product-src"
    (source / "src").mkdir(parents=True)
    (source / "src" / "app.py").write_text("print('hello')\n")
    (source / "README.md").write_text("# product\n")
    claude_dir = source / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text('{"hooks": {}}\n')
    (claude_dir / "notes.md").write_text("REPO-CLAUDE-DIR\n")

    git("init", "-q", "-b", "main", str(source))
    for key, value in (("user.email", "p@example.invalid"), ("user.name", "product")):
        git("-C", str(source), "config", key, value)
    git("-C", str(source), "add", "-A")
    git("-C", str(source), "commit", "-qm", "product")

    bare = tmp_path / "product.git"
    git("clone", "-q", "--bare", str(source), str(bare))
    return bare


def run_install(tmp_path, root, fake_claude_on_path, *args, home=None, tmux=None):
    """Run `hx install`.

    Step 6 is `hx launch partner`, so a full install starts a real tmux session. `tmux` points
    it at the test's private server; without it the session would land on the machine's default
    server, where the lanes themselves run — which is exactly how one leaked in build-3.
    """
    home = home or (tmp_path / "install-home")
    home.mkdir(parents=True, exist_ok=True)
    env = clean_env(HOME=str(home), **fake_claude_on_path)
    if tmux:
        env["HX_TMUX"] = " ".join(tmux)
    return subprocess.run(
        [sys.executable, "-m", "hx", "install", "--root", str(root), *args],
        env=env, capture_output=True, text=True,
    )


# --- step 1: preflight ---------------------------------------------------------------------


def test_step_1_pins_a_bare_tested_version(tmp_path, fake_claude_on_path, tmux_server):
    root = tmp_path / "instance"
    result = run_install(tmp_path, root, fake_claude_on_path, "--skeleton-only")
    assert result.returncode == 0, result.stdout + result.stderr
    # --skeleton-only stops before step 1's pin is needed, so pin it the normal way:
    (root / "seed").mkdir(parents=True, exist_ok=True)
    (root / "seed" / "token").write_text("sk-ant-oat-x\n")
    (root / "seed" / "token").chmod(0o600)
    result = run_install(tmp_path, root, fake_claude_on_path, tmux=tmux_server)
    pin = json.loads((root / "config" / "claude.json").read_text())
    assert pin["version"] == TESTED[0]
    assert "(Claude Code)" not in pin["version"], "the version is bare (CONTRACTS.md)"
    assert Path(pin["bin"]).is_file()


def test_step_1_refuses_an_untested_version(tmp_path):
    root = tmp_path / "instance"
    bindir = tmp_path / "oldbin"
    bindir.mkdir()
    binary = bindir / "claude"
    binary.write_text('#!/usr/bin/env bash\necho "1.0.0 (Claude Code)"\n')
    binary.chmod(0o755)
    home = tmp_path / "h"
    home.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "hx", "install", "--root", str(root)],
        env=clean_env(HOME=str(home), PATH=f"{bindir}:{os.environ.get('PATH', '')}"),
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "not in this package's tested list" in result.stderr
    assert TESTED[0] in result.stderr, "it says which version to install"


def test_step_1_takes_an_explicit_binary(tmp_path, fake_claude_on_path):
    root = tmp_path / "instance"
    binary = Path(fake_claude_on_path["PATH"].split(":")[0]) / "claude"
    home = tmp_path / "h2"
    home.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "hx", "install", "--root", str(root), "--claude", str(binary),
         "--skeleton-only"],
        env=clean_env(HOME=str(home)), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- step 3: the seed token ------------------------------------------------------------------


def test_step_3_stops_until_the_human_pastes_the_token(tmp_path, fake_claude_on_path):
    from hx.install import TOKEN_WAIT_EXIT

    root = tmp_path / "instance"
    result = run_install(tmp_path, root, fake_claude_on_path)
    assert result.returncode == TOKEN_WAIT_EXIT
    assert "claude setup-token" in result.stdout
    assert "~/.claude" in result.stdout, "it says hx reads nothing of the user's"


def test_step_3_tightens_the_tokens_mode(tmp_path, fake_claude_on_path, tmux_server):
    root = tmp_path / "instance"
    run_install(tmp_path, root, fake_claude_on_path)
    token = root / "seed" / "token"
    token.write_text("sk-ant-oat-x\n")
    token.chmod(0o644)
    run_install(tmp_path, root, fake_claude_on_path, tmux=tmux_server)
    assert token.stat().st_mode & 0o777 == 0o600


# --- step 5: the units -------------------------------------------------------------------------


def test_step_5_renders_the_units_into_this_home_without_enabling_them(
    tmp_path, fake_claude_on_path, tmux_server
):
    from hx import units

    root = tmp_path / "instance"
    home = tmp_path / "unit-home"
    run_install(tmp_path, root, fake_claude_on_path, home=home)
    token = root / "seed" / "token"
    token.write_text("sk-ant-oat-x\n")
    token.chmod(0o600)
    result = run_install(tmp_path, root, fake_claude_on_path, home=home, tmux=tmux_server)

    directory = units.target_dir(home)
    assert directory.is_dir(), result.stdout + result.stderr
    for name in units.unit_names():
        rendered = (directory / name).read_text()
        assert "{HARNESS_ROOT}" not in rendered and "{HX_BIN}" not in rendered
        assert str(root) in rendered
    # hx prints what the human runs; it never runs it.
    assert "hx does not enable them" in result.stdout
    for command in units.enable_commands(home):
        assert command in result.stdout


def test_unit_substitution_is_literal_not_format(tmp_path):
    """A brace in a future comment must not raise (handoff/gtm-to-build.md)."""
    from hx.units import render

    template = "cmd {HX_BIN} --root {HARNESS_ROOT}\n# a shell brace: ${FOO:-bar} and {weird}\n"
    rendered = render(template, root=Path("/srv/hx"), hx_bin="/usr/bin/hx")
    assert "cmd /usr/bin/hx --root /srv/hx" in rendered
    assert "${FOO:-bar}" in rendered and "{weird}" in rendered


# --- hx repo add and the sparse worktree ---------------------------------------------------------


def test_repo_add_mirrors_and_records(instance, hx, product_repo):
    result = hx("repo", "add", str(product_repo))
    assert result.returncode == 0, result.stderr
    config = json.loads((instance / "config" / "repo.json").read_text())
    assert set(config) == {"name", "upstream", "base_branch", "keep_claude_dir"}
    assert config["name"] == "product" and config["base_branch"] == "main"
    assert config["keep_claude_dir"] is False

    mirror = instance / "repos" / "product.git"
    assert git("-C", str(mirror), "rev-parse", "--is-bare-repository").stdout.strip() == "true"
    assert git("-C", str(mirror), "remote", "get-url", "upstream").stdout.strip()


def test_repo_add_refuses_a_second_repo(instance, hx, product_repo):
    assert hx("repo", "add", str(product_repo)).returncode == 0
    result = hx("repo", "add", str(product_repo))
    assert result.returncode == 1 and "refuse" in result.stderr


def test_the_worktree_is_sparse_and_has_no_claude_dir(instance, hx, product_repo, agent):
    """spec 17.3: the repo's own `.claude/` never lands in a harness worktree."""
    assert hx("repo", "add", str(product_repo)).returncode == 0
    import shutil

    shutil.rmtree(instance / "wt" / "eng-001")
    result = hx("launch", "eng-001")
    assert result.returncode == 0, result.stdout + result.stderr

    workdir = instance / "wt" / "eng-001"
    assert (workdir / "src" / "app.py").is_file(), "the product is checked out"
    assert (workdir / "README.md").is_file()
    assert not (workdir / ".claude").exists(), "the repo's .claude/ is excluded"

    mirror = instance / "repos" / "product.git"
    listed = git("-C", str(mirror), "ls-tree", "-r", "--name-only", "main").stdout
    assert ".claude/settings.json" in listed, "and it is still in the mirror"


def test_keep_claude_dir_opts_back_in(instance, hx, product_repo):
    assert hx("repo", "add", str(product_repo)).returncode == 0
    config_path = instance / "config" / "repo.json"
    config = json.loads(config_path.read_text())
    config["keep_claude_dir"] = True
    config_path.write_text(json.dumps(config))

    import shutil

    shutil.rmtree(instance / "wt" / "eng-001")
    assert hx("launch", "eng-001").returncode == 0
    assert (instance / "wt" / "eng-001" / ".claude" / "notes.md").is_file()


def test_the_worktree_is_on_the_agents_own_branch(instance, hx, product_repo):
    assert hx("repo", "add", str(product_repo)).returncode == 0
    import shutil

    shutil.rmtree(instance / "wt" / "eng-001")
    assert hx("launch", "eng-001").returncode == 0
    workdir = instance / "wt" / "eng-001"
    branch = git("-C", str(workdir), "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    # `config/<id>/harness.json` names it; the default is `hx/<id>` (goal build-4 item 2).
    assert branch == "agent/eng-001"
    mirror = instance / "repos" / "product.git"
    assert git("-C", str(mirror), "rev-parse", "--verify", branch).returncode == 0


def test_dispatch_resets_the_worktree_to_the_base_branch(instance, hx, product_repo, orders, launched):
    import shutil

    assert hx("repo", "add", str(product_repo)).returncode == 0
    shutil.rmtree(instance / "wt" / "eng-001")
    launched("eng-001")

    workdir = instance / "wt" / "eng-001"
    (workdir / "leftover.py").write_text("from a previous dispatch\n")
    git("-C", str(workdir), "add", "leftover.py")
    git("-C", str(workdir), "-c", "user.email=a@b.c", "-c", "user.name=a", "commit", "-qm", "leftover")

    orders("eng-001")
    assert hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance).returncode == 0
    assert not (workdir / "leftover.py").exists(), "a dispatch is a fresh start (spec 17.3)"
    assert (workdir / "src" / "app.py").is_file()


# --- hx push -------------------------------------------------------------------------------------


def test_push_sends_the_branch_to_the_upstream(instance, hx, product_repo, tmp_path):
    assert hx("repo", "add", str(product_repo)).returncode == 0
    import shutil

    shutil.rmtree(instance / "wt" / "eng-001")
    assert hx("launch", "eng-001").returncode == 0

    workdir = instance / "wt" / "eng-001"
    (workdir / "src" / "new.py").write_text("added by the agent\n")
    git("-C", str(workdir), "add", "src/new.py")
    git("-C", str(workdir), "-c", "user.email=a@b.c", "-c", "user.name=a", "commit", "-qm", "work")

    before = git("-C", str(product_repo), "branch", "--list", "agent/eng-001").stdout.strip()
    assert before == "", "the upstream has not seen the branch yet"

    result = hx("push", "eng-001")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HX-PUSH eng-001 agent/eng-001" in result.stdout
    after = git("-C", str(product_repo), "branch", "--list", "agent/eng-001").stdout.strip()
    assert "agent/eng-001" in after, "and now it has, because it was asked to"


def test_nothing_but_push_touches_the_upstream(instance, hx, product_repo, orders, launched):
    """spec 17.2 step 4: the user's remote sees nothing until the Partner is told to push."""
    import shutil

    assert hx("repo", "add", str(product_repo)).returncode == 0
    shutil.rmtree(instance / "wt" / "eng-001")
    before = git("-C", str(product_repo), "for-each-ref", "--format=%(refname) %(objectname)").stdout

    launched("eng-001")
    orders("eng-001")
    assert hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance).returncode == 0
    assert hx("board", "--json").returncode in (0, 1)

    after = git("-C", str(product_repo), "for-each-ref", "--format=%(refname) %(objectname)").stdout
    assert after == before, "launch and dispatch changed the upstream"


def test_push_refuses_a_branch_that_does_not_exist(instance, hx, product_repo, agent):
    assert hx("repo", "add", str(product_repo)).returncode == 0
    agent("eng-009")
    result = hx("push", "eng-009")
    assert result.returncode == 2 and "no such branch" in result.stderr


def test_push_is_the_partners(instance, hx, product_repo):
    assert hx("repo", "add", str(product_repo)).returncode == 0
    result = hx("push", "eng-001", harness_id="eng-001")
    assert result.returncode == 1 and "refuse" in result.stderr


# --- hx upgrade ------------------------------------------------------------------------------------


def test_upgrade_refuses_an_untested_version(instance, hx, tmp_path):
    bindir = tmp_path / "newbin"
    bindir.mkdir()
    binary = bindir / "claude"
    binary.write_text('#!/usr/bin/env bash\necho "9.9.9 (Claude Code)"\n')
    binary.chmod(0o755)
    before = (instance / "config" / "claude.json").read_text()

    result = hx("upgrade", "--claude", str(binary))
    assert result.returncode != 0
    assert "not in this package's tested list" in result.stderr
    assert (instance / "config" / "claude.json").read_text() == before, "the pin survives (spec 17.6)"


def test_upgrade_moves_the_pin_to_a_tested_version(instance, hx, fake_claude_on_path):
    binary = Path(fake_claude_on_path["PATH"].split(":")[0]) / "claude"
    pin_path = instance / "config" / "claude.json"
    pin_path.write_text(json.dumps({"bin": str(binary), "version": "2.1.200"}))

    result = hx("upgrade", "--claude", str(binary))
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(pin_path.read_text())["version"] == TESTED[0]
    assert f"HX-UPGRADE 2.1.200 -> {TESTED[0]}" in result.stdout
    # It does not restart anything: a restart mid-turn would throw a turn away (spec 17.6).
    assert "restart each session at a boundary" in result.stdout


def test_upgrade_on_an_already_current_pin_changes_nothing(instance, hx, fake_claude_on_path):
    binary = Path(fake_claude_on_path["PATH"].split(":")[0]) / "claude"
    result = hx("upgrade", "--claude", str(binary))
    assert result.returncode == 0
    assert result.stdout.strip() == f"HX-UPGRADE unchanged {TESTED[0]}"


def test_doctor_fails_when_the_binary_is_not_the_pinned_version(instance, hx, tmp_path):
    from hx.doctor import FAIL, run_checks

    bindir = tmp_path / "driftbin"
    bindir.mkdir()
    binary = bindir / "claude"
    binary.write_text('#!/usr/bin/env bash\necho "9.9.9 (Claude Code)"\n')
    binary.chmod(0o755)
    pin = instance / "config" / "claude.json"
    pin.write_text(json.dumps({"bin": str(binary), "version": TESTED[0]}))

    failures = [c for c in run_checks(instance) if c[0] == FAIL]
    assert any("9.9.9" in detail and TESTED[0] in detail for _, _, detail in failures), failures


# --- the standing rule ---------------------------------------------------------------------------


def test_no_install_path_reads_the_users_claude_home(tmp_path, fake_claude_on_path, monkeypatch):
    """hx reads nothing from ~/.claude on any platform (spec 11 Auth, 17.3)."""
    import builtins

    real_home_claude = Path.home() / ".claude"
    opened: list[str] = []
    real_open = builtins.open

    def watching_open(file, *args, **kwargs):
        try:
            resolved = str(Path(file).resolve())
        except (TypeError, ValueError, OSError):
            resolved = str(file)
        if resolved.startswith(str(real_home_claude)):
            opened.append(resolved)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", watching_open)

    from hx.install import install_skeleton, preflight

    root = tmp_path / "instance"
    monkeypatch.setenv("PATH", fake_claude_on_path["PATH"])
    preflight(root, None, os.environ)
    install_skeleton(root)
    assert opened == [], f"hx opened the user's Claude home: {opened}"


def test_the_default_branch_name_is_agent_slash_id(instance, hx, product_repo, agent):
    """spec 17.2/17.3: agent branches are `agent/<id>`, and only `hx push` sends one up."""
    from hx.repo import branch_for

    # No `branch` in harness.json, so the default applies.
    agent("qa-004")
    harness = instance / "config" / "qa-004" / "harness.json"
    config = json.loads(harness.read_text())
    del config["branch"]
    harness.write_text(json.dumps(config))
    assert branch_for(instance, "qa-004") == "agent/qa-004"


def test_harness_json_overrides_the_branch_name(instance):
    """spec 05 makes `branch` config, and the skeleton's example worker sets it."""
    from hx.repo import branch_for

    harness = instance / "config" / "eng-001" / "harness.json"
    config = json.loads(harness.read_text())
    config["branch"] = "feature/importer"
    harness.write_text(json.dumps(config))
    assert branch_for(instance, "eng-001") == "feature/importer"


# --- build-5 item 8: the three answers from build-4's open questions ---------------------------


def test_dispatch_refuses_a_dirty_worktree_and_lists_the_files(
    instance, hx, product_repo, orders, launched
):
    """A dispatch resets the worktree, so it will not throw uncommitted work away."""
    import shutil

    assert hx("repo", "add", str(product_repo)).returncode == 0
    shutil.rmtree(instance / "wt" / "eng-001")
    launched("eng-001")

    workdir = instance / "wt" / "eng-001"
    (workdir / "src" / "half-done.py").write_text("work in progress\n")
    orders("eng-001")

    result = hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance)
    assert result.returncode == 1
    assert "refuse" in result.stderr
    assert "half-done.py" in result.stderr, "it lists what would be lost"
    assert "hx bench" in result.stderr, "and says the way out"
    assert not (instance / "tasks.json").exists(), "a refused dispatch writes nothing"
    assert (workdir / "src" / "half-done.py").is_file()


def test_dispatch_is_fine_once_the_work_is_committed(instance, hx, product_repo, orders, launched):
    import shutil

    assert hx("repo", "add", str(product_repo)).returncode == 0
    shutil.rmtree(instance / "wt" / "eng-001")
    launched("eng-001")

    workdir = instance / "wt" / "eng-001"
    (workdir / "src" / "done.py").write_text("finished\n")
    git("-C", str(workdir), "add", "src/done.py")
    git("-C", str(workdir), "-c", "user.email=a@b.c", "-c", "user.name=a", "commit", "-qm", "done")

    orders("eng-001")
    assert hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance).returncode == 0


def test_bench_saves_the_dirty_diff_as_a_patch(instance, hx, product_repo, orders, launched):
    """spec 08: benching frees the id, so what is uncommitted is saved before the reset."""
    import shutil

    assert hx("repo", "add", str(product_repo)).returncode == 0
    shutil.rmtree(instance / "wt" / "eng-001")
    launched("eng-001")
    orders("eng-001")
    assert hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance).returncode == 0
    assert hx("complete", "blocked", harness_id="eng-001").returncode == 0

    workdir = instance / "wt" / "eng-001"
    (workdir / "src" / "app.py").write_text("print('edited but never committed')\n")
    (workdir / "src" / "brand-new.py").write_text("untracked work\n")

    result = hx("bench", "eng-001")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "patch=" in result.stdout

    patches = list((instance / "pods" / "engineers" / "archive").glob("eng-001-*.patch"))
    assert len(patches) == 1
    body = patches[0].read_text()
    assert "src/app.py" in body, "the tracked change is in the patch"
    assert "brand-new.py" in body, "and so is the untracked file"
    assert "edited but never committed" in body


def test_bench_writes_no_patch_when_the_worktree_is_clean(instance, hx, product_repo, orders, launched):
    import shutil

    assert hx("repo", "add", str(product_repo)).returncode == 0
    shutil.rmtree(instance / "wt" / "eng-001")
    launched("eng-001")
    orders("eng-001")
    assert hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance).returncode == 0
    assert hx("complete", "blocked", harness_id="eng-001").returncode == 0

    result = hx("bench", "eng-001")
    assert result.returncode == 0
    assert "patch=" not in result.stdout
    assert list((instance / "pods" / "engineers" / "archive").glob("*.patch")) == []


def test_doctor_fails_when_base_branch_is_not_in_the_mirror(instance, hx, product_repo):
    from hx.doctor import FAIL, run_checks

    assert hx("repo", "add", str(product_repo)).returncode == 0
    config_path = instance / "config" / "repo.json"
    config = json.loads(config_path.read_text())
    config["base_branch"] = "trunk"
    config_path.write_text(json.dumps(config))

    failures = [c for c in run_checks(instance) if c[0] == FAIL]
    assert any("trunk" in detail for _, _, detail in failures), failures
