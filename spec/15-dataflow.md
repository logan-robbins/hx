## 15. Data flow

Human → Partner → HarnessAgents → Subagents → HarnessAgents → Partner → Human. Solid arrows are data the receiver reads; dashed arrows are wake signals. Every hand-off is a file path, never inline content, and the Partner is driven by the same mechanism it drives workers with.

```mermaid
flowchart TB
  H[Human]

  subgraph P["Partner (HarnessAgent, id=partner)"]
    PWI["pods/partner/partner-working.md\n## Order · ## Definition of done · ## Tasks"]
    PG["Partner main thread\n(/goal pointer → its work item)"]
    PC["Partner Companion\nstate/partner/*.json"]
    PM["PARTNER.md\n(Partner memory)"]
  end

  subgraph CP["Control plane (hx, deterministic)"]
    O["orders/&lt;id&gt;.md\norders/&lt;id&gt;.addendum.md"]
    T["tasks.json\n{order, after, addenda, outcome}"]
    WI["pods/&lt;pod&gt;/&lt;id&gt;-&lt;state&gt;.md\nidle · queued · working · complete"]
    CF["run/&lt;id&gt;/&lt;stream&gt;.context.md\n(one file the agent reads)"]
    PF["run/&lt;id&gt;/persona.md\n(system prompt at launch)"]
    ID["config/&lt;id&gt;/AGENTS.md\npersona ▲ header ▼ agent memory"]
  end

  subgraph W["HarnessAgent (full Claude Code, /goal, bypass)"]
    WA["main thread"]
    WC["Companion\nstate/&lt;id&gt;/&lt;id&gt;-main.json"]
    L["logs/&lt;id&gt;/&lt;id&gt;-main.jsonl\n(Companion-only)"]
    GIT["worktree /work/wt/&lt;id&gt;\ncommit-as-you-go"]
  end

  subgraph S["Subagents (inside the HarnessAgent)"]
    SA["sNNN"]
    SC["state/&lt;id&gt;/&lt;id&gt;-sNNN.json\n+ .digest.md"]
    SL["logs/&lt;id&gt;/&lt;id&gt;-sNNN-open|closed.jsonl"]
  end

  H -- "1 chat in tmux attach -t partner" --> PG
  PG -- "writes orders/partner.md; hx dispatch partner (self)" --> PWI
  PWI -. "/goal pointer via stop hook (goal-pending)" .-> PG
  PG -- "2 writes orders" --> O
  O -- "hx dispatch: verbatim" --> T
  T -- "render" --> WI
  WI -. "3 /goal pointer pasted (working)\nor queued until after is done" .-> WA
  ID -- "above header, at launch" --> PF
  PF -- "system prompt: no read" --> WA
  ID -- "below header" --> CF
  T -- "order + addenda" --> CF
  WI -- "## Tasks" --> CF
  WC -- "step state" --> CF
  CF -- "4 one Read at start / seam / resume" --> WA
  WA -- "5 tool calls → log hook" --> L
  L -- "records" --> WC
  WA -- "edits ## Tasks" --> WI
  WA -- "commits" --> GIT
  WA -- "6 spawn" --> SA
  SA -- "log hook (agent_id)" --> SL
  SL --> SC
  SC -- "context file at SubagentStart" --> SA
  SA -- "7 SubagentStop → digest via PostToolUse(Agent)" --> WA
  WC -. "seam marker" .-> WA
  WA -- "8 hx complete: checks → ## Digest, outcome, HX-COMPLETE\npromotes queued dependents" --> WI
  WA -- "memory below header" --> ID
  WI -. "9 hx wake partner" .-> PG
  WI -- "hx read: ## Digest" --> PG
  PG -- "10 update" --> PM
  PC -- "Partner step state" --> PG
  PG -- "11 hx resume (addendum) · hx bench · re-dispatch" --> O
  PG -- "12 hx complete done: checks = hx board --require-done" --> PWI
  PG -- "reports in chat" --> H
  H -- "answers a decision in chat" --> PG
  PG -- "orders/partner.addendum.md; hx resume partner (self)" --> PWI
```

**Reading the numbers.** 1 is the human talking to the Partner; the Partner writes its own order and dispatches itself the way it dispatches workers, and the human never runs hx. 2–3 are dispatch: orders are files, the pointer is the only thing pasted, and `queued` items wait on `after` without a Partner wake. 4 is the only read an agent does to know what it is doing and where it left off; who it is came with the system prompt at launch; the read repeats after every seam and resume. 5 is continuous: every tool call becomes evidence the Companion turns into step state. 6–7 are the subagent round trip, with the parent receiving a Companion-written digest, not a transcript. 8 is the provable end of a task: checks first, then the line the evaluator reads. 9–12 close the loop through the Partner's memory back to the human, and a `decision` comes back down as an addendum, not a fresh start.

**What never crosses an arrow.** Raw logs (Companion-only). Claude's own compaction summary (bypassed by seams). Persona files of other agents (guard). Task text on a command line (always a file, then a pointer).
