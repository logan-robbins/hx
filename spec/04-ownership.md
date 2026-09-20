## 4. Ownership

One writer per artifact. Where two writers share a file, the boundary is mechanical and named here.

| Artifact | Writer | Via |
|---|---|---|
| `config/CLAUDE.md`, `config/models.json`, `companion/**`, `templates/**` | Human | Editor |
| `config/<id>/AGENTS.md` above `## UPDATES BELOW ONLY` | Partner, rarely, only on direct human instruction | Edit tool |
| `config/<id>/AGENTS.md` below `## UPDATES BELOW ONLY` | That HarnessAgent | Edit tool |
| `config/<id>/SUBAGENTS.md`, `config/<id>/harness.json` | Partner, rarely, only on direct human instruction | Edit tool |
| `run/<id>/persona.md` | `hx` | `start.sh`, derived from the part of `AGENTS.md` above the header at every launch |
| `PARTNER.md` | Partner | Edit tool |
| `orders/<id>.md`, `orders/<id>.addendum.md` | Partner, its own `orders/partner.md` and addendum included | Write tool |
| `tasks.json` | `hx` | `hx dispatch`, `hx complete`, `hx resume` (under `run/tasks.lock`) |
| Work item create / rename (state suffix) | `hx` | `hx launch`, `hx dispatch`, `hx complete` (including promotion of `queued` items), `hx resume`, `hx bench` |
| Work item `## Order` addendum | `hx` | `hx resume`, appended verbatim from the addendum file |
| Work item body (running task list) | That HarnessAgent (the Partner for `partner`) | Edit tool |
| Raw streams (`logs/**`), incl. open→closed rename of subagent streams | Hooks | `hx-hook` |
| Step state (`state/**`) | Companion | `hx companion` |
| Digest | Companion | `hx companion` |
| `run/<id>/<stream>.context.md` | `hx` | composed from memory, task, `## Tasks`, step state at each boundary |
| `run/<id>/seam` | Companion, `log` hook | touch-file; writes are idempotent; removed by `hx seam` |
| `run/<id>/home/` | `hx` installs settings and seeds credentials; Claude Code writes its own auto memory and transcripts there | `adapters/claude/install.sh`, Claude Code |
| `run/**` (everything else) | `hx`, hooks | — |

- Work item rename vs. body edit can race if hx renames while the agent's Edit tool is mid-write. Accepted: rare, and hx transitions happen at turn boundaries.
- Partner edits to the top of `AGENTS.md` can race with the agent's edits below the header. Accepted: the Partner only does this on direct human instruction, and the human is aware of the timing.
- `hx resume` appends to `## Order` while the agent is stopped (the item is `complete`), so it never races the agent.
