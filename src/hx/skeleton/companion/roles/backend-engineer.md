# Role: backend-engineer (Companion retention rules)

You keep step state for a backend engineer. BASE.md fixes the style — telegraphic, exact
identifiers, numbers not adjectives — and this file says which backend facts are worth the
characters.

## The tool calls a backend agent makes after a seam, and what pre-empts them

| It would run | Write instead |
|---|---|
| `Read` a module it already read | `working_set.files`: `<path>`, symbol + `:line` + the fact |
| `grep -rn "<symbol>"` | the `path:line` in the file note or in `next` |
| re-run the suite to see the error | `last_failure`: `<exact command>` → failing test node id → assertion line + `file:line` |
| `alembic history` / `\d <table>` | the migration revision id and the columns touched, in a closed step or a file note |
| `curl` the endpoint to recall its shape | method + path + request/response keys in a file note or decision |
| `env \| grep` / read `settings.py` again | the variable name, its default, and where it is read, in a file note |
| `docker ps`, `lsof -i` | the container/port/process the agent started, in a closed step |
| `git log --oneline` | `working_set.commits`: sha7 + message |

## Keep until the task completes

- Every path the agent read or edited, with the fact and the line: `src/api/deps.py` →
  `"get_cache at :57 → redis.asyncio.Redis, db from settings.redis_db"`. Backend work is
  path-heavy; a forgotten path is a Read after every seam.
- Interface and data facts, exact: table and column names, migration revision ids, endpoint
  method + path, request/response keys, env var names with defaults, ports.
- Test facts: the command as typed (`.venv/bin/python -m pytest tests/api -q`, not "the
  tests"), failing node ids one per line, counts (`3/47 failed`), and which now pass.
- Dead ends: approach → measured cost or exact error → why dropped.
- External state the agent created: containers, databases, fixtures, background processes —
  name and how it is torn down.

## Collapse and discard

Finished refactors → one line + commit sha. Repeated test runs → the last result only, with
the count of attempts. Discard raw output, stack traces once the failing frame is recorded
(keep that frame's `file:line` verbatim), and anything `git log --oneline` or `git diff --stat`
recovers. A `Bash cat` of a working-set file is waste; record it as a dead end so the metric
sees it.
