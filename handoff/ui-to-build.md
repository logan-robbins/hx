# Handoff from the ui lane to the build lane


## 2026-09-20 — ui lane — `hx ui` should call `hx.ui.server.serve(root)`

`src/hx/cli.py` lists `ui` as not-implemented until build-10. When you wire it, the entry point
is already there and stable:

```python
from hx.ui.server import serve
serve(root)                      # blocking; 127.0.0.1, port from config/ui.json, default 8765
```

- `serve(root: Path | str, *, port: int | None = None, host: str = "127.0.0.1") -> None`.
- It creates `run/ui-token` with mode 0600 if missing and reads `config/ui.json` `{"port": …}`
  itself, so `hx ui` needs to pass nothing but the root. Pass `port=` only to override.
- It never returns until the server is shut down, and it writes nothing under `HARNESS_ROOT`
  except `run/ui-token`.
- `hx.ui` has no dependency on `hx.cli`, so importing it from `cli.py` cannot cycle.

Nothing is needed from you before build-10; this is so the signature does not get guessed.


## 2026-09-20 — ui lane — what `InstanceSource` will call in ui-2, and the one gap

`src/hx/ui/data.py` has `InstanceSource` with its spec 16.1 mtime sweep (`scan()`) complete and
working; its five readers raise `SourceUnavailable` until ui-2 binds them to your functions. So
that ui-2 binds to real names rather than guesses, please confirm or correct these:

| `Source` method | what ui-2 intends to call | status today |
|---|---|---|
| `board()` | `hx.board.collect(root)` | exists; returns the `CONTRACTS.md` document — please keep `collect` returning the JSON shape rather than the text form |
| `show(id)` | `hx.show.collect(root, id)` or equivalent | `src/hx/show.py` does not exist yet (spec 08 has `hx show`; `cli.py` schedules it) |
| `wake_partner(text)` | `hx.wake.wake_partner(root, text) -> bool` | `CONTRACTS.md` names this exactly; `src/hx/wake.py` does not exist yet |
| `orders()` | no command exists | see `handoff/to-orchestrator.md` 2026-09-20 (ui lane): `hx orders --json` is proposed but not in spec 08 |
| `archive()` | no command exists | same entry: `hx archive --json` proposed but not in spec 08 |

The UI only ever reads, so any of these may be a plain function returning a dict; it does not
need a CLI form for the UI's sake. `wake_partner` is the sole exception and is already
specified as a function in `CONTRACTS.md`.

No action needed before your `show`/`wake` goals land — the ui lane is not blocked, it runs on
fixtures.


## 2026-09-20 — ui-2 — the `hx ui` subcommand: what to call, and one stale entry

**The request (ui-2 item 4).** `hx ui` is yours (build-10 in `cli.py`). When you wire it, the
whole subcommand is:

```python
from hx.ui.server import serve

def main(argv, root, *, env=None):
    args = parse(argv)              # optional: --port
    serve(root, args.port)          # blocks until shutdown
    return 0
```

- `serve(root: Path | str, port: int | None = None, *, host: str = "127.0.0.1") -> None`.
  `port` is positional so an override passes straight through; omitted, `serve` reads
  `config/ui.json` `{"port": …}` itself and falls back to 8765.
- `serve` creates `run/ui-token` with mode 0600 if missing and writes nothing else under
  `HARNESS_ROOT`. That is asserted in `tests/ui/test_instance_source.py`, which diffs a
  manifest of a real instance across the whole UI test run and allows only `run/ui-token`.
- `hx.ui` imports nothing from `hx.cli`, so this cannot cycle. It does import `hx.ui.data`,
  which shells out to `hx` — see below.
- It blocks. `hx ui` should not wrap it in a thread.

**One stale entry, yours to fix.** `cli.py`'s `NOT_IMPLEMENTED` maps `"show": 10`, so today
`hx show` prints `hx: show: not implemented (build-10)`. `goals/build-2.md` item 9 delivers
`hx show --json` in build-2, and items 8 and 10 deliver `wake`, `orders` and `archive`. The
numbers for `show`, `orders`, `archive` and `wake` want to be `2`. The UI surfaces that string
verbatim in its 503 body, so a human reading the Agent view today is told to wait for build-10
when the answer is build-2. Nothing breaks either way.

**What `InstanceSource` runs today**, so you can see what it will stop running:

```
hx board --json          # live now
hx show <id> --json      # build-2 item 9
hx orders --json         # build-2 item 10
hx archive --json        # build-2 item 10
hx wake partner <text>   # build-2 item 8 — the text is one argv element
```

Each goes through one function, `hx.ui.data.run_hx(root, args)`, which sets `HARNESS_ROOT` and
unsets `HARNESS_ID` (the UI is not an agent, and spec 08 has Partner commands refuse a foreign
id). It treats exit 1 with a JSON document on stdout as data, not failure, because `hx board`
exits 1 whenever `errors` is non-empty and the board must still render.

When `handoff/build-to-ui.md` lands naming the Python functions, ui-3 replaces `run_hx` with
direct calls. Please include, for each of board / show / orders / archive / wake: the module
path, the exact signature, what it returns, and what it raises when the id is unknown. The UI
needs to tell "no such id" (404) apart from "the instance is broken" (502).


## 2026-09-20 — ui-5 — advisory: `test_upgrade_moves_the_pin_to_a_tested_version` is red

Reporting, not fixing, not waiting — `tools/milestone-check.sh ui --all` says to do exactly
that, and `tests/core/**` is yours. From the advisory pass at the close of ui-5:

```
FAILED tests/core/test_packaging.py::test_upgrade_moves_the_pin_to_a_tested_version
E  AssertionError: assert 'restart each session at a boundary' in 'HX-UPGRADE unchanged 2.1.278\n'
   tests/core/test_packaging.py:338
```

`hx upgrade --claude <fakebin>/claude` printed `HX-UPGRADE unchanged 2.1.278` and exited 0, so
it decided the pin was already current and returned before printing the restart advice the
test expects. Whether the fake binary's version needs to differ from the pinned one for that
path to run, or `upgrade` should print the advice even when unchanged, is yours to say.

Very likely just work in flight — most of what I have reported this way has been. Nothing is
owed to me: `./tools/milestone-check.sh ui` exits 0 and ui-5 is closed.
