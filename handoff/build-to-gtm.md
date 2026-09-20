# Handoff: build → gtm

## 2026-09-20 — build-1 — `tests/packaging/test_skeleton_texts.py` fails collection after your `packaging/` move

`.venv/bin/python -m pytest` (and therefore `tools/milestone-check.sh`) aborts at collection:

```
tests/packaging/test_skeleton_texts.py:341: in units
    p for p in (PACKAGING / "systemd").iterdir()
E   FileNotFoundError: [Errno 2] No such file or directory: '/Users/loganrobbins/workspace/hx/packaging/systemd'
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```

`git status` shows the cause: you are moving `packaging/**` to `src/hx/packaging/**` (staged
renames, working tree already moved) and `tests/packaging/test_skeleton_texts.py` still
resolves `PACKAGING` to the old top-level `packaging/`. The old directory is now empty, so
`iterdir()` raises at import time and the *whole* suite is interrupted, not just your file.

Both paths are yours, so this is yours to fix — point `PACKAGING` at `src/hx/packaging`. Two
things worth doing while you are there, because a collection-time error takes every lane's
tests down with it:

- resolve the directory inside the test function rather than at module import, or guard the
  `units()` helper, so a missing directory fails one test instead of interrupting collection;
- if `src/hx/packaging/**` is meant to ship in the wheel, it needs a `package-data` entry in
  `pyproject.toml` (build lane's path) — tell me the glob and I will add it.

Not blocking me: `tests/guard` (5 passed) and `tests/core` (235 passed) both pass, and the
full suite passed end to end immediately before your move. Recorded in `goals/build-1.done.md`.
