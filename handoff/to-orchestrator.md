# Handoff to the orchestrator

## 2026-09-20 — build lane — `tests/guard/test_user_home_untouched.py` fails on a file hx never touched

`tests/guard/test_user_home_untouched.py` fails at the start of goal build-1, before the build
lane wrote anything:

```
+ e03b9b7ccfed665ce24190c52acaf55d3b5860a7a8494f5151c63aef714fee04  plugins/known_marketplaces.json
- fa598acef3e7d9aa3d136f30c709249de79371b949a460ff08cb9429b0a0f81b  plugins/known_marketplaces.json
```

`~/.claude/plugins/known_marketplaces.json` has mtime 13:07:41, the baseline was recorded at
13:06. The only difference in the file is the `lastUpdated` timestamp of the
`claude-plugins-official` marketplace entry (`"lastUpdated": "2026-09-20T20:07:41.893Z"`):
Claude Code's own marketplace refresh, run by one of the three lane sessions starting up. No hx
code existed at that point and no lane ran `/plugin`.

Per ORCHESTRATION.md I am not editing `.baseline/`, `tools/claude-home-hash.sh`, or
`tests/guard/**`. Two options for you:

1. Re-record `.baseline/` now that the marketplace refresh has happened (it is once-per-day-ish,
   so it will recur), or
2. add `plugins/known_marketplaces.json` to the exclusion list in `tools/claude-home-hash.sh`
   with a comment saying why — it is Claude Code's own cache bookkeeping, like
   `plugins/cache/` and `plugins/marketplaces/` which are already excluded, and nothing hx does
   can write it (hx never touches `~/.claude`).

The other guard file, `tests/guard/test_harness_root_refusal.py`, passes as of this goal.

## 2026-09-20 — build lane — questions raised by `goals/build-1.md`

1. **`hx: <cmd>: not implemented (build-N)`** — `N` is a placeholder. I resolved it to the
   *build-lane goal number* that is expected to deliver the command, derived from spec 13
   (goal `build-1` = M0, so `build-<milestone+1>`); e.g. `hx: dispatch: not implemented
   (build-2)`. The mapping lives in one dict, `hx.cli.NOT_IMPLEMENTED`, and is trivial to
   change. Tell me if you meant a literal `build-N`, or a different numbering.

2. **`hx install` step numbering** — build-1 says "step 3 in 17.2 only: the skeleton under a
   given `HARNESS_ROOT`; steps 1, 2, 4–6 come later", but the skeleton is 17.2 **step 2**
   ("Create `HARNESS_ROOT` from the skeleton"); step 3 is the seed login. I built the prose
   ("the skeleton under a given HARNESS_ROOT. Copy `src/hx/skeleton/**` into the root"), i.e.
   17.2 step 2, and left steps 1 and 3–6 unimplemented. `hx install` requires
   `--skeleton-only` until the rest lands.

3. **`crossSessionInbound` scope** — build-1 item 5 lists `crossSessionInbound: accept` among
   the keys `adapters/claude/install.sh` writes, without qualification; spec 05 and 11 and
   17.3 scope it to the Partner ("for the Partner, `crossSessionInbound: accept`"). I followed
   the spec: `run/partner/home/settings.json` gets it, worker homes do not. Say so if every
   home should carry it.

4. **`config/hx.json`** — spec 17.1 says hook commands reference "the absolute path `hx install`
   recorded in `config/hx.json`" but no section defines the file's keys. `install.sh` reads
   `{"hook_bin": "<abs path>"}` and falls back to `$HARNESS_ROOT/bin/hx-hook` (the literal path
   in spec 09) when the file is absent. If you want different keys, say so and I will change
   `install.sh` and the future `hx install`.

5. **`hx doctor` exit code** — spec 08 says doctor exits 1 with the list of failures (seed
   credentials, every home's settings, mirror reachability); build-1 item 1 says it "exits 0".
   For M0 doctor fails (exit 1) only on things M0 owns — missing `tmux`, `git`, Python < 3.12,
   a `config/claude.json` whose `bin` is missing or not executable — and reports everything
   else (missing skeleton files from the gtm lane, missing seed credentials, missing homes) as
   `warn` lines with exit 0. It tightens to the spec 08 rule when those milestones land.
