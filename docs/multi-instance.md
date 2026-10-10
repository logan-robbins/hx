# Several instances on one host: a fleet of Partners

One hx instance is one Partner, one `HARNESS_ROOT`, one tmux server. A large programme is split by
ownership (one instance per product area, plus one for shared code), and a human session arbitrates
between them. Everything below was learned running thirteen instances on one 96-vCPU host on
2026-10-09; it is the procedure, not a proposal.

## 1. One instance per area, each on its own tmux server

- `hx install --root /path/fleet/instances/<area> --claude <pinned binary> --skeleton-only` creates
  the layout without launching anything; a second `install` is idempotent and never overwrites.
  Launch later with `hx launch partner` / `hx ui` once the Partner's files are in place.
- Every hx command for an instance runs with `HX_TMUX="tmux -L hx-<area>"` (`src/hx/tmux.py`
  honours it) and `HARNESS_ROOT=<its root>`. A private tmux server per instance keeps session
  names (`partner`, `ui`, `<id>`) from colliding and lets one instance be killed without touching
  another. Wrap it once:

  ```bash
  hxc() { local a=$1; shift; HARNESS_ROOT=/path/fleet/instances/$a HX_TMUX="tmux -L hx-$a" hx "$@"; }
  hxc payments board; hxc payments attach partner
  ```
- `config/ui.json` (or `hx ui --port N`) gets a distinct port per instance: `8800 + index`.
- Start each instance's UI **inside that instance's environment** (`hxc <area> ui`, in a `ui`
  session on its private server): the server reads `HX_TMUX` from its own process to capture
  panes and to reach the Partner, so a UI started with the default tmux shows every pane as
  dead. Thirteen UIs on `127.0.0.1:8800…8812` in one browser work because the token cookie
  carries the port (`hx_ui_token_<port>`); forward them in one line,
  `ssh -L 8800:127.0.0.1:8800 -L 8801:127.0.0.1:8801 … host`, and tell the tabs apart by the
  instance name the page shows, which is the root directory's name.
- A heartbeat cron must loop over the instances with the same two variables, and it launches
  a missing Partner: never install it for an instance that is deliberately stopped.
- The Partner persona of each instance states its area, its prefixes and who owns the rest; the
  shared instance owns cross-cutting code and the others consume it after merge. "Never more than
  one new shared component per wave per worker" keeps the shared instance from becoming the queue.

## 2. Credentials and usage limits

- `seed/token` is per instance. A key starting `sk-ant-api` is exported as `ANTHROPIC_API_KEY`;
  anything else is a `CLAUDE_CODE_OAUTH_TOKEN` seat. Thirteen Partners prewarming on three OAuth
  seats hit usage limits within the hour; an API key did not. Decide the credential per instance
  before the first launch and record it in the fleet table.
- Record every agent binary by its **stable launcher path**, never a versioned file. The Muse
  launcher's auto-update replaced `muse-bin-1.4.3-…` with `1.4.4-…` and deleted the old file within
  the day; every instance pinned to the versioned path failed `hx doctor` at once. `config/meta.json`
  and friends name `~/.local/bin/muse`, `/usr/bin/codex` and a version-less `claude` path; the
  `version` field is a note, not a gate; a fleet-wide `pins-check` (grep for a version-like path
  component, then `-x`) runs with the board. A running process keeps its unlinked binary, so the
  deletion hurts the next launch, not the current one.
- A pane frozen by a usage limit does not answer `hx restart partner` (restart sends the goal and
  waits; it hung seven minutes). Recovery: `HX_TMUX="tmux -L hx-<area>" tmux kill-session -t partner`
  then `hx launch partner`. Use `hx wake partner "<text>"` for cross-instance nudges; it is accepted
  through the Partner's cross-session inbound and never pastes into a pane.

## 3. The go-gate

