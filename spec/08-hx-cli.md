## 8. `hx` CLI

Zero-dependency Python (3.14, stdlib only: `json`, `fcntl`, `subprocess`, `tempfile`), one file per command group. Renames use same-directory rename. Agent-side commands identify the caller by `HARNESS_ID` from the tmux session env; Partner commands refuse when `HARNESS_ID` is set and is not `partner`; system commands (`hx up`, `hx heartbeat`) run from systemd and cron with no `HARNESS_ID`. The human runs nothing after system setup. No timeouts anywhere: hx waits for the condition it needs. Models are always passed as full ids (`claude-opus-5`), never aliases, which drift. No task text is ever a command-line argument: orders and addenda are files.

| Command | Caller | Effect |
|---|---|---|
| `hx launch <id>` | Partner, `hx up` | Idempotent. Create worktree (not for `partner`) and `-idle` work item if missing; run `install.sh` (writes `run/<id>/home/` settings: hooks with id baked in, bypass permissions, instruction-files mode `claude-md`, `claudeMdExcludes`; seeds credentials and the bypass acceptance from the harness user's `~/.claude`); `tmux new-session -d -s <id>` with `HARNESS_ID`, `HARNESS_ROOT`, `CLAUDE_CONFIG_DIR=run/<id>/home`; run `start.sh` in window `main` (derives `run/<id>/persona.md`, launches bare); run `hx companion <id>` in window `companion`. If the item is already `working` (relaunch after a reboot), `hx goal <id>` once the pane is ready |
| `hx install` | Human, once | 17-packaging.md 17.2: checks, instance skeleton, seed login, repo mirror, boot and heartbeat units, `hx launch partner` |
| `hx doctor` | Partner, `hx up` | Check tmux, git, the pinned `claude` binary and version, `seed/token` present and mode 0600, every home's settings, mirror reachability; exit 1 with the list |
| `hx repo add <url\|path>` | `hx install`, Partner | Bare mirror at `repos/<name>.git`; `config/repo.json` |
| `hx push <id>` | Partner, on instruction | `git push upstream agent/<id>` from the mirror; the only command that touches the user's remote |
| `hx show <id> [--json]` | Partner, UI | Work item, step state, context file, stream tails, metrics, subagent handles for one id |
| `hx orders [--json]` | Partner, UI | Every `orders/*.md` and addendum with the `tasks.json` record it produced, readiness (`waiting_on`), whether the file still matches the dispatched order, and the `after` graph |
| `hx archive [--json]` | Partner, UI | Benched bodies and archived dispatches per id with their digests |
| `hx ui` | systemd/launchd, Partner | 16-ui.md server on `127.0.0.1` |
| `hx upgrade` | Human, rarely | 17-packaging.md 17.6 |
| `hx up` | systemd/launchd at boot | `hx launch <id>` for every `config/<id>/`, `partner` first |
| `hx dispatch <id> <order-file> [<id> <order-file> …]` | Partner (itself included) | Validate each order file (`## Order`, `## Definition of done`, non-empty `### Checks`; optional frontmatter `after`). Under flock: write `tasks.json` entries; per id archive `logs/<id>/` and `state/<id>/` to `archive/<id>/<ts>/`, clear `run/<id>/` except `home/` and `persona.md` (auto memory and transcripts under `home/` are cleared); render the body with the order verbatim; rename `idle → working` and `hx goal <id>` when every `after` entry is `done`, else `idle → queued`. For `partner`: no archive, no wipe, no reset (its session is continuous); render, record, rename, `hx goal partner`, which lands in `goal-pending` because the Partner's own pane is mid-turn. Refuses (exit 1, listing the files) when `wt/<id>` is dirty: uncommitted work is never discarded silently; `hx bench` is the way to archive it |
| `hx goal <id> [--now]` | `hx dispatch`, `hx resume`, `hx restart`, `hx launch`, `context` hook | Paste the fixed-form `/goal` pointer from `06-work-items.md` into window `main` via tmux buffer; write `run/<id>/goal` marker with ts. If the pane is at the idle prompt (`capture-pane`), paste now. If the pane is mid-turn (the Partner dispatching or resuming itself from its own Bash tool), write `run/<id>/goal-pending` and return; the `stop` hook pastes the pointer at the end of that turn, the live-verified path for a slash command queued from a hook. `--now` pastes without checking and is used only from the `context` hook on `clear` (E3) and the `stop` hook. The pointer names the work item path and the `HX-COMPLETE` line; it never carries the order itself. In both cases `run/<id>/goal` is written with the timestamp of the call, so the `working` invariant holds during the turn in which a busy pane is owed its goal; the stop hook rewrites it when it pastes |
| `hx task` | HarnessAgent | Print own full order and addenda |
| `hx compose <id> <stream>` | Hooks, `hx seam`, `hx resume` | Write `run/<id>/<stream>.context.md` per `07-streams-and-step-state.md` 7.3; print its path |
| `hx seam <id>` | `stop` hook, when `run/<id>/seam` exists | Require empty `background_tasks` in the Stop payload, else return and retry at the next boundary; `hx flush`; `hx compose <id> <id>-main`; paste `/clear` (it queues and runs after the hook returns); append `seam` record; remove `run/<id>/seam`; return. The `context` hook on `source=clear` finishes the seam by sending the goal because the item is `working` |
| `hx restart <id>` | Partner, `hx heartbeat` | Fallback seam: `hx flush`; `hx compose`; kill window `main`; `start.sh <id>` bare; `hx goal <id>` once the pane is ready |
| `hx complete <outcome>` | HarnessAgent | Require zero `-open` subagent streams. For `done`: require `git status --porcelain` empty in the worktree (not for `partner`) and run the `### Checks` block with `bash -e` in the worktree (`HARNESS_ROOT` for `partner`); on any failure print `HX-CHECK-FAILED <id>` and the failing output, exit 1, change nothing. Then: `hx flush`; companion writes Digest; write outcome to the work item and `tasks.json`; rename `working → complete`; remove `run/<id>/goal`; for `done`, promote every `queued` item whose `after` entries are all `done` (`queued → working`, `hx goal`); print `HX-COMPLETE <id> <outcome>` as the last line of stdout (the goal evaluator's proof: tool output is in the transcript it reads); unless id is `partner`, `hx wake partner "<id> complete: <outcome>; hx read <id>"` |
| `hx resume <id> <addendum-file>` | Partner (itself included; for `partner` the goal lands in `goal-pending`) | Require `complete` with outcome `blocked` or `decision`. Under flock: append `## Order addendum <ts>` + the file verbatim beneath `## Order`; record the addendum in `tasks.json` and clear the outcome; keep logs, state, `## Tasks`, memory, worktree; rename `complete → working`; `hx compose`; `hx goal <id>` |
| `hx read <id>` | Partner | Print Digest of a `complete` work item; `--full` prints the whole body |
| `hx bench <id>` | Partner | Archive the completed body to `pods/<pod>/archive/<id>-<ts>.md`; if `wt/<id>` is dirty, save `git diff` (tracked and untracked) to `pods/<pod>/archive/<id>-<ts>.patch` and then reset the worktree to `base_branch`; reset the body from template; rename `complete → idle`. Does not touch `tasks.json`: the board shows the benched id as `idle` with its last outcome until the next dispatch, by design (the outcome is history, the state is the board) |
| `hx board [--json] [--require-done <id>…]` | Partner, checks, UI | One line per id: `<work-item-file>  <after>  <outcome>  <open subagents>  <goal ts>`, then invariant errors; exit 1 on any error. With `--require-done`, exit 0 iff every listed item is `complete` with outcome `done` (the Partner's own `### Checks`) |
| `hx flush <id>` | `hx complete`, `hx seam` | For every stream with records past `state.seq`: `hx wake companion <id> <stream>` and wait (no timeout) until `state.seq` is at the log head |
| `hx companion <id>` | `hx launch` | Launch the Companion session in window `<id>:companion` via `start.sh <id> --companion` (idempotent); write `run/<id>/companion-system.md` first |
| `hx wake companion <id> <stream>` | `stop` hook, `hx flush`, `subagent-stop` | Write `run/<id>/companion/<stream>.pass.md`; paste `/clear` then the fixed pointer into `<id>:companion` when its pane is idle; else queue the pass and paste it from the Companion's own `stop` hook |
| `hx wake partner "<text>"` | `hx complete`, `hx heartbeat` | Connect to the unix socket in `run/partner/socket.json`; write `{"type":"auth","token":"<token>"}` then `{"type":"user","message":{"role":"user","content":"<text>"}}`, newline-terminated; the socket answers nothing. An idle Partner starts a turn; a busy one takes it as steering in the current turn. The text is a fixed short form composed by hx, never an order |
| `hx heartbeat` | System cron, every 15 min | `hx board`; `hx restart <id>` for every `working` item whose session is dead, `partner` included; then, if any item is `working` or `queued` and the board output differs from the last heartbeat's, `hx wake partner "check on each HarnessAgent: <board diff>"` |
| `hx metrics <id>` | Partner | Per seam: tool calls in the next 10 turns (Reads of `working_set` files vs. other), context-file Reads, `prompt_version`, context tokens before |

**`tasks.json`** (control-plane record per id, written only by hx under `run/tasks.lock`):

```json
{
  "eng-002": {
    "order": "<orders/eng-002.md verbatim>",
    "after": ["eng-001"],
    "addenda": [{"ts": "…", "text": "…"}],
    "outcome": null,
    "dispatched": "20260920T101500Z",
    "completed": null
  }
}
```

Readiness of an `after` entry is `tasks.json[<dep>].outcome == "done"`. A re-dispatch of the dependency resets its outcome to `null`, so a stale completion never satisfies a newer dependent. `hx bench` does not touch `tasks.json`.

**`hx dispatch`** (the shape; Python in the implementation):

```
lock run/tasks.lock
for each (id, order_file):
    refuse unless item is idle and tmux session <id> exists
    refuse unless HARNESS_ID is partner
    parse frontmatter after: []; require ## Order, ## Definition of done, non-empty ### Checks
    tasks[id] = {order, after, addenda: [], outcome: null, dispatched: ts, completed: null}
write tasks.json (tmp + rename)
for each id:
    if id != partner:
        move logs/<id>, state/<id> to archive/<id>/<ts>/; recreate
        remove run/<id>/* except home/ and persona.md
        remove home/projects, home/file-history, home/history.jsonl
        write run/<id>/subagents.json = {}
    render templates/work-item.md with the order file verbatim → pods/<pod>/<id>-idle.md
    if every after entry has outcome done: rename to -working; hx goal <id>   # partner: goal-pending, sent by its stop hook
    else: rename to -queued
```

- Write order: tasks, then per-id archive, reset, render, rename, goal. Re-running the same `hx dispatch` completes an interrupted one.
- `home/` wipe is exactly `projects/` (all session and subagent transcripts, spilled tool results, and per-project auto memory with its `MEMORY.md`), `file-history/` (pre-edit snapshots), and `history.jsonl` (typed prompts). `settings.json`, `.credentials.json`, `agents/`, `skills/`, `plugins/`, and `agent-memory/` are siblings and survive. Auto memory is keyed by git repo, so without a per-agent home every worktree of the product repo would share one memory; the per-agent home is what isolates it.

**`hx board` invariants:** one work item per id, `partner` included; names match the regex; every work item has `config/<id>/`; every `config/<id>/` has a work item; every `tasks.json` key has `config/<id>/`; every `working` item has a live session and a `run/<id>/goal` marker; every `queued` item has an unmet `after` entry and no goal marker; every `complete` item has zero `-open` streams; every `run/<id>/home/` has its settings file and credentials.
