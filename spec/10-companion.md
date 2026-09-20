## 10. Companion

**Process:** `hx companion <id>` runs in tmux window `<id>:companion`, one per HarnessAgent including the Partner, serving all of that agent's streams. It reads streams and writes step state, digests, and the seam marker. It never writes the agent's files, with one exception: the `## Digest` section of the work item, once, inside `hx complete`.

**System prompt** is composed once at start (05-configuration.md): `companion/BASE.md`, `companion/roles/<role>.md`, and the facts from `config/<id>/harness.json`. The Companion reads no config at runtime.

**Loop:**
1. Wake on: `batch_records` new records in any stream, `run/<id>/turn` touched, subagent stop, or `hx flush`.
2. Per stream with new records, make one stateless call:

```
[companion/BASE.md]                       cache breakpoint (shared by all companions on this model)
[companion/roles/<role>.md]               cache breakpoint
[config/<id>/AGENTS.md or SUBAGENTS.md]   cache breakpoint, cache_ttl
[task: order + addenda]                   cache breakpoint, cache_ttl
[current step state]
[raw records with seq > state.seq]
→ new step state
```

3. Validate and write `state/<id>/<stream>.json`, stamping `prompt_version` with the shas of `BASE.md` and the role file.
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
