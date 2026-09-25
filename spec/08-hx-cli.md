## 8. `hx` CLI

Zero-dependency Python (3.14, stdlib only: `json`, `fcntl`, `subprocess`, `tempfile`), one file per command group. Renames use same-directory rename. Agent-side commands identify the caller by `HARNESS_ID` from the tmux session env; Partner commands refuse when `HARNESS_ID` is set and is not `partner`; `hx up` and `hx heartbeat` run with no `HARNESS_ID`, from the human's shell or their own cron. Day to day the human runs nothing: they talk to the Partner. No timeouts anywhere: hx waits for the condition it needs. The one exception is `hx dispatch`'s gate preflight, whose 120 s budget ends in a report, never in a refusal. Models are always passed as full ids (`claude-opus-5`), never aliases, which drift. No task text is ever a command-line argument: goals and addenda are files, and hx deletes each one once it has read it.

| Command | Caller | Effect |
|---|---|---|
| `hx launch <id>` | Partner, `hx up` | Idempotent. Create the `-idle` work item if missing (not for `partner`, which has none); run `install.sh` (writes `run/<id>/home/` settings: hooks with id baked in, bypass permissions, instruction-files mode `claude-md`, `claudeMdExcludes`, the bypass acceptance and the pre-seeded first-launch state); `tmux new-session -d -s <id>` with `HARNESS_ID`, `HARNESS_ROOT`, `CLAUDE_CONFIG_DIR=run/<id>/home`; run `start.sh` in window `main` (derives `run/<id>/persona.md`, launches bare); run `hx companion <id>` in window `companion`. If the item is already `working` (relaunch after a reboot), `hx goal <id>` once the pane is ready |
| `hx install` | Human, once | 17-packaging.md 17.2: checks, instance skeleton, seed token gate, `hx launch partner` |
| `hx doctor` | Partner, human | Check `tmux`, `git`, Python ≥ 3.14, the pinned `claude` binary and version; the paths in `config/hx.json` resolve; `seed/token` present and mode 0600; each `run/<id>/home/settings.json` and its pre-seeded first-launch state file; and, for each running agent, `--dangerously-skip-permissions` in argv and `IS_SANDBOX=1` in env (a warning, not an error, while `start.sh` has not yet exec'd). Exit 1 with the list. It does not inspect work items |
| `hx show <id> [--json]` | Partner, UI | Work item, step state, context file, stream tails, metrics, subagent handles for one id |
| `hx goals [--json]` | Partner, UI | The `tasks.json` record for every id: its goal text, its addenda in order, outcome, dispatched and completed timestamps |
| `hx archive [--json]` | Partner, UI | Benched bodies and archived dispatches per id with their digests |
| `hx ui` | Human, Partner | 16-ui.md server on `127.0.0.1` |
| `hx up` | Human, or their own cron at boot | `hx launch <id>` for every `config/<id>/`, `partner` first |
| `hx dispatch <id> <goal-file> [<id> <goal-file> …]` | Partner | Validate each goal file (`## Goal`, `## Definition of done`, non-empty `### Checks`). Gate preflight, for every id once the rest validates: run its `### Checks` with `bash -e` in the id's workdir, `HARNESS_ID` set to the id, 120 s budget. Exit 0 refuses the whole dispatch with `HX-GATE-EMPTY <id>` and writes nothing, because a block that passes before the work exists gates nothing; a non-zero exit prints `gate preflight: exit <n> [<id>], first FAIL line: …`; an exhausted budget kills the block's process group, prints `gate preflight: not judged after 120s [<id>]` and proceeds. There is no flag to skip it, and an interrupted dispatch being completed is not re-judged. Then per id: write the `tasks.json` entry; archive `logs/<id>/` and `state/<id>/` to `archive/<id>/<ts>/`; clear `run/<id>/` except `home/` and `persona.md` (and wipe `home/projects/`, `home/file-history/`, `home/history.jsonl`); render the body with the goal verbatim; rename `idle → working`; `hx goal <id>`; delete the goal file. It does not inspect or reset git: a dirty workdir is the agent's business, not hx's |
| `hx goal <id> [--now]` | `hx dispatch`, `hx resume`, `hx restart`, `hx launch`, `context` hook | Workers only. Paste the fixed-form `/goal` pointer from `06-work-items.md` into window `main` via tmux buffer; write `run/<id>/goal` marker with ts. It waits for the idle prompt (`capture-pane`) before pasting — dispatch, resume, restart, and launch all call it when the pane is idle. `--now` pastes without checking and is used only from the `context` hook on `clear` (E3), where the pane is by construction about to be ready. The pointer names the work item path and the `HX-COMPLETE` line; it never carries the goal itself. The Partner is never a target: it has no work item and no goal |
| `hx task` | HarnessAgent | Print own full goal and addenda |
| `hx compose <id> <stream>` | Hooks, `hx seam`, `hx resume` | Write `run/<id>/<stream>.context.md` per `07-streams-and-step-state.md` 7.3; print its path |
| `hx seam <id>` | `stop` hook, when `run/<id>/seam` exists | Require empty `background_tasks` in the Stop payload, else return and retry at the next boundary; `hx flush`; `hx compose <id> <id>-main`; paste `/clear` (it queues and runs after the hook returns); append `seam` record; remove `run/<id>/seam`; return. The `context` hook on `source=clear` finishes the seam by sending the goal because the item is `working` |
| `hx restart <id>` | Partner, `hx heartbeat` | Fallback seam: `hx flush`; `hx compose`; kill window `main`; `start.sh <id>` bare; for a worker, `hx goal <id>` once the pane is ready. Restarting `partner` sends no goal; its state is `PARTNER.md` and the human talks to it |
| `hx complete <outcome>` | HarnessAgent | Require zero `-open` subagent streams. For `done`: run the `### Checks` block with `bash -e` in the workdir and, when the workdir is a git repository, require `git status --porcelain` to be empty; on any failure print `HX-CHECK-FAILED <id>` and the failing output, exit 1, change nothing. Then: `hx flush`; companion writes Digest; write outcome to the work item and `tasks.json`; rename `working → complete`; remove `run/<id>/goal`; print `HX-COMPLETE <id> <outcome>` as the last line of stdout (the goal evaluator's proof: tool output is in the transcript it reads); `hx wake partner "<id> complete: <outcome>; hx read <id>"` |
| `hx resume <id> <addendum-file>` | Partner | Require `complete` with outcome `blocked` or `decision`. Append `## Goal addendum <ts>` + the file verbatim beneath `## Goal`; record the addendum in `tasks.json` and clear the outcome; keep logs, state, `## Tasks`, memory, workdir; rename `complete → working`; `hx compose`; `hx goal <id>`; delete the addendum file. An addendum carrying its own `### Checks` block replaces the goal's, as `hx amend` does, and it prints `HX-AMEND` before `HX-RESUME` |
| `hx amend <id> <addendum-file>` | Partner | Require `working` or `complete`. Append `### Goal addendum <ts>` + the file beneath `## Goal` and record it in `tasks.json[<id>].addenda`, exactly as `hx resume`. When the addendum carries a `### Checks` heading with a fenced bash block, that fence replaces the Checks fence in `tasks.json[<id>].goal` and in the work item's `## Definition of done`; the amended goal must parse and both copies must carry the new block, or it refuses and writes nothing. No state change, no paste, no restart: the worker's next `hx task` and `hx complete done` read the amended record. Delete the addendum file; print `HX-AMEND <id> checks=replaced\|kept addenda=<n>` |
| `hx distill <id> <distilled-file> [--memory]` | Partner | Shrink accumulation back down: replace every `### Goal addendum` in the Work Item's `## Goal` and drop the `tasks.json[<id>].addenda` records, after writing the file's directives into a fenced `## Distilled directives` section above the header of `config/<id>/AGENTS.md` (created or replaced whole; `hx compile` keeps it). The goal and its current `### Checks` are never touched. With `--memory`, the file's `## Memory` section also replaces below-header memory whole. The distilled file must exist and be non-empty, and with `--memory` must carry `## Memory`; a refusal writes nothing and consumes nothing. Delete the distilled file; print `HX-DISTILL <id> directives=created\|replaced goal_addenda=<n> recorded=<n> memory=kept\|replaced bytes=<a>-><b>` |
| `hx read <id>` | Partner | Print Digest of a `complete` work item; `--full` prints the whole body |
| `hx bench <id>` | Partner | Archive the completed body to `pods/<pod>/archive/<id>-<ts>.md`; reset the body from `templates/work-item.md`; rename `complete → idle`. It touches neither git nor the workdir. Does not touch `tasks.json`: the board shows the benched id as `idle` with its last outcome until the next dispatch, by design (the outcome is history, the state is the board) |
| `hx board [--json]` | Partner, UI | A plain listing of what is on disk, one line per id: id, pod, state, outcome, dispatched, session alive, open subagents, `context_tokens` of the last record, seams this dispatch. It judges nothing and exits 0 |
| `hx flush <id>` | `hx complete`, `hx seam` | For every stream with records past `state.seq`: `hx companion <id> --wake <stream>` and wait (no timeout) until `state.seq` is at the log head |
| `hx companion <id>` | `hx launch` | Launch the Companion session in window `<id>:companion` via `start.sh <id> --companion` (idempotent); write `run/<id>/companion-system.md` first |
| `hx companion <id> --wake <stream>` | `stop` hook, `hx flush`, `subagent-stop` | Write `run/<id>/companion/<stream>.pass.md`; paste `/clear` then the fixed pointer into `<id>:companion` when its pane is idle; else queue the pass and paste it from the Companion's own `stop` hook |
| `hx wake partner "<text>"` | `hx complete`, `hx heartbeat` | Connect to the unix socket in `run/partner/socket.json`; write `{"type":"auth","token":"<token>"}` then `{"type":"user","message":{"role":"user","content":"<text>"}}`, newline-terminated; the socket answers nothing. An idle Partner starts a turn; a busy one takes it as steering in the current turn. The text is a fixed short form composed by hx, never a goal |
| `hx heartbeat` | Human's own cron, if they want it | `hx board`; `hx restart <id>` for every `working` item whose session is dead; `hx launch partner` if the Partner's session is dead; then, if any item is `working` and the board output differs from the last heartbeat's, `hx wake partner "check on each HarnessAgent: <board diff>"` |
| `hx memory search <query> [--role R] [--pod P] [--id ID] [--kind K] [--k N] [--all-roles] [--half-life-h H] [--json]` | HarnessAgent, Partner, human | Drain `state/memory/queue/` into the ChromaDB store, then a recency-weighted semantic search over every agent's episodes (`score = similarity × (0.5 + 0.5·2^(−age_h/half_life_h))`). The default filter is the caller's own role (`HX_ROLE` on the session); `--all-roles` widens. The `hx-memory` skill tells agents to read the context file's Memory episodes section first, search their own role next, and widen only when that is empty or off-topic |
| `hx memory index` · `hx memory list [--id] [--role] [--kind] [--limit] [--json]` · `hx memory stats [--json]` | Human, cron, Partner | Drain the queue; list episode metadata newest first; counts by role and kind plus the queue length. Every store access, reads included, holds `state/memory/index.lock` |
| `hx metrics <id>` | Partner | Per seam: tool calls in the next 10 turns (Reads of `working_set` files vs. other), context-file Reads, `prompt_version`, context tokens before |

**`tasks.json`** (control-plane record per id, written only by hx, with an ordinary write):

```json
{
  "eng-002": {
    "goal": "<the goal file verbatim>",
    "addenda": [{"ts": "…", "text": "…"}],
    "outcome": null,
    "dispatched": "20260920T101500Z",
    "completed": null
  }
}
```

Together with the work item this is the only place task text lives. `hx bench` does not touch `tasks.json`.

**`hx dispatch`** (the shape; Python in the implementation):

```
for each (id, goal_file):
    refuse unless item is idle and tmux session <id> exists
    refuse unless HARNESS_ID is partner
    require ## Goal, ## Definition of done, non-empty ### Checks
for each id:
    run ### Checks with bash -e in its workdir, 120 s
    exit 0 → print HX-GATE-EMPTY <id>; stop, nothing written
    non-zero → gate preflight: exit <n> [<id>], first FAIL line …
    budget spent → kill the process group; gate preflight: not judged after 120s [<id>]
for each id:
    tasks[id] = {goal, addenda: [], outcome: null, dispatched: ts, completed: null}
write tasks.json
for each id:
    move logs/<id>, state/<id> to archive/<id>/<ts>/; recreate
    remove run/<id>/* except home/ and persona.md
    remove home/projects, home/file-history, home/history.jsonl
    write run/<id>/subagents.json = {}
    render templates/work-item.md with the goal file verbatim → pods/<pod>/<id>-idle.md
    rename to -working; hx goal <id>
    delete the goal file
```

- Write sequence: tasks, then per-id archive, reset, render, rename, goal, delete the goal file. Re-running the same `hx dispatch` completes an interrupted one, except that a goal file already consumed is gone — the goal text is in `tasks.json` and hx re-renders from there.
- `home/` wipe is exactly `projects/` (all session and subagent transcripts, spilled tool results, and per-project auto memory with its `MEMORY.md`), `file-history/` (pre-edit snapshots), and `history.jsonl` (typed prompts). `settings.json`, `.credentials.json`, `agents/`, `skills/`, `plugins/`, and `agent-memory/` are siblings and survive. Auto memory is keyed by git repo, so without a per-agent home two agents pointed at the same checkout would share one memory; the per-agent home is what isolates it.
