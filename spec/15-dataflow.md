## 15. Data flow

Human → Partner → HarnessAgents → Subagents → HarnessAgents → Partner → Human, with one sideways loop: every Companion compaction also becomes an episode in the instance-global memory store, which flows back into every agent's context file. Solid arrows are data the receiver reads; dashed arrows are wake signals; dotted arrows into and out of the memory store are the episode path. Every hand-off is a file path, never inline content. The human drives the Partner by talking to it; the Partner drives workers with the harness.

```mermaid
flowchart TB
  H[Human]

  subgraph P["Partner (HarnessAgent, id=partner, no work item)"]
    PG["Partner main thread\n(goal given in chat, every time)"]
    PC["Partner Companion\nstate/partner/*.json"]
    PM["PARTNER.md\n(Partner memory)"]
  end

  subgraph CP["Control plane (hx, deterministic)"]
    OF["goal file / addendum file\n(written by the Partner, deleted by hx after it reads it)"]
    T["tasks.json\n{goal, addenda, outcome}"]
    WI["pods/&lt;pod&gt;/&lt;id&gt;-&lt;state&gt;.md\nidle · working · complete"]
    CF["run/&lt;id&gt;/&lt;stream&gt;.context.md\n(one file the agent reads)"]
    PF["run/&lt;id&gt;/persona.md\n(system prompt at launch)"]
    ID["config/&lt;id&gt;/AGENTS.md\npersona ▲ header ▼ agent memory"]
  end

  subgraph W["HarnessAgent (full Claude Code, /goal, bypass)"]
    WA["main thread"]
    WC["Companion\nstate/&lt;id&gt;/&lt;id&gt;-main.json"]
    L["logs/&lt;id&gt;/&lt;id&gt;-main.jsonl\n(Companion-only)"]
    GIT["workdir (any dir the Partner chose)\ncommit-as-you-go"]
  end

  subgraph S["Subagents (inside the HarnessAgent)"]
    SA["sNNN"]
    SC["state/&lt;id&gt;/&lt;id&gt;-sNNN.json\n+ .digest.md"]
    SL["logs/&lt;id&gt;/&lt;id&gt;-sNNN-open|closed.jsonl"]
  end

  subgraph M["Episode memory (instance-global, docs/memory.md)"]
    MQ["state/memory/queue/*.json\n(one episode per boundary: pass · seam · compact · complete)"]
    MC["state/memory/chroma\ncollection `episodes` · ts, role, pod, id, kind, seq"]
    MS["hx memory search\n(own role first, recency-weighted)"]
  end

  H -- "1 gives the Partner its goal in chat (tmux attach -t partner)" --> PG
  PG -- "2 writes an goal file" --> OF
  OF -- "hx dispatch: verbatim, then deleted" --> T
  T -- "render" --> WI
  WI -. "3 /goal pointer pasted (working)" .-> WA
  ID -- "above header, at launch" --> PF
  PF -- "system prompt: no read" --> WA
  ID -- "below header" --> CF
  T -- "goal + addenda" --> CF
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
  WA -- "8 hx complete: checks → ## Digest, outcome, HX-COMPLETE" --> WI
  WA -- "memory below header" --> ID
  WI -. "9 hx wake partner" .-> PG
  WI -- "hx read: ## Digest" --> PG
  PG -- "10 update" --> PM
  PC -- "Partner step state" --> PG
  PG -- "11 decides: next dispatch · hx resume (addendum) · hx bench" --> OF
  PG -- "12 reports in chat" --> H
  H -- "answers a decision in chat" --> PG
  WC -. "E1 every ingested state (pass), seam, compact" .-> MQ
  PC -. "E1 Partner passes too" .-> MQ
  WI -. "E1 Digest at hx complete" .-> MQ
  MQ -. "E2 drained under index.lock by hx memory / hx compose" .-> MC
  MC -. "E3 top-k same-role episodes of other agents → ## Memory episodes" .-> CF
  MC -. "E4 on demand" .-> MS
  MS -. "answers, --all-roles to widen" .-> WA
  MS -. "answers" .-> PG
```

**Reading the numbers.** 1 is the human giving the Partner its goal, in conversation, every time; there is no goal file and no dispatch for the Partner, and nothing else starts its work. 2–3 are dispatch: the goal is a file hx consumes and deletes, so the text ends up in exactly two places, and the pointer is the only thing pasted. 4 is the only read an agent does to know what it is doing and where it left off; who it is came with the system prompt at launch; the read repeats after every seam and resume. 5 is continuous: every tool call becomes evidence the Companion turns into step state. 6–7 are the subagent round trip, with the parent receiving a Companion-written digest, not a transcript. 8 is the provable end of a task: checks first, then the line the evaluator reads. 9–11 close the loop through the Partner's memory, and the Partner decides for itself when the next goal goes out. 12 is the Partner telling the human, in chat, what happened; a `decision` comes back down as an addendum, not a fresh start.

**Reading the letters.** E1 is the write side of episode memory and costs a hook one small file: every state the Companion produces (a `pass`), every seam, every native compaction and every Digest is queued as an episode with its time, agent, pod, role and kind. E2 is the only place ChromaDB is opened, always under `state/memory/index.lock`, so many agents' processes share one store safely. E3 is the passive read: at every boundary `hx compose` queries the store with the stream's own step state and puts the closest recency-weighted episodes of other agents with the same role into the context file, so the agent continues instead of searching. E4 is the active read, `hx memory search`, filtered to the caller's role by default and widened with `--all-roles` only when the own-role result is empty or off-topic (the `hx-memory` skill). Nothing on the E arrows is a precondition: an empty, broken or uninstalled store changes the section's text and nothing else.

**What never crosses an arrow.** Raw logs (Companion-only). Claude's own compaction summary (bypassed by seams). Task text on a command line (always a file, then a pointer).
