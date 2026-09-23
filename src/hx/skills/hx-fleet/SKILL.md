---
name: hx-fleet
description: The Partner's manual for the fleet itself — creating a worker from a shipped persona, what hx launch wires up and how to verify it, writing a new role, updating a persona, and retiring a worker. Use when asked for a new agent, a new kind of agent, a persona change, or when an id needs to be freed.
---

# hx-fleet

Yours alone. Workers never read this: they do not create agents, and an agent that starts
making agents is a bug.

Everything below is exact about paths because every one of them is load-bearing. `hx launch`
wires an agent up completely or refuses; there is no step you finish by hand afterwards.

## 1. Creating a worker from a shipped persona

Ids are `<pod>-NNN`. Three roles ship, each with a persona and a matching Companion role file:

| Role | Persona | Companion rules | Typical ids |
|---|---|---|---|
| `backend-engineer` | `personas/backend-engineer/AGENTS.md` | `companion/roles/backend-engineer.md` | `be-001`, `be-002` |
| `frontend-engineer` | `personas/frontend-engineer/AGENTS.md` | `companion/roles/frontend-engineer.md` | `fe-001` |
| `release-engineer` | `personas/release-engineer/AGENTS.md` | `companion/roles/release-engineer.md` | `rel-001` |

All paths are relative to `$HARNESS_ROOT`. To create `be-001`:

```bash
cp -R "$HARNESS_ROOT/templates/worker" "$HARNESS_ROOT/config/be-001"
```

That gives you `config/be-001/{AGENTS.md,SUBAGENTS.md,harness.json}`. Then:

1. **Substitute `{{id}}` and `{{pod}}`** in all three files — `be-001` and `be`. The pod is
   always the id's prefix, so `hx board` groups agents by it.
2. **Set `role`** in `config/be-001/harness.json` to one of the three above. `hx launch`
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
4. **Copy the persona over the template's `AGENTS.md`:**

   ```bash
   cp "$HARNESS_ROOT/personas/backend-engineer/AGENTS.md" "$HARNESS_ROOT/config/be-001/AGENTS.md"
   ```

   Then substitute `{{id}}`/`{{pod}}` in it too, and edit the paragraph that says what *this*
   id is for. That paragraph is the whole difference between this agent and the next one.
   **Leave everything below `## UPDATES BELOW ONLY` empty** — that section is the worker's own
   memory, and it must start empty.
5. **Keep `SUBAGENTS.md`** from the template. It is what this worker's subagents are told, and
   it is delivered by hook rather than as a system prompt, so there is no copy of it anywhere
   else.
6. **Launch:**

   ```bash
   hx launch be-001          # HX-LAUNCH be-001 started goal=none
   ```

## 2. What `hx launch` does, so you know it is wired

One command does all of this. None of it is optional and none of it is yours to do by hand:

- **`adapters/claude/install.sh`** writes `run/<id>/home/settings.json`: every hx hook with
  `--id <id>` baked into its command, the bypass-permissions acceptance, instruction-files mode
  `claude-md` with `claudeMdExcludes`, and — for the Partner alone — `crossSessionInbound`. It
  copies `config/CLAUDE.md` in as the home's CLAUDE.md, installs the `hx-worker` skill (you get
  `hx-partner` and `hx-fleet`; a worker gets neither) and `hx-memory` for both, and pre-seeds the home's `.claude.json`
  so no first-run wizard or trust dialog can stop a launch.
- **`adapters/claude/start.sh`** derives `run/<id>/persona.md` from the part of
  `config/<id>/AGENTS.md` **above** the header, reads `seed/token` in its own process and
  exports it as `CLAUDE_CODE_OAUTH_TOKEN`, and execs the pinned binary with
  `--dangerously-skip-permissions`, `IS_SANDBOX=1`, `--model`, `--effort` and
  `--append-system-prompt-file`. No prompt argument, ever.
- **The Companion** is launched in the same session's `companion` window, with its own home and
  its own system prompt composed from `companion/BASE.md`, the role file, and this agent's
  facts.

**Verify, do not assume:**

```bash
hx doctor                 # `home:<id>  settings.json` and both `sandbox:<id>` lines, all ok
hx show be-001            # the work item, the home's hook list, the pane
```

If `hx doctor` shows a `sandbox:<id>` failure a moment after launching, wait and look again:
the pane is the launcher until it execs the binary. A failure that persists is real.

## 3. Creating a new role

Two files, and both are required — `hx launch` refuses a `role` with no role file, so the pair
is enforced rather than merely recommended.

**`personas/<role>/AGENTS.md`** — the shape of the three shipped ones, which are the reference:
identity (who it is and where it runs), how work reaches it, how it works, which files are
its own, and how it finishes. It must contain **exactly one** `## UPDATES BELOW ONLY` line,
with the persona above it and nothing below. Read `personas/backend-engineer/AGENTS.md` before
writing a new one; matching its shape matters more than matching its length.

**`companion/roles/<role>.md`** — the retention rules for this kind of agent: what its
Companion keeps until the task completes, what it collapses, what it discards. The shipped
files are short and specific, and yours should be too: what is worth keeping is different for
someone editing schemas than for someone cutting a release, and that difference is the only
reason the file exists.

Then create workers from it exactly as in §1.

## 4. Updating a persona

Edit `config/<id>/AGENTS.md` **above** the header whenever efficiency calls for it —
tighter persona lines, corrected facts about that id's scope. `hx compile` distributes the
base (global invariants plus role persona) on the next `hx restart` or launch and preserves
everything else, so an above-header edit takes effect there — not immediately, and not
mid-task. `config/CLAUDE.md` edits land sooner: every agent re-reads them at its next
boundary.

**Never touch anything below the header.** That is the worker's own memory, written by it,
about what it has learned. Editing it is writing false memories into a running agent.

To change what *future* workers of a role get, edit `personas/<role>/AGENTS.md` instead;
existing workers are unaffected, because their persona was copied at creation.

## 5. Retiring a worker

```bash
hx bench be-001           # complete -> idle; the body is archived, the id is free
tmux kill-session -t be-001
```

`hx bench` only applies to a `complete` item. Kill the session when you are finished with the
agent, not to interrupt it — killing mid-task loses the conversation, though nothing on disk.

**Leave `config/<id>/` in place.** It holds that id's memory below the header, which is the
accumulated value of everything it has ever done. Deleting it to "clean up" throws that away
and cannot be undone. An idle worker costs nothing.

## 6. Never

- **Edit anything in a worker's `workdir`.** That is its work. If it is wrong, resume the
  worker with an addendum.
- **Paste into a worker's pane.** `hx dispatch`, `hx resume` and `hx restart` are the only ways
  a worker hears from you.
- **Copy one worker's memory into another.** The section below the header is that agent's own
  record of what it learned; transplanted, it is a set of confident claims about work the new
  agent never did.
- **Hand-edit anything under `run/`, `state/`, `logs/` or `pods/`.** hx and the Companion own
  those. If a worker's state looks wrong, that is worth reporting, not patching.
