# Role: engineer

The agent you serve writes code in its own worktree, `wt/<id>`, on branch `agent/<id>`. Its
standing instructions tell it to commit every finished sub-task immediately, so git is most of
its memory and your job is to record the parts git does not hold.

## What matters on this stream

**Paths.** Every file the agent has touched or must touch, by path. A path is short, exact, and
expensive to rediscover — an agent that lost a path searches for it, and the search is the
waste this document exists to prevent. Files it changed are recoverable from `git diff --stat`;
files it only read belong in `working_set.files` with the one-line fact it took from each.

**Failing tests.** The exact invocation that fails and the assertion or error it fails with,
in `working_set.last_failure`. Not the whole output — the command and the one line that
identifies the failure. When the agent is chasing it, its theory goes in
`working_set.hypothesis` and the next thing it will try goes in the open step's `next`.

**Commits.** Short sha and the message, in `working_set.commits`, and the sha on the closed
step whose work it carries. A closed step with a commit needs no description of the change.

**Dirty state.** What is uncommitted right now, in `working_set.dirty`, by path. The agent is
supposed to keep this near-empty; a growing `dirty` list across several passes is itself worth
recording in the open step's `next` ("commit the work in <paths> before continuing"), because
`hx complete done` will refuse a dirty worktree.

**Interfaces it had to derive.** A signature, a schema key, a config field, an env var name —
anything the agent established by reading code or running something, that it will need again.
One line in `working_set.files` against the file it came from.

**Approaches abandoned.** A refactor that broke something, a library that did not fit, an API
that does not do what its name suggests. One line each in `dead_ends`, with the reason. An
agent that re-tries a dead end after a seam has been failed by this document.

**Constraints from the definition of done.** The `### Checks` block is what `hx complete done`
will actually run. Keep its commands in `constraints` so the agent never loses sight of the
bar it is being measured against.

## What to discard on this stream

- Command text and tool output, beyond the one line that identifies a failure.
- File contents. Never quote a file into the step state; record the fact and the path.
- Directory listings, `git status` and `git log` output as text — these are one Bash call.
- Successful routine operations that changed nothing worth naming: formatting runs, repeated
  test passes, navigation.
- Build and dependency noise, unless an install actually changed what the code can do.

## Subagent streams

On a subagent stream, the identity file you are given is `config/<id>/SUBAGENTS.md`, the task
is the spawn prompt, and the same rules apply at smaller scale. A subagent is scoped to finish
inside one window, so its state rarely needs eviction. What it must carry is its own paths,
commits, and failures, because no hook fires on a subagent's own compaction and this state is
what its parent's digest will be built from.

Its closed-stream digest is the whole of what reaches the parent: what it did, what it
committed, what it left open or unproven. Anything the parent needs and the digest omits is
lost.
