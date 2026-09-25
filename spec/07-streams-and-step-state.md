## 7. Streams and step state

### 7.1 Raw stream (Companion-only)

The raw stream exists for one reader: the Companion. The HarnessAgent never reads it, and it is never part of what the agent is handed. Its job is to give the Companion enough recent, structured evidence to keep step state current. Everything about it follows from that.

**Raw record** (one JSON line per hook event, appended by `hx-hook`; event vocabulary in `09-hooks.md`):

```json
{"seq":412,"ts":"…","stream":"eng-001-s002","event":"post_tool","tool":"Bash",
 "input":"<head excerpt>","output":"<head excerpt>","exit":1,"agent_id":"…","context_tokens":148220,
 "ref":{"transcript":"<transcript_path>","tool_use_id":"<id>"}}
```

- `seq` is monotonic per stream, assigned by `hx-hook` under a per-stream lock.
- `context_tokens` is read by the hook from the `usage` block of the latest assistant record in the transcript at `transcript_path`.
- `input`/`output` are head excerpts (`excerpt_chars`, default 2000) plus `ref`, a pointer to the full payload in the transcript. Nothing is lost: the Companion follows `ref` when an excerpt is not enough. The excerpt size is a Companion evidence budget, not a cap on what the agent may produce.

**Retention (deterministic FIFO).** Each stream file is append-only and bounded:

- `max_records` (default 500) and `max_bytes` (default 4 MB) per stream, whichever is hit first.
- `hx-hook` truncates from the head on every append that would exceed a bound, but never below `state.seq − keep_behind` (default 100), so the Companion always has a window of already-processed evidence behind its cursor and everything ahead of it.
- Backpressure: if a stream is `max_records − keep_behind` records ahead of `state.seq`, the `log` hook waits before appending until the Companion catches up. The tool call has already run; only the next model request is delayed. Evidence is never dropped ahead of the cursor, and there is no timeout.
- Subagent streams close on `SubagentStop` (rename `open` → `closed`) and are retained until the Companion's final pass over them completes, then deleted.

### 7.2 Step state (Companion-written)

`state/<id>/<stream>.json`:

```json
{
  "seq": 412,
  "prompt_version": {"base": "<sha>", "role": "<sha>"},
  "goal": "",
  "constraints": [],
  "decisions": [{"d": "", "why": "", "ev": [401]}],
  "open_steps": [{"id": "st7", "intent": "", "next": "", "ev": [398, 410]}],
  "closed_steps": [{"id": "st6", "outcome": "", "verified": true, "commit": "<sha>", "ev": [390]}],
  "dead_ends": [],
  "working_set": {
    "commits": [{"sha": "", "msg": ""}],
    "dirty": [],
    "files": [{"path": "", "note": ""}],
    "last_failure": "",
    "hypothesis": ""
  },
  "blockers": [],
  "subagents_open": []
}
```

- `hx` validates schema and size (`chars/4 ≤ state_budget_tokens`); an invalid write keeps the previous state.
- `ev` entries are `seq` references into the raw stream. They are for the Companion's own use across its passes; the agent has no recall command.
- **Structure as memory.** The task template instructs the agent to commit each finished sub-task immediately with a descriptive message rather than leaving the workdir dirty. Then `git log --oneline`, `git diff --stat`, and `ls` recover most of the working state in one Bash call each, and the Companion records the commit sha on the closed step instead of describing the change. `working_set.commits` and `working_set.dirty` are derived from those records. `working_set.files` is only for files the agent read but did not change, with a one-line `note` of the fact it needed from them, so it does not read them again.
- Step state is kept across `hx resume`: an item paused on `blocked` or `decision` continues from the step state it paused with. Only `hx dispatch` archives it.

### 7.3 Context file (hx-composed)

At every boundary (start, resume, clear, compaction, subagent start) hx composes `run/<id>/<stream>.context.md` and the hook hands the agent its path. The persona is not in this file wherever the CLI takes a system prompt: it is in the system prompt (`02-decisions.md` Identity). On flavors whose CLI takes none (meta, codex) the persona rides as section 0 below. Sections in order:

