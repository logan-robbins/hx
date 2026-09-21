## 10. Companion

**Process:** the Companion is a Claude Code session, like every other agent in hx: `start.sh <id> --companion` launches it in tmux window `<id>:companion`, one per HarnessAgent including the Partner, with its own home `run/<id>/companion-home` (no hooks except `guard`, no product skills, the `hx-companion` skill), `--dangerously-skip-permissions`, `IS_SANDBOX=1`, `--model companion.model`, and its system prompt via `--append-system-prompt-file run/<id>/companion-system.md` (BASE.md + role + harness facts, composed at launch). There is no headless `claude -p` anywhere in hx; every model call is a tmux session hx operates by pasting. The Companion reads streams and writes step state, digests, and the seam marker with its own tools; hx validates what it wrote. It never writes the agent's files, with one exception: the `## Digest` section of the work item, once, inside `hx complete`.

**System prompt** is composed once at start (05-configuration.md): `companion/BASE.md`, `companion/roles/<role>.md`, and the facts from `config/<id>/harness.json`. The Companion reads no config at runtime.

**Loop** (hx drives it; the Companion's skill tells it what to do with each pass):
1. hx wakes the Companion on: `batch_records` new records in any stream, `run/<id>/turn` touched, subagent stop, or `hx flush`. A wake is `hx wake companion <id> <stream>`: paste `/clear`, then paste the fixed pointer `Companion pass: read <abs run/<id>/companion/<stream>.pass.md> and do what it says.` The pass file, written by hx, names the state file, the log file, the first new `seq`, and the output path. `/clear` makes every pass stateless; the system prompt is the cached prefix.
2. Per pass the Companion reads exactly those files and writes the new step state to the output path with its Write tool. Nothing is passed as prompt text but the pointer. The layers it sees:

```
[companion/BASE.md]                       cache breakpoint (shared by all companions on this model)
[companion/roles/<role>.md]               cache breakpoint
[config/<id>/AGENTS.md or SUBAGENTS.md]   cache breakpoint, cache_ttl
[task: order + addenda]                   cache breakpoint, cache_ttl
[current step state]
[raw records with seq > state.seq]
→ new step state, written by the Companion to run/<id>/companion/<stream>.out.json
```

3. hx (in the Companion's `stop` hook) validates the output against the 07.2 schema and moves it to `state/<id>/<stream>.json`, stamping `prompt_version` with the shas of `BASE.md` and the role file. Invalid or missing output keeps the prior state; hx re-wakes once with the failure named in the pass file, then logs and waits for the next wake.
4. Evaluate the seam policy on the main stream; when it fires, write `run/<id>/seam`. The stop hook does the rest (09-hooks.md 9.3).

**Seam policy:** a step closed on the main stream AND `context_tokens ≥ seam_min_context_tokens` AND time since the last `seam` record `≥ seam_min_interval_s` AND `subagents_open` is empty.

**Across `hx resume`:** the Companion keeps running against the same streams and state; `seq` continues. The addendum appears in the task block of its next call, so the step state absorbs the new instruction without losing the old steps. Only `hx dispatch` archives streams and state.

**Closed-stream digest.** When a subagent stream closes, the Companion's next pass over it writes `state/<id>/<stream>.digest.md`: a few lines of what the subagent did, what it committed, what it left open. The `subagent-result` hook returns it to the parent.

**Final pass** (inside `hx complete`, after the checks have passed for `done`): read the main step state and every closed-stream digest; write the `## Digest` section of the work item for the Partner. For `blocked` and `decision` the digest states the blocker or the question first, so the Partner's addendum can answer it.

**`companion/BASE.md` defines:**
- **Keep until task completes:** goal, constraints, decisions with reason, open steps with intent and next action, working set, blockers.
- **Collapse:** closed steps to one line with outcome, commit sha, and evidence seqs; repeated attempts to one line.
- **Discard:** raw command text and tool output; dead ends that changed no decision; anything one Bash call recovers (`git log --oneline`, `git diff --stat`, `ls`).
- **Keep:** any fact the agent had to Read a file to learn, as a `working_set.files` entry with a one-line note. Discarding it costs a Read after the seam.
- **Evict under budget, in order:** collapsed closed steps, oldest dead ends, working-set detail of closed steps, notes on files not touched by any open step.
- **Evidence:** close a step with `verified: true` only when citing raw record seqs proving it; otherwise `verified: false`.
- **Agent signals lead:** the agent's edits to the `## Tasks` section of its work item (log records on Edit/Write) and todo-tool calls set step status; the Companion fills gaps. Commits set `closed_steps[].commit`.
- **Boundary records** (`seam`, `goal`, `compact`) are evidence of a boundary, not of work. After a `compact` record, treat Claude's summary as unverified.

**`companion/roles/<role>.md` defines** role-specific retention (engineer: paths, failing tests; QA: boundaries, correlation ids, per-case results; partner: dispatch outcomes, digests consumed, cross-pod blockers, open decisions awaiting the human).
