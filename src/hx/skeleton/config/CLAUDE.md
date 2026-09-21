# You are running under hx

This is the only CLAUDE.md that loads. The repository you are working in has its own and it is
deliberately excluded; do not go looking for it.

**After any boundary, one Read.** A hook prints a line naming your context file — your memory,
your task, your `## Tasks`, the step state your Companion kept, your open subagent handles.
Open it with the **Read tool**, exactly once, before anything else. Not `cat`, not `head`, not
any Bash command: those spend the same tokens and do not count as the one read the harness
measures. Do not read it again later in the same turn. It is always current, and it is the only
file you need — do not search for context.

**Who you are is already in your system prompt.** Your persona arrived at launch and survives
everything. Never read a file to find out who you are.

**Do not re-read what the working set already tells you.** Files listed there carry the fact
you took from them. Read one again only if it has changed since.

**`/goal` points at your work item.** The goal you are given names a file. That file is the
whole task: `## Goal` and every `## Goal addendum` the Partner has appended, the
`## Definition of done` with the `### Checks` that will be run against you, and `## Tasks`,
which is yours. Keep `## Tasks` current — it is what you get back after a seam.

**Your conversation will be cut and rebuilt.** That is a seam: normal, planned, and cheap. It
costs you nothing if `## Tasks` is current and your work is committed, and a great deal if it
is not. Commit each finished sub-task immediately with a descriptive message.

**`hx complete <outcome>` is your last action.** Nothing after it. `done` is machine-checked:
the `### Checks` block runs, the worktree must be clean, and no subagent stream may be open. A
failure prints `HX-CHECK-FAILED <id>` with the output and changes nothing — fix it and run it
again. `blocked`, `decision`, and `exhausted` run no checks and are the honest answers when
`done` is not available.

**What is not yours.** `config/`, `companion/`, `logs/`, `state/`, `run/`, `archive/`,
`pods/` other than your own `-working` item, and `tasks.json` are managed by hx or belong to
another agent. Do not write there. `goals/` belongs to the Partner.

Run `hx task` to print your goal and its addenda at any time.
