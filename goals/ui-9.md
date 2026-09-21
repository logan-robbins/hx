# ui-9: the UI against the real M8 run

Sent after build-10. Read `goals/build-10.done.md`, `goals/ui-8.done.md`.

1. Serve `hx ui` against the M8 instance the build lane leaves behind (or rebuild it from the
   pack) and walk every screen: graph, board, agents, drawer, partner chat, activity, orders,
   archive. Fix whatever real data breaks that fixtures did not. Paste `/api/board` and the
   graph's node list in the done file.
2. Pane captures split into transcript and "still in the input box" using `hx.goal.input_box`,
   on the Partner page and in every agent drawer; the unsent part is styled as such and never
   read as something the agent said. One test each.
3. The orchestrator walks the same screens in the browser before the goal is accepted.
4. `tools/milestone-check.sh ui` passes; committed path-scoped; `goals/ui-9.done.md` short.
