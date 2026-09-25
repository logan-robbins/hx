## 5. Configuration

**`config/models.json`** — one row per model. `threshold` is hx's seam threshold: when the main stream's `context_tokens` reaches it, the `log` hook marks a seam. `autocompact_window` (optional) is the point at which Claude Code's own compaction fires; when present, `start.sh` exports it as `CLAUDE_CODE_AUTO_COMPACT_WINDOW` on the agent's session and validation requires `threshold < autocompact_window <= window`, so the seam always lands before native compaction. Without it the autocompact stays native and only fires if one turn grows from `threshold` to `window` without ending. 1M-window models are capped at a threshold of 500000.

```json
{
  "claude-opus-5":   { "window": 1000000, "autocompact_window": 250000, "threshold": 200000 },
  "claude-sonnet-5": { "window": 1000000, "autocompact_window": 250000, "threshold": 200000 }
}
```

The shipped defaults accept a 250k working context: the seam fires at 200k, compaction would fire at 250k, and the 1M window is headroom for the single runaway turn. Model id strings are placeholders until implementation; they are validated against the Models API then, not here.

**`config/<id>/harness.json`**:

```json
{
  "id": "eng-001",
  "pod": "engineers",
  "role": "engineer",
  "model": "claude-opus-5",
  "effort": "xhigh",
  "workdir": "/work/eng-001",
  "harness": { "args": ["…"] },
  "companion": {
    "model": "claude-haiku-4-5-20251001",
    "batch_records": 20,
    "cache_ttl": "1h",
    "state_budget_tokens": 10000,
    "seam_min_context_tokens": 60000,
    "seam_min_interval_s": 600,
    "memory_inject_k": 5,
    "memory_episode_chars": 700,
    "memory_half_life_h": 24
  }
}
```

- **Permissions: every HarnessAgent, its subagents, and the Partner run with bypass permissions.** Full trust, no prompts, no approval gates. `adapters/claude/install.sh` writes this into `run/<id>/home/` settings and `start.sh` launches accordingly. This is not configurable per agent.
- `effort` is the HarnessAgent model's effort level, passed at launch.
- `companion.effort` is the Companion's own effort level. Unset means the agent's; set it
  `low` — the Companion extracts step state from records, it does not do the task, so agent
  effort is wasted on it.
- `workdir` is any absolute directory the Partner chooses as this worker's working directory: it creates one, or points the agent at an existing checkout. hx does not manage git for it: it creates nothing, resets nothing, and pushes nothing. It only requires the directory to exist at launch, and `hx complete done` requires `git status --porcelain` to be empty there when the directory is a git repository.
- `companion.state_budget_tokens` bounds the step state and therefore the context file. Target is roughly 10k tokens: the hypothesis under test is that one intelligently constructed file holding the complete useful memory of the task fits in context and yields maximum quality, so this number is a tuning knob, not a ceiling.
- `companion.memory_*` govern episode memory in the context file (`docs/memory.md`): `memory_inject_k` is how many recency-weighted episodes from other agents `hx compose` puts in the **Memory episodes** section (0 removes the section), `memory_episode_chars` how much of each episode the line carries, `memory_half_life_h` the recency half-life. The store itself is instance-global and needs no configuration.
- `companion.seam_*` are the seam policy: the Companion does not declare a seam before `seam_min_context_tokens` are in use, nor more often than `seam_min_interval_s`. Context size comes from the `usage` block of the latest assistant record in the transcript, which `hx-hook` has the path to. The `models.json` threshold is the hard trigger that does not wait for a step to close.
- The Companion learns everything it needs about its HarnessAgent (id, role, pod, budgets, seam policy, persona) from a system prompt hx composes at Companion start from this file, `companion/BASE.md`, and `companion/roles/<role>.md`. It does not read config at runtime.
- `adapters/claude/install.sh` derives the per-agent home settings from this file: hooks with the id baked in, instruction-files mode `claude-md` (the real key is `pluginConfigs["agents-md@builtin"].options.instructionFiles`, honoured in the settings file at the root of `CLAUDE_CONFIG_DIR`; verified 2026-09-20 against `docs/en/memory`), `claudeMdExcludes` for the product repo, the bypass acceptance entry; for the Partner, `crossSessionInbound: accept` (messaging itself is on by default). It then writes the bypass acceptance entry directly; auth is the token in `seed/token`, exported into the agent's environment by `start.sh`, so no credentials are copied from anywhere; both stay in `run/<id>/home/` across dispatches. Nothing about launch is interactive. Effort, model, and the persona file are launch flags (`11-adapters.md`); no compaction env vars are set.
- Validate: `id` equals directory name; `model` exists in `models.json`; `role` has `companion/roles/<role>.md`; `workdir` exists. On failure, exit 2.
- Partner: `"role": "partner"`, no `pod` and no `workdir` — it has no work item and runs in `HARNESS_ROOT`. Validated the same way otherwise.
- The Companion is a Claude Code session in window `<id>:companion` (10-companion.md), launched by `start.sh <id> --companion` with `companion.model` at `companion.effort` (or the agent's effort when unset), its own home `run/<id>/companion-home`, `--dangerously-skip-permissions`, `IS_SANDBOX=1`, and the composed system prompt via `--append-system-prompt-file`. hx has no API client and no headless calls; there is no `provider` field.
