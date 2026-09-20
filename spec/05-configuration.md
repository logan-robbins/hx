## 5. Configuration

**`config/models.json`** — one row per model. `threshold` is hx's seam threshold: when the main stream's `context_tokens` reaches it, the `log` hook marks a seam. It is not passed to Claude Code; the harness autocompact stays native and only fires if one turn grows from `threshold` to `window` without ending. 1M-window models are capped at 500000.

```json
{
  "claude-opus-5":   { "window": 1000000, "threshold": 500000 },
  "claude-sonnet-5": { "window": 1000000, "threshold": 500000 }
}
```

Model id strings are placeholders until implementation; they are validated against the Models API then, not here. 1M-window models are capped at 500000.

**`config/<id>/harness.json`**:

```json
{
  "id": "eng-001",
  "pod": "engineers",
  "role": "engineer",
  "model": "claude-opus-5",
  "effort": "xhigh",
  "workdir": "/work/wt/eng-001",
  "branch": "agent/eng-001",
  "harness": { "args": ["…"] },
  "companion": {
    "provider": "claude-cli",
    "model": "claude-haiku-4-5-20251001",
    "batch_records": 20,
    "cache_ttl": "1h",
    "state_budget_tokens": 10000,
    "seam_min_context_tokens": 60000,
    "seam_min_interval_s": 600
  }
}
```

- **Permissions: every HarnessAgent, its subagents, and the Partner run with bypass permissions.** Full trust, no prompts, no approval gates. `adapters/claude/install.sh` writes this into `run/<id>/home/` settings and `start.sh` launches accordingly. This is not configurable per agent.
- `effort` is the HarnessAgent model's effort level, passed at launch.
- `companion.state_budget_tokens` bounds the step state and therefore the context file. Target is roughly 10k tokens: the hypothesis under test is that one intelligently constructed file holding the complete useful memory of the task fits in context and yields maximum quality, so this number is a tuning knob, not a ceiling.
- `companion.seam_*` are the seam policy: the Companion does not declare a seam before `seam_min_context_tokens` are in use, nor more often than `seam_min_interval_s`. Context size comes from the `usage` block of the latest assistant record in the transcript, which `hx-hook` has the path to. The `models.json` threshold is the hard trigger that does not wait for a step to close.
- The Companion learns everything it needs about its HarnessAgent (id, role, pod, budgets, seam policy, persona) from a system prompt hx composes at Companion start from this file, `companion/BASE.md`, and `companion/roles/<role>.md`. It does not read config at runtime.
- `adapters/claude/install.sh` derives the per-agent home settings from this file: hooks with the id baked in, instruction-files mode `claude-md` (the real key is `pluginConfigs["agents-md@builtin"].options.instructionFiles`, honoured in the settings file at the root of `CLAUDE_CONFIG_DIR`; verified 2026-09-20 against `docs/en/memory`), `claudeMdExcludes` for the product repo, the bypass acceptance entry; for the Partner, `crossSessionInbound: accept` (messaging itself is on by default). It then writes the bypass acceptance entry directly; auth is the token in `seed/token`, exported into the agent's environment by `start.sh`, so no credentials are copied from anywhere; both stay in `run/<id>/home/` across dispatches. Nothing about launch is interactive. Effort, model, and the persona file are launch flags (`11-adapters.md`); no compaction env vars are set.
- Validate: `id` equals directory name; `model` exists in `models.json`; `role` has `companion/roles/<role>.md`; `workdir` exists (Partner: no `workdir`, no `branch`). On failure, exit 2.
- Partner: `"pod": "partner"`, `"role": "partner"`. Validated the same way.
- `companion.provider` is `claude-cli` (default and the only one built first): the Companion's calls go through the pinned `claude -p` binary with the same `seed/token`, a Companion-only home (`run/<id>/companion-home`: no hooks, no skills, no CLAUDE.md), `--model companion.model`, and the composed system prompt via `--append-system-prompt-file`; the state and new records go in on stdin, never as an argument. Prompt caching is the binary's own on the identical prefix; `usage.cache_read_input_tokens` from the JSON result is what `hx metrics` reports. `anthropic` (Messages API with explicit cache breakpoints and an API key) is a later option for instances that have one; it is not required for a subscription-only deployment.
