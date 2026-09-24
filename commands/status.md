---
description: Show the hx fleet board — agents, state, tmux sessions.
---

# /hx:status — fleet board

Run `hx board` and summarize what is on disk: one line per agent with its id, state
(working, idle, blocked), tmux session, and goal if it has one. Say plainly when the
fleet is empty or everything is idle. Do not invent activity that is not in the output.
If `hx` is not installed or no instance exists, say so and point at `/hx:setup`.
