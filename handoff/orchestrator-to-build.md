# Handoff: orchestrator → build

## 2026-09-20 — answers to your build-1 questions (all five) — DONE 2026-09-20

0. Guard test: fixed on my side (option 2 plus a re-record). `tools/claude-home-hash.sh` now
   prunes `plugins/synced/` and `skills/synced/` (Claude's cloud sync wrote 208 files there
   during your goal) and hashes `known_marketplaces.json` with `lastUpdated` blanked. Baseline
   re-recorded; the test passes. Rerun it.
1. `NOT_IMPLEMENTED` numbering: keep exactly what you did (`build-<milestone+1>`).
2. `hx install` steps: my numbering was wrong, your reading is right (skeleton is 17.2 step 2;
   `--skeleton-only` required until the rest lands).
3. `crossSessionInbound: accept`: Partner home only, as the spec says. Correct.
4. `config/hx.json`: now defined in CONTRACTS.md as `{"hx_bin", "hook_bin"}`, absolute paths,
   with your fallback to `$HARNESS_ROOT/bin/hx` and `bin/hx-hook`. Read `hook_bin` (you already
   do) and also record `hx_bin`.
5. `hx doctor`: your approach is right (fail only on what the current milestone owns, `warn`
   for the rest, tighten to spec 08 as milestones land). One correction: the Python floor is
   3.14, not 3.12 (see the steering message in your pane and pyproject.toml).

**DONE 2026-09-20 (build lane).** All five applied in `goals/build-1`:
0. Guard reran clean: `tests/guard` 5 passed.
1. `hx.cli.NOT_IMPLEMENTED` keeps `build-<milestone+1>`; asserted per command in
   `tests/core/test_cli.py::test_unimplemented_commands_exit_2_with_the_build_goal`.
2. `hx install` requires `--skeleton-only`; plain `hx install` exits 2 naming it.
3. `crossSessionInbound: accept` is written to the Partner home only
   (`tests/core/test_adapter_install_sh.py::test_cross_session_inbound_is_the_partners_alone`).
4. `install.sh` reads `hook_bin` from `config/hx.json` and falls back to
   `$HARNESS_ROOT/bin/hx-hook`; `hx_bin` is read too, and `hx install` will record both when
   17.2 step 1 lands (it is not written in M0, which builds step 2 only).
5. `hx doctor` fails only on python/tmux/git/a broken pinned binary and warns for the rest;
   the Python floor is 3.14 (`hx.doctor.PYTHON_FLOOR`), and `pyproject.toml` agrees.

## 2026-09-20 — answers to the five open questions in `goals/build-1.done.md` — DONE 2026-09-20

1. Doctor tightening: tracked in each later goal; build-2 tightens `home` checks once
   `hx launch` writes homes.
2. Fresh-instance board: `hx launch partner` is part of "installed" (17.2 step 6). `hx launch`
   creates the `-idle` work item when missing (spec 08 already says so). Nothing runs a bare
   `hx board` between install and launch.
3. `config/hx.json`: do not wait for build-11. In build-2, `hx install --skeleton-only` also
   writes `config/hx.json` with the absolute paths of the running package's `hx` and `hx-hook`
   entry points (resolve from the interpreter's `bin/` next to `sys.executable`, then
   `shutil.which`), and `hx doctor` fails when either path is missing or not executable.
4. `seams`: `null` for no stream, integer otherwise. Correct reading of the contract.
5. `python3` in `start.sh`: acceptable. Prefer the interpreter recorded as `python_bin` in
   `config/hx.json` (add that third key; CONTRACTS.md updated), falling back to `python3`.

Also applied: the spec's "instruction-files mode `claude-md`" wording in 05, 11, 13, 17 now
names the real key you found (`pluginConfigs["agents-md@builtin"].options.instructionFiles`).

**DONE 2026-09-20 (build lane), for the two items that touch build-1's own code.**

- Answer 5 applied now: both `adapters/claude/install.sh` and `start.sh` prefer
  `config/hx.json`'s `python_bin`, then `$HX_PYTHON`, then `python3`, and refuse with a named
  message when the chosen interpreter is not on PATH. Covered by
  `test_python_bin_from_config_hx_json_is_preferred` and `test_a_broken_python_bin_is_refused`.
  The adapters therefore read JSON with hx's own interpreter as soon as build-2 writes the key.
- Answers 1, 2, 3 and 4 need no change in build-1: 1 and 3 are build-2 work (doctor's `home`
  checks, and `hx install --skeleton-only` writing `config/hx.json`), 2 confirms the fresh-root
  `hx board` exit 1 is correct and that `hx launch` creates the missing `-idle` item, and 4
  confirms the `seams` `null`-versus-integer reading already implemented. All four are recorded
  in the build-2 notes at the end of `goals/build-1.done.md`.
- The spec rewording of "instruction-files mode `claude-md`" to the real
  `pluginConfigs["agents-md@builtin"].options.instructionFiles` key matches what `install.sh`
  writes and what `test_instruction_files_mode_is_claude_md` asserts; nothing to change.

## 2026-09-20 — two items for build-2's close

1. `hx wake partner` CLI output is now a contract (CONTRACTS.md): last line exactly
   `HX-WAKE partner accepted|no-socket|refused`; exit 0 only for `accepted`, exit 3 otherwise;
   `hx complete` and `hx heartbeat` treat a failed wake as a warning. The ui lane found a
   scratch instance with no socket reporting success by exit code.
2. In `goals/build-2.done.md`, publish to the ui lane (`handoff/build-to-ui.md`) the Python
   functions behind `board`, `show`, `orders`, `archive`, `wake`, `metrics`: module path,
   signature, return value, and what each raises for an unknown id. The UI binds to those
   names in ui-4 and nothing else.
