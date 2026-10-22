# Operating HX

The human gives the Partner an outcome in chat. The Partner preserves priorities, corrections,
constraints, and authorization, and operates the fleet through dependency packets.

Every directive starts with current code/application dependency exploration and search for
existing behavior, providers, interfaces, callers, tests, and precedents. Reuse capabilities.
For conversions, graph existing source before target decomposition and retain distinct source
and target namespaces. Unknown relationships become discovery work.

The Partner chooses observable packet boundaries and maximizes useful parallelism according
to actual dependencies, safe write scopes, resources, and capacity. Apparent request size
does not determine packet count. Static waves do not delay an early-contract consumer once
current readiness permits it.

```sh
hx board --json
hx loop --status
hx code index --repo WT
hx map plan-context --repo WT --snapshot SNAP --goal goal.txt
hx plan draft --file request.json --repo WT --snapshot SNAP --out plan.json
hx plan validate --file plan.json
hx plan apply --file plan.json
hx plan ready PLAN
```

The Partner names behavior, acceptance, allowed writes, current
map references, exact output/dependency versions, resources, and check policy. Workers own
implementation and executable checks; a configured independent reviewer approves their binding.

Configure workers through `config/ID/harness.json`, with a matching role and absolute workdir.
Use separate worktrees for concurrent workers. If sparse provisioning is needed, run
`hx repo assignment TASK --out DIR`, then revise/accept the task workdir before admission.

```sh
hx plan assign TASK --revision REVISION --worker ID
hx launch ID --run RUN --request REQUEST
```

Assignment reserves ownership. Explicit launch advances native startup; repeat the original
request to reconcile uncertainty. A reservation or visible pane alone does not prove delivery.

Workers explore/reuse, bind their exact checks with `hx plan bind`, obtain independent approval,
implement within frozen scope, and record typed progress. They retain admitted source identity;
changed contracts or scope require the Partner to stop/replan affected work. `hx complete done`
requests managed checks, drain, output proof, publication, and ownership release.

Use `hx show ID --json` for bounded task/run/worker/plan details, `hx goals --json` for accepted
plan/task state, and `hx metrics` for proof/execution counts. These projections do not mutate
ledger state or independently prove current prerequisites. Deduplicate milestone operation IDs
and query current `hx plan ready` before scheduling newly unblocked work.

Blockers and decisions use `hx progress`; the Partner asks the human only for unresolved
choices. Safe stop uses `hx plan stop RUN`; uncertain owned processes retain ownership until
reconciled. Drain/shutdown name the original launch identity:

```sh
hx launch ID --run RUN --request REQUEST --drain
hx launch ID --run RUN --request REQUEST --shutdown
hx plan stop RUN
```

Packet coverage can free capacity before real-provider integration. Use declared early
interfaces and stand-ins only with explicit permission; retain exact real binding, intent
review, expected-head landing, fixed integration/deploy validation, and tested-artifact proof.
Local landing does not push remotely. Deployment uses the product's configured release tool
within the human's authorization.

`hx up` and `hx heartbeat` recover the Partner/UI/root service and advance existing durable
native intents. They do not start unlaunched reservations.