Install and verify every instance before approval; let none of them work. The Partner persona
carries a go-gate: unless its standing instructions contain a `GO <date>` line written by the
human, on every wake the Partner reads its plan, runs `hx doctor` and `hx board`, writes one
`READY` or `BLOCKED <why>` row to its board, replies with that row and waits. No worker is
launched, no goal dispatched, no lane provisioned before `GO`. A fleet that is installed but
gated costs nothing and starts in one line per instance.

## 4. Goals across many instances

- Keep goal templates in the fleet root and validate each one with
  `hx.goals.parse_goal_text(text, path)` before anyone dispatches it: exactly `## Goal`,
  `## Definition of done`, and a `### Checks` fenced bash block. `hx dispatch` refuses a goal whose
  Checks already pass on the untouched tree (`src/hx/dispatch.py`), so Checks must assert the
  deliverable, not the environment.
- One `hx dispatch` call per wave with every independent goal in it; one stream per dispatch is
  how capacity sits idle. Completion wakes the Partner; nobody polls panes.
- Systematic failures are promoted to one shared `LEARNINGS.md` as category rules
  (`Rule · Signal · Check · Evidence`), never as file-and-line notes; every rule a grep can decide
  becomes a lint the Checks run. Each instance appends application facts to its own map file and
  copies only the rule to the shared file.

## 5. Keeping the product repository clean

`hx plan` and `hx map` write `.hx/map/` into the product repository by design
(`src/hx/appmap.py`). A team whose repository must receive product files only has to deny both
commands in the hook guard and refuse any `.hx/`, `.claude/`, env or evidence path inside a
worktree (a deny-paths entry for the repository root plus a deny-commands regex for
`\bhx\s+(plan|map)\b`). Put a ship check in every goal's Checks: allowed path prefixes only, no
harness or evidence file names, no protected team files, required commit author and trailer.
Worktrees for lanes live outside the repository (`/data/worktrees/<fleet>-<area>-<id>`), and every
lane's environment file lives under the instance root, never in the worktree.

## 6. Evidence that survives a dispute

Across many instances the same result is read by people who did not see it run. Bind every
result to what produced it: source SHA and tree, local-overlay hash, build identity, script hash,
database, role and fixture manifest, captured at run time; report unit, component HTTP, browser
HTTP and browser UI as four separate numerators; reconcile CI, component and live outcomes in one
table before any readiness word. A missing original proof stays missing; nothing is reconstructed.

## 7. Merges across instances

Instances merge into one branch. Serialize the merges with a host-wide lock
(`flock /data/<fleet>-merge-lock/<repo>.lock`) held by the release goal from "merge base in" to
"push", so two instances never race the same integration branch, and let the release goal's
Checks require the exact merge result to have been tested (`git merge-base --is-ancestor
origin/<base> HEAD` plus a local gate line with the head SHA).

## 8. Channels between instances

Four channels, each one line, and nothing else:

- **Partner → human session**: an append-only inbox file in the fleet root, written through a
  helper that takes a per-file `flock`, stamps the instance (from `HARNESS_ROOT`) and the UTC time,
  and refuses anything but one line. Reasons are closed: wave complete, blocker outside the area,
  remote-write authorization needed. The human's board view prints the lines after the last
  `## ACK` line before the boards; the human closes a batch by appending an `## ACK <date>` line.
- **Human → Partner**: `hx wake partner "<text>"` per instance, or a loop over all of them.
- **Partner → Partner**: the other instance's `hx wake partner "<area>: <request>"`, only for a
  dependency that instance owns (a shared provider, a lint rule).
- **Partner → everyone**: the same append helper into the shared learnings file and the area's
  map file. A rule line written as `**Rule T?** · Signal … · Check … · Evidence …` gets the next
  free number under the lock, so two instances never claim the same one and lines never
  interleave. Nobody opens a shared file for writing; nobody pastes into a pane.

Status rows stay in each instance's own `BOARD.md`. A heartbeat cron loops over the instances
with each one's `HARNESS_ROOT` and `HX_TMUX`, and skips every instance whose standing
instructions carry no `GO` line, because `hx heartbeat` launches a missing Partner.
