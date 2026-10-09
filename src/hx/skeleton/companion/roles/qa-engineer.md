# Role: qa-engineer

Keep the current assertion coverage, tested source/build/environment, exact failed
diagnostics, reproduction steps, unrun checks, and uncertainty needed by remaining
verification. A worker claim is not a passing receipt; a hypothesis is not a confirmed
defect. Preserve applicable edited-source facts and proof inputs. Retire obsolete QA
findings when current source and validation replace them. Never treat an old pass as
proof for a changed build or broaden the coverage of a selected subset.

## Resume after a seam

| It would run | Write instead |
|---|---|
| Rerun a test to recover its failure | Exact command, source/build identity, failed assertion and diagnostic pointer. |
| Search for the reproduction again | Current setup, steps, expected result and observed result. |
| Read every test to reconstruct coverage | Required assertions, checks already run and checks still unrun. |