0. Persona: the part of `config/<id>/AGENTS.md` above `## UPDATES BELOW ONLY` — main stream only, and only on flavors without system-prompt injection. The stamped `hx:global` region is stripped: Invariants already carries that source, so keeping it would load the same tokens twice
1. Memory: the part of `config/<id>/AGENTS.md` below `## UPDATES BELOW ONLY` (main stream); `config/<id>/SUBAGENTS.md` whole (subagent streams)
2. Task: the verbatim `## Goal` and every addendum from the work item (the live copy the agent edits; `tasks.json` before the first render). For a subagent stream this section says only that the task is the message it was spawned with, already in its conversation: `SubagentStart` carries no prompt (verified live 2026-09-20) and hx does not guess a pairing from the parent's `PreToolUse(Agent)` payload, which cannot be correlated when two spawns are in flight. The Partner has no work item and no goal: its sections 2 and 3 are `PARTNER.md` and the current `hx board` output. `PARTNER.md` rides bounded — identity, rules, digest, fleet, pods and open questions whole; decisions/completed tails plus omitted counts; notes head plus pointer — because the handover is a resume pointer and an unbounded journal re-fills the window it just freed
3. Work item `## Tasks` section (main stream only)
4. Step state, rendered from `state/<id>/<stream>.json`, one tagged line per fact (`goal:`, `dec:`, `open/next`, `done`, `dead:`, `file`, `fail`, `hypo`, `block`)
5. Memory episodes: the closest episodes other agents of the same role left behind, recency-weighted, queried from this stream's own step state (10-companion.md Episode memory; omitted when `companion.memory_inject_k` is 0)
6. Open subagent handles

Before composing at a planned seam, hx waits for the Companion to process to the head of the stream, so step state is current and no raw tail is needed. On crash or resume the Companion catches up first, then hx composes. The agent reads one file and nothing else.

Every fact in sections 4 and 5 exists to make a tool call unnecessary: the agent that reads the exact `path:line`, the sha, the last failing command and its error line does not grep, log, or re-run for them. Density is the point, prose is not; the Companion writes for a model, not a person.

### 7.4 Seam records

`hx` appends a `seam` record to the stream at every boundary with `prompt_version`, `context_tokens` before, and the context file size. Tool calls in the 10 turns after each seam are the metric for whether the context file worked, split into Reads of files already noted in `working_set` (waste) and everything else; `prompt_version` is what it is compared across. `hx metrics <id>` reads it from the seam records.

### 7.5 Continuity checkpoints

The design question at every critical step: *if this stopped right now, is there an up-to-date, deliberately curated context for this specific process that avoids searching, re-reading, and re-running tools?* The answer at each step, and who guarantees it:

| Step | What exists on disk at that instant | Guaranteed by |
|---|---|---|
| Dispatch | Work item with `## Goal`, `## Definition of done` (checks), empty `## Tasks`; `tasks.json` entry; the goal file consumed and deleted; fresh logs/state; agent home wiped of prior transcripts and auto memory; persona + agent memory in `AGENTS.md` | `hx dispatch` |
| Session start | Persona in the system prompt; context file composed from memory, task, `## Tasks`, step state; the agent's first action is one Read | `start.sh`, `context` hook, `hx compose` |
| Every tool call | One raw record with excerpt + ref appended before the next call; Companion within `batch_records` of head | `log` hook, Companion loop |
| Every `## Tasks` edit | The agent's own plan is current in the work item; the Companion sees the edit as a record | Standing instructions, `log` hook |
| Every commit | Done work is in git with a message; `closed_steps[].commit` set on the Companion's next pass | Standing instructions, Companion |
| Subagent start | Its own context file: `SUBAGENTS.md`, its prompt, empty step state; parent main stream records the spawn | `subagent-start` hook |
| Subagent stop | Stream closed; closed-stream digest written; parent receives it via `subagent-result` | `subagent-stop`, Companion, `subagent-result` |
| Turn end | `run/<id>/turn` with `background_tasks`; Companion woken; seam taken if pending and safe | `stop` hook |
| Seam | Companion at head; context file recomposed; `/clear` then `/goal` pointer; persona still in the system prompt; first action after is one Read | `hx seam`, `context` hook |
| Threshold hit | `log` hook touches `run/<id>/seam`; the next `stop` with no background work takes the seam; native compaction is never reached on the planned path, and if it is, `SessionStart(compact)` still hands over the context file | `log`, `stop`, `context` hooks |
| Crash / restart | Companion catches up to head; context file recomposed; goal restored by Claude Code on `resume`, or sent by `hx goal` after `hx restart` once the pane is ready | `context` hook, `hx restart` |
| Complete | Zero open streams; checks passed and workdir clean (for `done`); Companion final pass; `## Digest` written; agent memory updated; outcome set in the work item and `tasks.json`; `HX-COMPLETE` line printed | `hx complete`, standing instructions |
| Resume | The item's logs, step state, `## Tasks`, memory, and workdir exactly as it paused; the addendum appended to `## Goal`; context file recomposed; goal sent | `hx resume` |
| Partner wake | The worker's renamed work item and the wake message are the signal; the Partner's context file is `PARTNER.md` plus the board | `12-partner-loop.md` |
| Bench | Body archived with timestamp; item reset; logs/state archived at next dispatch | `hx bench`, `hx dispatch` |
