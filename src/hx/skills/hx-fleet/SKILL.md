---
name: hx-fleet
description: The Partner's manual for the fleet itself — creating a worker from a shipped persona, what hx launch wires up and how to verify it, writing a new role, updating a persona, and retiring a worker. Use when asked for a new agent, a new kind of agent, a persona change, or when an id needs to be freed.
metadata:
  hx-audience: partner
  hx-roles: partner
  hx-loads-when: creating or retiring a worker, choosing a model, refreshing the model registry
---

# hx-fleet

Yours alone. Workers never read this: they do not create agents, and an agent that starts
making agents is a bug.

Everything below is exact about paths because every one of them is load-bearing. `hx launch`
wires an agent up completely or refuses; there is no step you finish by hand afterwards.

## 1. Creating a worker from a shipped persona

Ids are `<pod>-NNN`. Four worker roles ship, each with a persona and a matching Companion role file:

| Role | Persona | Companion rules | Typical ids |
|---|---|---|---|
| `backend-engineer` | `personas/backend-engineer.md` | `companion/roles/backend-engineer.md` | `be-001`, `be-002` |
| `frontend-engineer` | `personas/frontend-engineer.md` | `companion/roles/frontend-engineer.md` | `fe-001` |
| `release-engineer` | `personas/release-engineer.md` | `companion/roles/release-engineer.md` | `rel-001` |
| `qa-engineer` | `personas/qa-engineer.md` | `companion/roles/qa-engineer.md` | `qa-001` |

All paths are relative to `$HARNESS_ROOT`. To create `be-001`:

```bash
mkdir -p "$HARNESS_ROOT/config/be-001"
cp "$HARNESS_ROOT/templates/worker/harness.json" "$HARNESS_ROOT/config/be-001/harness.json"
```

That one file is the whole worker. Then:

1. **Substitute `{{id}}` and `{{pod}}`** — `be-001` and `be`. The pod is
   always the id's prefix, so `hx board` groups agents by it.
2. **Set `role`** in `config/be-001/harness.json` to one of the four above. `hx launch`
   refuses a role with no `companion/roles/<role>.md`, so a typo fails at launch rather than
   at the first Companion pass.
