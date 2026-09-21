## 4. Ownership

One writer per artifact. Where two writers share a file, the boundary is mechanical and named here.

Personas and per-agent config live in `config/<id>/`, outside every agent's working directory; each agent is told the exact absolute paths of its own files and that it reads nothing else under `HARNESS_ROOT`. Nothing is enforced: this table is a description of who writes what, not a permission system.

| Artifact | Writer | Via |
|---|---|---|
| `config/CLAUDE.md`, `config/models.json`, `companion/**`, `templates/**` | Human | Editor |
| `config/<id>/AGENTS.md` above `## UPDATES BELOW ONLY` | Partner, rarely, only on direct human instruction | Edit tool |
| `config/<id>/AGENTS.md` below `## UPDATES BELOW ONLY` | That HarnessAgent | Edit tool |
| `config/<id>/SUBAGENTS.md`, `config/<id>/harness.json` | Partner, rarely, only on direct human instruction | Edit tool |
| `run/<id>/persona.md` | `hx` | `start.sh`, derived from the part of `AGENTS.md` above the header at every launch |
| `PARTNER.md` | Partner | Edit tool |
| Order and addendum files (any path the Partner chooses) | Partner | Write tool; `hx dispatch` / `hx resume` read them and delete them |
| `tasks.json` | `hx` | `hx dispatch`, `hx complete`, `hx resume` (an ordinary write) |
| Work item create / rename (state suffix) | `hx`; the HarnessAgent may rename its own item directly | `hx launch`, `hx dispatch`, `hx complete`, `hx resume`, `hx bench` |
| Work item `## Order` addendum | `hx` | `hx resume`, appended verbatim from the addendum file |
| Work item body (running task list) | That HarnessAgent | Edit tool |
| Raw streams (`logs/**`), incl. open→closed rename of subagent streams | Hooks | `hx-hook` |
| Step state (`state/**`) | Companion | `hx companion` |
| Digest | Companion | `hx companion` |
| `run/<id>/<stream>.context.md` | `hx` | composed from memory, task, `## Tasks`, step state at each boundary |
| `run/<id>/seam` | Companion, `log` hook | touch-file; writes are idempotent; removed by `hx seam` |
| `run/<id>/home/` | `hx` installs settings and seeds credentials; Claude Code writes its own auto memory and transcripts there | `adapters/claude/install.sh`, Claude Code |
| `run/**` (everything else) | `hx`, hooks | — |

- Work item rename vs. body edit can race if hx renames while the agent's Edit tool is mid-write. Accepted: rare, and hx transitions happen at turn boundaries; the Partner and the agent never touch the item at the same time.
- Partner edits to the top of `AGENTS.md` can race with the agent's edits below the header. Accepted: the Partner only does this on direct human instruction, and the human is aware of the timing.
- `hx resume` appends to `## Order` while the agent is stopped (the item is `complete`), so it never races the agent.
