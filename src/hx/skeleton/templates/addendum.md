Answering the open decision in your Digest: use a flat `checks` list, not a map keyed by check
name. `ui-004` already renders a list and the order of the checks is meaningful to the human
reading it; a map would lose that order and cost more to render than it saves.

Nothing else in the order changes. Keep the `## Tasks` you have, keep what you have already
committed, and continue from where you stopped — you do not need to re-read `src/hx/board.py`,
your notes on it still hold.

One thing to add while you are in this file: `detail` must be `null`, never an empty string,
when a check passes with nothing to say. The UI distinguishes the two and `hx board --json`
already follows that rule ("absent values are `null`, never omitted").

The definition of done and its `### Checks` are unchanged, and the check that `docs/` and the
UI are untouched still applies.
