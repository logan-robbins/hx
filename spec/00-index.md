# HarnessAgent Runtime — Spec Index

Each file is one section. Edit one file per change; cross-references use file names.

| File | Covers |
|---|---|
| `01-terminology.md` | Terms |
| `02-decisions.md` | Architecture decisions |
| `03-layout.md` | Filesystem layout |
| `04-ownership.md` | Single writer per artifact |
| `05-configuration.md` | `models.json`, `harness.json` |
| `06-work-items.md` | Work item states (idle, working, complete), transitions, `/goal` pointer, executable definition of done, resume, template |
| `07-streams-and-step-state.md` | Raw stream (Companion-only), step state, context file, continuity checkpoints |
| `08-hx-cli.md` | `hx` commands |
| `09-hooks.md` | Hook events, seam handshake, Claude Code event mapping |
| `10-companion.md` | Companion process, prompts, output style, seam policy, digests, episode memory |
| `11-adapters.md` | Claude Code adapter: config home, persona, threshold, launch |
| `12-partner-loop.md` | Partner operating loop |
| `13-build-order.md` | Milestones and acceptance tests |
| `14-open-items.md` | Decisions pinned at implementation, with the milestone that verifies each |
| `15-dataflow.md` | Data flow human → Partner → HarnessAgents → Subagents and back, plus the episode-memory loop |
| `16-ui.md` | Observing UI: board, agent, Partner chat, orders, archive |
| `17-packaging.md` | Package vs instance, `hx install`, isolation from the user's Claude, launch, skills, build plan |
