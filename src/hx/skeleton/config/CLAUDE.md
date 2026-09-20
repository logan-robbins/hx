# You are running under hx

This is the only CLAUDE.md that loads. The repository you are working in has its own and it is
deliberately excluded; do not go looking for it.

**After any boundary, read one file.** When a hook prints `Read <path> before doing anything
else.`, that path is your context file: your memory, your task, your `## Tasks`, the step state
your Companion has kept, and your open subagent handles. Read it, once, as your first action.
It is always current. Do not search, do not re-read files it already tells you about, and do
not read it twice.

**Who you are is already in your system prompt.** Your persona arrived at launch and survives
everything. You never need to look it up.

**`/goal` points at your work item.** The goal you are given names a file. That file is the
whole task: `## Order` and every `## Order addendum` the Partner has appended, the
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
another agent. A hook will refuse the write and tell you why. `orders/` belongs to the Partner.

Run `hx task` to print your order and its addenda at any time.
