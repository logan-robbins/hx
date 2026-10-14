# You are running under hx

This is the only CLAUDE.md that loads. The repository you are working in has its own and it is
deliberately excluded; do not go looking for it.

**After any boundary, one Read.** A hook ends with a line naming your work item — your goal,
your `## Tasks`, and the `## Status` hx rewrites at every boundary: the step state your
Companion kept, the episodes others left, your open subagent handles.
Open it with the **Read tool**, exactly once, before anything else. Not `cat`, not `head`, not
any Bash command: those spend the same tokens and do not count as the one read the harness
measures. Do not read it again later in the same turn. It is always current, and it is the only
file you need — do not search for context.

**Who you are is already in your context.** The same hook hands you your persona, your
identity line and your rules at every boundary. Never read a file to find out who you are.

**Do not re-read what the working set already tells you.** Files listed there carry the fact
you took from them. Read one again only if it has changed since.

**`/goal` points at your work item.** The goal you are given names a file. That file is the
whole task: `## Goal`, one paragraph, and every `## Goal addendum` the Partner has appended.
The rest is yours: `## Definition of done`, written before your first edit on top of your
role's standard set, `## Tasks`, and `## Evidence`. Keep `## Tasks` current — it is what you
get back after a seam.

**Your conversation will be cut and rebuilt.** That is a seam: normal, planned, and cheap. It
costs you nothing if `## Tasks` is current and your work is committed, and a great deal if it
is not. Commit each finished sub-task immediately with a descriptive message.

**`hx complete <outcome>` is your last action.** Nothing after it. `done` is machine-checked:
the worktree must be clean, no subagent stream may be open, and `## Evidence` must name what
proves each line of your definition of done. A failure prints `HX-CHECK-FAILED <id>` with the
reason and changes nothing — fix it and run it again. `blocked`, `decision`, and `exhausted`
check only for open subagents and are the honest answers when `done` is not available.

**What tripped you up is owed to every agent like you.** Every rule of this instance's
`LEARNINGS/` (one file per category of mistake) that applies to you — the global ones and
your role's — is printed into your context, whole, at every start, `/clear` and compaction;
read those files only when that context lists them as files instead. You keep no memory
file: when a wrong assumption, a
correction you had to make or a wasted cycle teaches you a rule, run `hx learn propose
--category <c> --signal … --check … --evidence …` the moment you understand it, with
`--role <your role>` when only your role needs it (`hx learn classify --signal … --check …`
ranks the categories). Never edit the files: a different session reviews every proposal,
which is what keeps each lesson one rule.

**What is not yours.** `config/`, `companion/`, `logs/`, `state/`, `run/`, `archive/`,
`pods/` other than your own `-working` item, that item's `## Status`, and `tasks.json` are
managed by hx or belong to another agent. Do not write there. `goals/` belongs to the Partner.

Run `hx task` to print your goal and its addenda at any time.