3. **Set `workdir`** to an **absolute path to a directory that already exists** — the checkout
   the human named, or one you create with `mkdir -p`. hx creates no repository, no branch and
   no worktree; the directory is yours to choose and nobody's to clean up. The template ships
   `{{workdir}}` as the value precisely so that an unedited copy fails loudly.

   **Scaling one persona to N sessions.** Five backend engineers are `be-001` … `be-005`, five
   copies of the same recipe with the same `role`, and each one **must have its own workdir**:
   two sessions committing in one checkout corrupt each other's index and dirty each other's
   `hx complete done`. When they work on the same repository, give each a git worktree on its
   own branch (`git -C <repo> worktree add -b feat/<x> <wt>/<id> main`), dispatch all of them
   in one `hx dispatch` call, and integrate afterwards — a release engineer in the main checkout
   merging the branches is the shape that has worked. Pin in each goal what the others are
   touching, so nobody reorganizes a shared file.
   **Flavor.** `flavor` in `harness.json` picks the native harness: `claude` (the default, and
   the Partner's own), `codex`, `pi`, `grok` or `meta`. Each non-Claude flavor authenticates
   from its own `seed/<flavor>-token` file and runs the same dispatch, goal, board and complete
   flow; its Companion is still a Claude session. `templates/contributor/` is the Meta recipe
   (Muse contributor model with a Haiku Companion); a Meta worker with no Companion sets
   `"disabled": true` in its `companion` block, and its completion digest then falls back to
   the agent's own `## Deliverables`.
4. **Write `purpose`** in `harness.json`: the paragraph that says what *this* id is for — the
   domain it owns, the part of the codebase it lives in, the judgement you want from it. That
   paragraph is the whole difference between this agent and the next one of its role. Copy no
   persona: at every boundary the `context` hook hands the worker `personas/<role>.md`, an
   identity line (`You are <id> in pod <pod>. <purpose>`) and the learnings (`hx persona <id>`
   prints it), so an edit to the role persona reaches every worker of the role at its next
   boundary. `hx doctor` warns `purpose:<id>` while the template's `{{purpose}}` is left.
   **Explorer.** `explorer` (`{"model": "claude-sonnet-5-5", "effort": "high"}` in the
   template) is what the worker's orientation subagent runs on: every engineer persona's
   first rule spawns it with the goal paragraph before the first edit, and the identity line
   says `Your explorer runs on <model> at <effort>`. The model must be in
   `config/models.json`, like `model`; `hx metrics <id>` reports
   `edits_before_definition_of_done` for each dispatch.
5. **Copy nothing else.** What this worker's subagents are told is
   `templates/worker/SUBAGENTS.md`, shared by every worker and rendered with its id when the
   `SubagentStart` hook composes each subagent's context file; edit it there and every worker's
   next subagent gets the edit.
6. **Launch:**

   ```bash
   hx launch be-001          # HX-LAUNCH be-001 started goal=none
   ```

## 2. What `hx launch` does, so you know it is wired

One command does all of this. None of it is optional and none of it is yours to do by hand:

- **`adapters/claude/install.sh`** writes `run/<id>/home/settings.json`: every hx hook with
  `--id <id>` baked into its command, the bypass-permissions acceptance, instruction-files mode
  `claude-md` with `claudeMdExcludes`, and — for the Partner alone — `crossSessionInbound`. It
  copies `config/CLAUDE.md` in as the home's CLAUDE.md, copies the skills
  `src/hx/skills/manifest.json` selects for this home — `hx-partner`, `hx-fleet` and
  `hx-memory` for you; `hx-worker` and `hx-memory` for every worker; `hx-signoff` for release
  and QA engineers; `hx-conversion` only where `config/hx.json` names it in `skills` — and
  pre-seeds the home's `.claude.json` so no first-run wizard or trust dialog can stop a launch.
- **`adapters/claude/start.sh`** refreshes the home's `CLAUDE.md` from `config/CLAUDE.md`,
  reads `seed/token` in its own process and exports it as `CLAUDE_CODE_OAUTH_TOKEN`, and execs
  the recorded binary (`config/claude.json`, re-recorded at every launch so the fleet runs the
  current release; `DISABLE_AUTOUPDATER=1` keeps a running session on one version) with
  `--dangerously-skip-permissions`, `IS_SANDBOX=1`, `--model` and `--effort`. No prompt
  argument and no system-prompt flag, ever: the persona, the identity line and the learnings
  arrive through the SessionStart `context` hook at every boundary, bounded at 10,000 bytes.
- **The Companion** is launched in the same session's `companion` window, with its own home and
  its own system prompt composed from `companion/BASE.md`, the role file, and this agent's
  facts.

Hooks and skills are written by `install.sh`, which runs at `hx launch` and not at `hx
restart`: an optional skill you turn on, or a package upgrade that changes a skill, reaches a
worker's home the next time that id is launched, not when it is restarted.

**Verify, do not assume:**

```bash
hx doctor                 # `home:<id>  settings.json` and both `sandbox:<id>` lines, all ok
hx show be-001            # the work item, the home's hook list, the pane
```

If `hx doctor` shows a `sandbox:<id>` failure a moment after launching, wait and look again:
the pane is the launcher until it execs the binary. A failure that persists is real. The
`skills` row says every packaged skill is well-formed and which optional ones are on; a
`skills-isolation` warning names a worker whose workdir carries `.claude/skills`,
`.agents/skills` or `.pi/skills`, or a host `~/.agents/skills` beside a Codex or Pi worker. A
checkout's skills must never load into a worker, so report it to the human rather than
deleting anything.

## 3. Creating a new role

Two files, and both are required — `hx launch` refuses a `role` with no role file, so the pair
is enforced rather than merely recommended.

**`personas/<role>.md`** — the shape of the shipped ones, which are the reference:
identity (who it is and where it runs), how work reaches it, how it works, which files are
its own, and how it finishes. It is shared by every worker of the role and holds nothing about
any one of them — that is each worker's `purpose`. Read `personas/backend-engineer.md`
before writing a new one; matching its shape matters more than matching its length. The
`context` hook prints it whole at every boundary, with the identity line and the rules, under a
10,000-byte bound on Claude: keep it short, and `hx persona <id>` shows what an agent of the
role is handed.

**`companion/roles/<role>.md`** — the retention rules for this kind of agent: what its
Companion keeps until the task completes, what it collapses, what it discards. The shipped
files are short and specific, and yours should be too: what is worth keeping is different for
someone editing schemas than for someone cutting a release, and that difference is the only
reason the file exists.

Then create workers from it exactly as in §1.

## 4. Updating a persona

Each layer has one source, and you edit the source:

- what every worker of a role is — `personas/<role>.md`. It reaches every worker of the role
  at its next boundary (`/clear`, compaction, restart), existing ones included: nothing was
  copied;
- what one worker is for — its `harness.json` `purpose`, same timing;
- what every agent must hold — `config/CLAUDE.md`, at every agent's next launch or restart
  (it is the home's CLAUDE.md on Claude);
- what agents have learned — the learnings, through `hx learn accept` (`--role R` files a rule
  in `personas/<role>/<category>.md`), never by hand.

None of it lands mid-task. A worker's lessons are proposals you review (`hx learn pending`),
scoped to its role unless you widen them with `--global`; there is no per-worker memory file
to edit or to protect.

## 5. Retiring a worker

```bash
hx bench be-001           # complete -> idle; the body is archived, the id is free
tmux kill-session -t be-001
```

`hx bench` only applies to a `complete` item. Kill the session when you are finished with the
agent, not to interrupt it — killing mid-task loses the conversation, though nothing on disk.

**Leave `config/<id>/` in place.** Its `harness.json` is the id: role, purpose, workdir and
the Companion's settings. An idle worker costs nothing, and what it learned is already in the
learnings, not in its directory.

## 6. Never

- **Edit anything in a worker's `workdir`.** That is its work. If it is wrong, resume the
  worker with an addendum.
- **Paste into a worker's pane.** `hx dispatch`, `hx resume` and `hx restart` are the only ways
  a worker hears from you.
- **Write a lesson into a persona or a `purpose`.** A lesson is a learnings proposal: reviewed
  once, carried by every agent of the role, and removable. Prose pasted into a persona is none
  of those.
- **Hand-edit anything under `run/`, `state/`, `logs/` or `pods/`.** hx and the Companion own
  those. If a worker's state looks wrong, that is worth reporting, not patching.

## 7. Model refresh

Model IDs go stale: a provider ships a new one, deprecates an old one, or caps a window
below what `config/models.json` says. `hx models` asks each provider what it serves now and
diffs that against the registry and `docs/models.md`. It writes nothing; you decide.

```bash
hx models                                   # every provider, in order, one block each
hx models --provider anthropic --json       # one provider, machine-readable
hx models --expect current                  # exit 3 if a configured ID is stale
```

Read each block by its status:

- `ok` — the provider answered. `added` are IDs of its family that `docs/models.md` does not
  list; `removed` are listed or configured IDs it no longer returns; `deprecated` are IDs it
  still returns but flags; `window` is its `max_input_tokens` per ID.
- `unverified` — no credential for it (`reason` says which variable or `seed/` file). That is
  the honest answer, not a failure: the Claude OAuth seed cannot call the Anthropic Models
  API, so an instance without `ANTHROPIC_API_KEY` always sees Anthropic `unverified`.
- `error` — the request failed (`http` and `reason`). No retry: run it again later.
- `pinned` — TypeSafe's decision model, fixed in hx itself.

When a configured ID is in `removed` or `deprecated`, or a `stale:` line says a configured
`window` exceeds the provider's `max_input_tokens`, move the agents that use it to a current
ID (`config/<id>/harness.json` `model`, then `hx restart <id>`), and tell the human the
registry needs the same change. Never edit `config/models.json` to make the check pass, and
never paste a key into a goal, a chat message or a file: credentials stay in the env file.
