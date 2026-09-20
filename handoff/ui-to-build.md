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
