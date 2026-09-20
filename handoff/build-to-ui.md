# Handoff: build → ui

## 2026-09-20 — build-1 — `tests/ui` is red mid-flight (15 failed, 5 errors), all on the bearer token

At the end of goal build-1, `tools/milestone-check.sh` fails in `tests/ui` only:

```
tests/guard:      5 passed
tests/core:     236 passed
tests/packaging: 66 passed
tests/ui:        15 failed, 131 passed, 1 skipped, 5 errors
```

Every failure I looked at is the same shape — the test expects 200 and the server answers 401:

```
tests/ui/test_events.py:17: in <module-level helper>
>       assert response.status == 200
E       assert 401 == 200
```

`git status` shows why this is very likely just in-flight work rather than a defect:
`src/hx/ui/server.py` and `src/hx/ui/static/index.html` are modified and `src/hx/ui/pane.py`
is untracked, so the auth path is being changed right now and the tests have not caught up (or
the reverse). Both paths are yours; per ORCHESTRATION.md I am reporting rather than touching
them.

**Nothing of mine is involved.** The build lane wrote no file under `src/hx/ui/**` or
`tests/ui/**` in this goal, and `hx ui` is still `hx: ui: not implemented (build-10)` in
`cli.py`, so nothing in the CLI reaches your server yet.

Two notes that may matter to you, from the build side:

- `hx board --json` is real now and exits **1** on a fresh instance, because spec 08's
  invariant "every `config/<id>/` has a work item" is only true after `hx launch partner`.
  `InstanceSource` should read `errors` and render them rather than treating a non-zero exit as
  a failed call — the JSON on stdout is complete and valid either way.
- `seams` is `null` when `logs/<id>/<id>-main.jsonl` does not exist and an integer (possibly
  `0`) when it does; `context_tokens` is `null` until a record carries one; `session_alive` is
  `false` when tmux cannot be reached. If you would rather always render an integer for
  `seams`, say so here and I will change it — it is one line and better settled before ui-2
  renders it.
