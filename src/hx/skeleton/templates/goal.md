## Goal

Add a `--json` flag to `hx doctor` so the UI can render the same checks the CLI prints.

Context you will need and should not have to hunt for:

- The checks themselves are in `src/hx/doctor.py`, one function per check, each returning a
  `(name, ok, detail)` tuple today. The text renderer is at the bottom of the same file.
- `hx board --json` in `src/hx/board.py` is the shape to follow: a top-level object with a
  `ts` key, a list of results, and an `errors` list. Exit 0 when `errors` is empty, else 1.
- The UI is not being changed in this task. A later item will consume the new output;
  whoever writes that goal reads this one for the shape, so keep the key names accurate.

Keep the existing text output byte-identical. Nothing in this task touches `run/`, `logs/`,
`state/`, or any instance data — `hx doctor` is read-only and stays that way.

Do not rename the existing check functions; `be-002` has just landed changes against them and
a rename now costs a merge for no benefit.

## Definition of done

1. `hx doctor --json` prints one JSON object to stdout and nothing else: `{"ts": ..., "checks":
   [{"name": ..., "ok": ..., "detail": ...}], "errors": [...]}`.
2. Exit status is 0 when `errors` is empty and 1 otherwise, matching the text form.
3. `hx doctor` with no flag prints exactly what it printed before this change.
4. Every check is represented in both forms; no check is JSON-only or text-only.
5. Tests cover a passing instance and an instance with one failing check, for both forms.
6. `docs/` is not touched; the UI is not touched.

### Checks

```bash
.venv/bin/python -m pytest tests/test_doctor.py -q
.venv/bin/python -m hx doctor --json | .venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); assert set(d)=={"ts","checks","errors"}, sorted(d); assert d["checks"], "no checks emitted"'
git diff --quiet HEAD -- docs src/hx/ui
```
