# Search and retrieval: code, graph, content, and execution

hx keeps raw data out of the model's context by programming the analysis and
retrieving exact text on demand. Four commands share that contract; none of
them is a security sandbox — the fleet already runs with full permissions, so
execution isolation here means a disposable subprocess whose only way back
into the conversation is its stdout.

## The context-economy contract

- No raw payload larger than about 5 KB enters worker context directly.
  Larger output is indexed and the model retrieves the sections it needs.
- Analysis is programmed, not read: counting, filtering, comparing, and
  parsing happen in `hx exec`, and only the printed answer returns.
- Retrieval returns exact content, never a summary of content the caller
  will need verbatim (code examples, signatures, contracts).
- Deterministic search nominates; decision models only sift. A score never
  establishes absence, validates a graph edge, or completes a task.

## Code search — `hx code`

`hx code index --repo PATH` builds one instance SQLite graph at
`state/code-index/index.db`, outside every product checkout. Each repository gets
a namespace: `.hx/map/manifest.json`'s `repo_id`, else a git remote, else its
resolved path. Indexing another repository preserves previous namespaces. Git
file discovery includes tracked and nonignored untracked files; nongit trees use
a directory walk. There is no file-count cap. `--max-file-bytes` or
`HX_CODE_MAX_FILE_BYTES` sets the default 512 KiB body budget. Binary/oversize
bodies are counted as skipped; their streamed content fingerprints still support
scope freshness and asset assignments. Excluded oversize bodies cannot certify
an absence lint. Porter and optional trigram FTS reference one
stored body; `--porter-only` and SQLite without trigram remain supported.

Immutable snapshots bind repository, commit, overlay and extractor generation. Queries
accept `--at COMMIT` without checking out that revision; dirty overlays remain separate.
Content/extraction objects and persistent path maps reuse unchanged data. Named lookups
select indexed postings and snapshot membership before result caps. A host repository
service can share the same objects across instances behind the CLI; ownership and early
published-contract proof still come from the requesting instance's ledger.

