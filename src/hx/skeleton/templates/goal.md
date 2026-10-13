## Goal

Add a `--json` flag to `hx doctor` so the UI can render the same checks the CLI prints. The worker decomposes this further: what the flag prints, how both forms stay covered, and what must not change.

## Definition of done

1. `hx doctor --json` prints one JSON object to stdout and nothing else: `{"ts": ..., "checks":
   [{"name": ..., "ok": ..., "detail": ...}], "errors": [...]}`.
2. Exit status is 0 when `errors` is empty and 1 otherwise, matching the text form.
3. `hx doctor` with no flag prints exactly what it printed before this change.
4. Every check is represented in both forms; no check is JSON-only or text-only.
5. Tests cover a passing instance and an instance with one failing check, for both forms.
6. `docs/` is not touched; the UI is not touched.

### Checks

The Partner binds REPO_ID to this product's indexed namespace before dispatch.

```bash
hx code type DoctorReport --namespace REPO_ID --expect absent
hx code tests-for src/hx/doctor.py --namespace REPO_ID --expect exists
hx code fresh src/hx/doctor.py --namespace REPO_ID --expect fresh
.venv/bin/python -m pytest tests/core/test_doctor.py -q
.venv/bin/python -m hx doctor --json | .venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); assert set(d)=={"ts","checks","errors"}, sorted(d); assert d["checks"], "no checks emitted"'
git diff --quiet HEAD -- docs src/hx/ui
```

Bind the concrete interface name, test path, write scope and repository namespace
for the assignment. Refresh changed source before completion checks. The three
graph lines mechanically check no second interface, an existing test boundary,
and fresh scope; they are executable checks in the same `### Checks` block.