Python AST and structural brace extraction for C#, TypeScript/JavaScript, Go,
and Rust capture qualified declarations, attributes, spans and source headers.
Software AG Natural has a separate stdlib extractor for object/routine declarations,
parameter/data fields, literal calls and dependencies, with dynamic targets kept
as unresolved references. [Natural source support](#natural-source-support)
describes the exported formats and query examples.
Other text languages remain searchable. Optional syntax precedence is tree-sitter
**0.25.10** tags (CLI, C compiler and operator grammar checkouts with parser and
`queries/tags.scm` required), then Universal Ctags JSON, then stdlib extraction.
Set `HX_TREE_SITTER_GRAMMARS` to colon-separated grammar directories. Unknown
versions/formats degrade to stdlib; BSD ctags is refused. Every answer preserves
`repo`, `lane`, `extractor`, `anchor_sha` and `stale`. Signatures preserve source
header tokens with whitespace normalized; compiler/parser-supplied signatures
are labelled by their extractor. Syntax references remain candidates.

```sh
hx code index --repo /path/to/repo
hx code type IOrderRepository --expect exists
hx code signature Namespace.OrderRepository.Add
hx code signature Namespace.OrderRepository.Add --at COMMIT
hx code callers Add --count
hx code register IOrderRepository
hx code tests-for src/OrderRepository.cs --expect exists
hx code conform IOrderRepository --expect clean
hx code precedent AddScoped --language csharp
hx code search "integration event" --sift "Does this publish integration events?"
hx code contract IOrderRepository --expect absent
hx code fresh src/OrderRepository.cs --namespace REPO_ID --expect fresh
hx code projects Ordering
hx code map --budget 5000 --focus src/OrderRepository.cs
hx code edges src/OrderRepository.cs
hx code submaps --focus src
hx code stats
```

Named queries search all namespaces unless `--namespace` is given. Queries cap
results at 100 (`--limit`, default 20); maps additionally cap their JSON bytes.
Exact symbol search is case folded and lane ranked (resolved, syntax, text,
imported). FTS rankings use reciprocal rank fusion and term proximity; a typo
can fall back to edit-distance correction. `precedent` returns verbatim windows.
Callers include syntactic reference sites and provenance, excluding defining
files; `--count` reports distinct-file fan-in. Test lookup uses names,
attributes, references and implementation contracts. `conform` compares members
after whitespace and parameter-name normalization, reports missing or mismatched
members, and refuses clean parity for uncertainty (including overloads, generic
constraints and defaults). `--owners` reads current ledger leases on every call.

Every query accepts `--expect exists|absent|one|clean|fresh`: success is 0,
a failed expectation 3, usage errors 2. `clean` is the conform audit. A bounded
query's fresh rows cannot prove an entire scope fresh: use `fresh PATH...` for
that gate. Stale facts remain visible. Plan validation and admission compare the
indexed head's ancestry, committed scope diff and current file hashes. Missing
caches bootstrap; other checkouts and revisions retain independent immutable views.
A stale scope in the indexed checkout refuses with its rebuild
command. Completion refreshes durable `changed_paths` and retries that refresh
on publication replay; scoped refresh invalidates dependents. The installed
`bin/code-index-post-merge` is the operator's post-merge refresh trigger:

```sh
HARNESS_ROOT=/path/to/instance HX_BIN=/path/to/hx \
  /path/to/instance/bin/code-index-post-merge
```

Run it from the post-merge job/hook in the repository, or set `HX_CODE_REPO`.
It does not install hooks or write index files into the repository.
Prepared changesets record exact changed paths/modes/blobs/deletions; publication and
replay are durable. `hx code journal --namespace REPO_ID --replay CHANGESET` reconciles
interrupted derived publication. See [agent-scale runtime](agent-scale-runtime.md) for
shared metadata, sparse assignment provisioning and reusable check/build artifacts.

Resolved indexing is optional and explicit:
`index --resolved --indexer-checkout /isolated/checkout`. The checkout must be
separate, clean, and at the product's committed HEAD. Available SCIP indexers run
there; `scip print --json` supplies definitions, references, signatures,
enclosing symbols and implementation candidates. Missing tools skip; indexer
failures degrade. C# restore/build artifacts remain in the disposable checkout;
new `index.scip` output is removed after ingestion. `HX_SCIP_COMMANDS` can supply
operator argv lists keyed by indexer name. No server or per-worker index is used.

External analyzers can import JSONL without a compiler:

```sh
hx code import --namespace legacy --repo /path/to/legacy --extractor legacy-tool \
  --symbols symbols.jsonl --edges edges.jsonl
hx code contracts --register published-contracts.json
hx code contract Customer --owners
```

A symbol row is `{name,qualified,kind,path,line,end_line,signature}` with optional
attributes; an edge is `{from,to,kind,evidence}`. Paths are repository relative.
Imports are atomic and idempotent per namespace/tool, preserve custom edge kinds,
and mark missing source spans unanchored. A published contract record is
`{name,path,version,producer,repo,sha}`, where `sha` is its Git commit and `repo`
is an indexed namespace. The same name can appear in source and target namespaces
with separate producers and versions; a Partner queries one shared registry.

PageRank uses fixed file order and personalized focus. Louvain local movement,
multilevel aggregation and connected-component refinement are deterministic.
`submaps` reports the directory-layout modularity baseline and proposes components
only when measured modularity beats it. Candidates include bounded member and
boundary sets plus application-map `put` operations and expected versions.
The companion supplies the active run, snapshot and evidence envelope; normal
map validation applies. No query validates a relation or acquires ownership.

## Natural source support

Natural source indexing requires UTF-8 text exports and uses `stdlib-natural`
provenance. Extension matching and symbol lookup are case insensitive. It supports
these source formats without a Natural compiler or additional packages:

| Extensions | Source objects |
| --- | --- |
| `.nat` | Generic Natural text export |
| `.nsp`, `.nsn`, `.nsh` | Programs, subprograms, helproutines |
| `.nss`, `.ns7` | Subroutines, functions |
| `.nsc` | Copycode |
| `.nsg`, `.nsl`, `.nsa` | Global, local, parameter data areas |
| `.nsm`, `.nsd` | Maps, DDMs |

Functions and external subroutines retain their public declaration names even when
their filenames differ. Inline and nested routines gain their containing object's
or routine's prefix. Routine signatures include return layouts and data declarations;
fields retain their level-qualified names, formats and parameter modes. Maps and
DDMs expose object anchors and searchable text without interpreting their layouts.

Literal `CALLNAT` and `FETCH` targets, named `PERFORM` targets, and symbolic
`NAME(<...>)` function calls produce candidate call sites. Dynamic operands and
variable prototype calls remain `dynamic_calls` references without static call
edges. Prototypes expose declared interfaces without suppressing real caller sites.
Copycode, data-area, DDM and literal map dependencies produce import candidates.
File matching prefers one same-directory object, then one unique repository object;
ambiguous targets do not choose a file. Library/steplib resolution and parameter
parity remain outside this syntax lane.

Listing numbers and comments are removed from signatures while anchors hash the
original physical lines, including their line endings. Structured terminators and
reporting-mode `DEFINE name`/`RETURN` bound routine spans. Application-map symbol
anchors support these spans and refuse stale indexed declarations. Raw server
serialization and non-UTF-8 source must be converted before indexing.

```sh
hx code index --repo /path/to/natural-source
hx code signature 'F#TOTAL' --expect one
hx code signature 'PAY-ONE' --expect exists
hx code symbols 'RUN.CHECK-INPUT'
hx code type ARGS --expect exists
hx code callers 'F#TOTAL' --count
hx code edges RUN.NSP
hx code precedent 'CALLNAT' --language natural
hx code fresh RUN.NSP --expect fresh
```

Use `--namespace REPO_ID` when shared namespaces contain duplicate names. Quote
names containing `#` in shell commands. `type` accepts data areas and DDMs;
callable objects use `symbols` or `signature`.

## Graph search

Graph search remains the application map's bounded traversal: lexical seeds,
validated edges interleaved by kind and direction, interface consumers as a
second boundary, and explicit coverage counts (available, inspected,
omitted). The code index feeds it in two ways: derived candidate edges give
a cold repository a graph on day one, and Jev sifting (below) can suggest
where a responsibility likely lives when lexical seeds find nothing. Search
scores cannot validate edges.

## Content store — `hx content`

`hx content` is the persistent exact-text store for documentation, web
pages, skill prompts, and large tool output, at `state/content/index.db`.
Markdown is chunked by headings with fenced code blocks kept intact;
oversized sections split at paragraph boundaries (4 KiB cap). Search runs
the same Porter + trigram + RRF pipeline with title weighting, proximity
boost, fuzzy fallback, source and `code`/`prose` filters, and staleness
flags when an indexed file changed on disk.

```sh
hx content index --path docs/spec.md --source spec
hx content index --content "# Title ..." --source notes
hx content search "cleanup pattern" "authentication middleware"
hx content fetch https://example.com/docs --source example-docs
hx content stats
```

`fetch` converts HTML to markdown, indexes it, and returns a preview only.
Fetches are cached per URL for 24 hours by default (`--ttl-ms`, `--force`
to bypass), restricted to http/https, and blocked from link-local cloud
metadata, multicast, and reserved ranges; `HX_FETCH_STRICT=1` additionally
blocks loopback and private ranges. Fetched content is secret-redacted
before storage.

## Isolated execution — `hx exec`

```sh
hx exec --language python --code "print(sum(1 for _ in open('big.log')))"
hx exec file huge.log --language python --code "print(FILE_CONTENT.count('ERROR'))"
hx exec batch --file commands.json --queries "failing tests,HTTP 500"
```

Python, JavaScript/TypeScript (via `node` when present), and shell run in a
fresh temporary directory with a timeout and process-group kill; only
stdout returns. Output over 5 KB with an `--intent` is indexed into the
content store and only matching sections return; output over 100 KB always
returns an index pointer instead of raw text. `exec file` exposes the file
as `FILE_CONTENT` inside the subprocess so its bytes never enter context.
`batch` runs many labelled shell commands (optionally concurrently for
I/O-bound work), auto-indexes every output, and answers the given queries
in the same call.

## Decision models — `hx sift`

Jev (`jev-1.13.0`, the current pinned TypeSafe System One model) judges
bounded candidate sets through `hx sift`:

```sh
hx sift candidates --question "which file handles login?" --file candidates.json
hx sift triage --file failed-output.txt
```

Candidates are nominated deterministically (code/content search, grep) and
judged under safe internal keys with one atomic, positively phrased
question each. Results are unioned: deterministic hits are facts, Jev-only
hits are labelled candidates, and a Jev failure returns the deterministic
set marked `unsifted` — search is never blocked and no substitute scorer is
invented. The same module orders candidate checks for changed code
(deterministic matches first, Jev candidates next, the rest `not_run`) and
attaches a tentative failure category to failed output; ordering never
shrinks the acceptance suite, and a category never implies pass, fail, or
retry permission. Judgments are cached by model, question, and candidate
bytes under `state/content/sift-cache.json`.

Decision models must never count, compare versions, check permissions or
leases, validate receipts or graph edges, or certify completion. Those stay
in code, as do the 4,000-byte / 16-question / 500 ms Jev bounds.

## Status and boundaries

These commands are operator and worker tools available now. Automatic
consultation of the code index during plan brief construction, and routing
of planned-run tool output through intent indexing before Jev surplus
judgment, remain follow-on integration; the planned packet path is
unchanged. Indexes are rebuildable derived state under `state/` and are
never the ledger's authority.

## Prompt caching and companion actions

Invariant information — persona, learnings, policy, tool schemas — belongs
in the stable prefix the provider caches; everything else is working state.
`hx cache assess --prefix-bytes N --age-s S --ttl 1h` reports whether that
prefix is warm, expiring, or cold (TTL 5 minutes by default, one hour with
`cache_ttl: "1h"`; prefixes under the provider's ~1,024-token minimum earn
no cache benefit at all). `hx cache budget` checks invariants fit under the
model's seam threshold, leaving the rest of the window as working room.

Companion passes respect the same clock: warm cache means append-only
deltas with no reordering of retained facts; a cold seam is the free moment
to compress, drop, and restructure, because the prefix rewrite is already
owed. Seams taken for token thresholds should prefer cold boundaries when
the work allows waiting for one.

Jev is also leveraged where the judgment is semantic and the options are
exact: `hx learn classify --jev` takes a Jev Choice over the fixed
learnings taxonomy as a second opinion (deterministic ranking on provider
failure), and `hx code search QUERY --sift "question"` reranks the index's
deterministic candidates with Jev scores merged in — every index hit is
still returned, so the model ranks facts, it never filters them out.

## Local graph benchmark — 2026-10-12

`tools/code-graph-benchmark.py` measured this branch of hx and the real
[dotnet/eShop repository](https://github.com/dotnet/eShop/tree/dc7ea499cd356924fb6689b3702964a5869dbae9).
The complete commands, commits, coverage, timing and token counts are in
[the benchmark receipt](code-graph-benchmark.json). All index databases and
compiler/provider cache state stayed in an external temporary instance.

| Repository | Index time | Added SQLite bytes | Text files | Extracted symbols | Source-file coverage | Signature coverage | Resolved references |
|---|---:|---:|---:|---:|---:|---:|---:|
| hx | 14.859 s | 50,208,768 | 452 | 3446 | 100% | 100% | 0% |
| eShop | 1.860 s | 40,235,008 | 913 | 3098 | 100% | 100% | 0% |

Source-file coverage counts Python, C#, TypeScript/JavaScript, Go and Rust files.
Signature coverage is the fraction of **extracted** symbols with a signature;
it does not measure recall against every declaration in the repository. Binary
and oversize bodies stay outside FTS, with fingerprints retained for freshness.
The second database measurement is added bytes in the same shared store, not a
second per-repository database. The hx tree includes the preserved local spec
deletions and current documentation changes, so its receipt records `dirty: true`.

| Fixed question | hx graph latency | hx graph / grep tokens | eShop graph latency | eShop graph / grep tokens |
|---|---:|---:|---:|---:|
| existing type, case folded | 4.210 ms | 180 / 3919 | 4.384 ms | 206 / 897 |
| exact signature | 3.550 ms | 183 / 57 | 4.068 ms | 201 / 44 |
| callers | 21.767 ms | 2509 / 1015 | 19.941 ms | 2724 / 897 |
| tests | 525.667 ms | 4169 / 4967 | 73.031 ms | 1740 / 897 |
| parity diagnostic | 31.700 ms | 237 / 170 | 36.519 ms | 2199 / 897 |
| existing precedent | 16.693 ms | 630 / 134 | 5.294 ms | 946 / 235 |
| current owner | 4.586 ms | 185 / 3919 | 5.320 ms | 211 / 897 |
| fuzzy service intent with Jev | 35.226 ms | 2015 / 37 | 43.685 ms | 2068 / 1422 |

All eight fixed questions were answered in each repository without a follow-up
grep. A separate shared registry query returned the published records from both
namespaces. Fixture tests additionally prove same-name source/target contracts
and live ownership changes; the benchmark instance itself has no active leases.
The hx parity question correctly reports no known implementation; the eShop
question finds `OrderRepository`, matches its members and reports its inherited
generic contract as uncertain. These are diagnostic answers, not compiler proofs.

Latencies are warm in-process medians of three calls, including SQLite opening
and closing; they exclude Python CLI startup. The fuzzy questions were actually
Jev-sifted, with ten scored candidates per repository. Their warm timings include
cached sifting rather than an uncached provider round trip. Token counts use
`tiktoken` `o200k_base`, installed only for the benchmark, and are comparison
counts rather than provider billing. The baseline is literal `rg -n -i -F`
output; it cannot itself prove signature parity or ownership.

The graph saves substantial answer tokens for the case-folded type lookup.
Full provenance JSON costs more than short literal grep output for several
other questions, including exact signatures and precedent. No general token
savings or published performance figure is claimed as an hx result. Lexical
candidates plus Jev found both fuzzy targets in this question set, so this
measurement does not justify a required embedding dependency.

One manual review of hx submaps found that modularity improves over directory
layout but the large communities mix responsibilities (runtime/map/planning,
CLI/work-items, and indexing/configuration utilities). They are too broad to
become one assignment without further decomposition. No proposal was promoted
or asserted as a semantic boundary.

- hx: Louvain modularity 0.226193, directory baseline 0.015424; returned community sizes 97, 71, 102, 3.
- eShop: Louvain modularity 0.510429, directory baseline 0.022889; returned community sizes 21, 52, 41, 16, 44, 80, 50, 53.

Promotion remains unmeasured: no live fleet wave has run. The receipt stores
`live_waves: 0` and `promoted: null`; derived tables remain the useful product
and the promotion path is not expanded. Actual tree-sitter, Universal Ctags and
SCIP prerequisites are absent here, so their live checks skip with named reasons;
protocol/format fixtures and the stdlib extractors always run.

The complete milestone suite passes **2,103 tests**, with **26 skips**, using
the guard's supported `HX_CLAUDE_HOME_BASELINE` override and a separate temporary
manifest captured immediately before testing. Full core/fake-harness, UI,
packaging, scenario and guard results are in the
[validation handoff](../handoff/to-orchestrator.md). The default persisted
September user-home baseline still differs at eight files whose modification
times predate this goal; it was preserved, and that default environmental check
is not reported as green.
