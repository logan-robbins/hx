## Goal

Give hx a state-of-the-art structural graph for existing codebases — any
language, any build system — so agents reuse instead of rebuild: one bounded
lookup answers "does this exist, where is it, what is its signature, who
calls it, and who owns it" before any new code is proposed. This plan is
the technical implementation guide: it carries its own verdicts on the
state of the art (§Verdicts), the system-level lessons generalized from the
Natural-to-C# fleet that GRAPH.md was written against (§Lessons), and the
schema, commands, gates and tests to build (§Steps). It implements the
graph against hx's current code index and application map, without an MCP
server and without writing into product checkouts.

Validated 2026-10-12 against primary sources and against this host. The
design matches what the strongest published systems do — tree-sitter or
ctags syntax extraction, SCIP for resolved references, a persistent SQLite
graph refreshed from content hashes, community detection, a curated map
above it — and departs only where hx's constraints require: a CLI facade
instead of MCP, no index files in the product repository, deterministic
facts before model judgment, enforcement by exit code rather than by
prompt prose.

## Verdicts on the state of the art

Each figure or technique the plan leans on was traced to its primary source
before it was adopted. Secondary blog restatements were ignored.

| Technique or claim | Primary source and what it actually says | Verdict for hx |
|---|---|---|
| Tree-sitter knowledge graph in SQLite with content-hash refresh (codebase-memory-mcp) | README: Linux kernel 28M LOC / 75K files in ~3 min on an Apple M3 Pro, self-benchmarked; grammars vendored into one binary; optional LSP layer refines call edges. Paper arXiv 2603.27277 (31 repos): ~10× fewer tokens, 2.1× fewer tool calls, 83% answer quality vs 92% for a file-exploring agent. The "99% fewer tokens" is the README's marketing line. | **Adopt the architecture, not the figures.** Verify correctness and bounded work in step 9; published figures are not hx claims. LSP refinement rejected (a long-running server per language per worktree). |
| Build-centred graph (RIG + SPADE, arXiv 2601.10112) | 8 repos × 30 questions × 3 agents: +12.2% accuracy, −53.9% completion time, +17.7% accuracy on multilingual repos. SPADE's only automatic extractor is CMake; other build systems were hand-written. | **No tool to adopt.** The cheap equivalent — project manifests (`.csproj`/`.sln`, `package.json`) parsed with stdlib — joins the syntax lane (step 1). Declared checks already cover the test side. |
| Tree-sitter CLI as the extractor | Docs for `tree-sitter tags`: emits name, role (definition or reference), kind, location, first line, docstring per match of each grammar's `queries/tags.scm`; output layout undocumented; needs grammar checkouts the CLI can find and a C compiler to build them. `tree-sitter-c-sharp/queries/tags.scm` captures class/interface/method/namespace definitions and call, base-type, object-creation and variable-type references. | **Use it when all three prerequisites are present; never depend on it.** Parse tag lines behind a pinned-version fixture. On this host the CLI is absent, so the stdlib extractor is the default lane, not a fallback. |
| universal-ctags as an extractor | docs.ctags.io: JSON Lines output when built with jansson; `--fields=+S` adds `signature` only where the language parser supplies it. macOS ships BSD ctags, which rejects `--version`. | **Second tier of the syntax lane.** Signatures are sliced from source so the parser-dependent field is a bonus. Detect by the `--version` banner. |
| SCIP for resolved references | `scip.proto`: occurrences carry a `symbol_roles` bitset with `Definition`; `SymbolInformation` carries `signature_documentation`, `enclosing_symbol`, `kind`, `relationships[]` with `is_implementation`, `is_reference`, `is_type_definition`, `is_definition`. `scip print --json` is the documented scripted reader. scip-dotnet runs `dotnet restore` itself (MSBuild workspace; `--skip-dotnet-restore` exists). | **Adopt as the optional resolved lane.** Read JSON through the `scip` CLI (no protobuf code in hx), import `is_implementation` as `implements` candidates and `signature_documentation` as signatures. Resolved C# is a restore step confined to the indexer checkout. |
| Ranked repo map (Aider) | tree-sitter tags → file reference graph → personalised PageRank with focus files weighted → binary search to a token budget → definitions rendered with enclosing scope headers. | **Adopt for orientation** as `hx code map` (step 2), with fixed node order so output is deterministic. |
| Community detection for module boundaries | codebase-memory and Graphify ship it; Leiden is the current algorithm, Louvain its simpler predecessor whose defect is disconnected communities. | **Louvain plus a connected-components refinement**, fixed order and seed, directory layout as the baseline a proposal must beat (step 8). |
| Graphify, Graft, code-graph-rag as drop-in backbones | code-graph-rag: Memgraph via Docker, Python 3.12+, optional compiler frontends. Graphify: a skill writing `graph.json`/`graph.html`/`GRAPH_REPORT.md` into the repo, ~36 languages by AST. Graft (`@nanonets/graft`): tree-sitter, ~23 languages, graph stored as a `graft/` markdown folder in the repo. | **None adopted.** All write into the checkout or need a database service. Their shared design points are what this plan implements inside hx's existing SQLite store. |
| Hybrid vector + graph as "the frontier" | Every surveyed system that offers it makes embeddings optional; none measures reuse-question recall against lexical search. | **Keep lexical FTS + Jev sift.** Ship the agreed lookup design with correctness fixtures; optional embeddings remain outside this implementation. |

Host observation, same date (macOS, this checkout): `tree-sitter`,
universal-ctags, `scip`, every `scip-*` indexer, `rust-analyzer`, `go` and
`cargo` are absent; `/usr/bin/ctags` is BSD ctags; `dotnet` and `node` are
present; Python's bundled SQLite is 3.53 and accepts the trigram tokenizer.

## Lessons carried from the Natural-to-C# fleet

GRAPH.md's assessment was written against one conversion programme. These
are the parts that generalize to any hx instance, each with the plan
element it shapes.

1. **Reuse questions are name-keyed.** Almost every "does this exist?" in
   the fleet was an exact lookup (type, handler, test class, contract); the
   fuzzy "is there already a service for X?" was rare. → Exact lanes first
   and bounded; the fuzzy case is one correctness fixture (step 9), not the
   design centre.
2. **Over-exploration is the failure mode, not missing code.** Lane reads
   consumed most of lane wall time; the root session ran thousands of
   commands and compacted 17 times; a general "query the graph first"
   surface would be one more place to wander. → No browse or list-all
   command. Every query takes a name or path, returns a capped set, and
   says what to do with it (path + signature + owner), so one call ends
   the question.
3. **Agents do not read prose rules.** Lanes read the rule files zero
   times; the rules that held were the ones a script decided. → Every
   `hx code` query has a lint mode (`--expect`) with a distinct exit code,
   usable inside a goal's `### Checks` block; the freshness gate lives in
   `hx plan validate`; prompts only redirect.
4. **One ownership authority.** The fleet's single most protective rule
   was "never a second ownership registry". → The graph stores no
   ownership. "Who owns it" is a read-through join on the continuity
   ledger's leases at query time, exactly as the plan brief already does
   for anchors.
5. **Agent-appended layers go unused.** The hand-appended `## Learned`
   section stayed at zero lines in all thirteen maps; the regenerated
   tables were what agents used. → Derived, regenerated facts are the
   product. Candidate promotion uses the existing anchored application-map validation
   path; derived tables remain available independently of promotion.
6. **Freshness is a gate on the merge SHA, not a timer.** A ten-minute
   cron plus a snapshot regenerated by hand meant a scout goal re-derived
   claims every wave. → Index rows carry the indexed head SHA; dispatch
   refuses a stale scope; completion re-indexes the paths it changed.
7. **The costliest greps were parity audits and precedent hunts.** Seven
   build cycles went to an interface-versus-test signature audit done by
   diff; a transaction precedent was found by hand; cross-lane interface
   drift cost most of an integration window. → `hx code conform` (signature
   parity across interface, implementations and tests), `hx code
   precedent` (exact snippets of an existing pattern), and the contract
   registry (step 6).
8. **Case folding and attributes are first-class.** Misses came from
   `Handler` versus `handler` and from test classes identified only by
   attributes. → Case-folded symbol index; attribute-tagged rows.
9. **Source-side graphs may come from outside hx.** The legacy side had a
   pre-built edge set (`edges.jsonl` with `CALLS_STATIC`, `PERFORMS`,
   `INPUTS_MAP`) produced by analysers hx will never contain; many legacy
   languages have no tree-sitter grammar. → A documented JSONL import into
   a repository namespace (step 5), with `lane='imported'` provenance and
   free-form edge kinds preserved.
10. **Shared callees are the reuse unit.** "Build the service once, reuse N
    times" came from fan-in counts in the generated maps. → Fan-in is a
    column on `map` output and a `callers --count` mode.
11. **Keep the product repository clean; keep operations small.** One
    generator, one trigger, one SQLite file was maintainable across
    thirteen instances; anything writing into a worktree was denied by
    guard. → Store under instance `state/`, one post-merge trigger, no
    database service, no per-worktree index.
12. **Mixed adapters mean a shell facade.** Codex, Meta and Claude workers
    shared one fleet; per-agent MCP schemas cost prompt tokens on every
    turn and are not supported uniformly. → CLI only; bounded JSON.
13. **Figures need a primary source or a local measurement.** → §Verdicts
    above; step 9 before any default flips.

## Success Criteria

- A syntax-only index of an existing repo answers type/interface → path +
  signature, DI/registration sites, handler/test-class lookup (case-folded),
  callers, and signature parity, each in one bounded `hx code` query with
  no checkout-wide grep. Structural extractors cover Python,
  TypeScript/JavaScript, Go, C# and Rust; every other language gets
  universal-ctags rows when that binary is present and the text lane
  always. "Any language" means degraded, never absent.
- Signatures are stored verbatim from source (the declaration header
  text), so a signature answer is exact whichever extractor produced the
  row, never a regex approximation.
- Every query has a lint mode: `--expect exists|absent|one|clean|fresh`
  exits 0 when the expectation holds and 3 when it does not (2 stays the
  usage error, matching `hx wake`), so a goal's `### Checks` block can
  refuse a second interface, a missing test class, or a stale scope
  mechanically.
- The index lives outside every worktree (instance `state/`), has no file
  count cap, discovers files through `git ls-files`, rebuilds incrementally
  from content hashes, and every row carries an anchor hash and a lane
  (`resolved`, `syntax`, `text`, `imported`): changed source marks its rows
  stale and invalidates dependents, reusing the application map's
  freshness model.
- Ownership is never stored in the graph; `--owners` joins the continuity
  ledger's leases at query time.
- `hx map anchor --symbol` resolves qualified symbols for the five
  languages through the syntax lane, so the curated map gets symbol-level
  anchors outside Python and still fails explicitly when the index has no
  unambiguous row.
- An externally produced graph (symbols and edges as JSONL) imports into a
  repository namespace with provenance and free-form edge kinds intact.
- A shared contract registry indexes every published interface
  (name, path, version, producer, SHA) so "does a contract for X exist?"
  is one lookup for every Partner in the instance, not a branch hunt.
- The same store serves both graphs: the existing codebase being
  converted (source graph) and the application being built (target
  graph), namespaced by repository, so a Partner can trace a source
  capability to its target counterpart in one query.
- Dispatch refuses a stale map/index for its scope (base SHA mismatch),
  instead of re-deriving claims every wave.
- Decomposition proposals come from measured coupling (community detection
  over the import/call graph) as candidate feature submaps, identical on
  repeated runs; only the existing map validation path promotes them.
- Published performance figures are not claims about hx; operation-count fixtures verify bounded work
  on at least two real repos before it is relied on; §Verdicts figures
  are hypotheses, not inputs.

## Approach

hx already has the curated layers (application map, learnings,
contract-first stubs, exact lookup via search) and, as of this branch, a
derived code index — but that index is Python-exact and regex elsewhere,
with whole-file anchors for C#, no resolved references, no signatures, a
2,000-file cap, and one namespace per hx root. The plan adds the missing
structural lanes while keeping hx's proven constraints: CLI only (no
per-agent MCP schemas), derived state outside checkouts, deterministic
facts separate from model judgment, and Jev only sifting candidates the
index nominates.

Five lanes feed one store (today's `state/code-index/index.db`, extended):

1. **Syntax lane (new).** Per-language extraction producing the same
   symbol/edge shape as today's Python AST path, with one `extractor`
   value per row and this precedence:
   - `tree-sitter tags` — the CLI's tagging subcommand run over each
     grammar's `queries/tags.scm`. It needs the CLI, grammar checkouts the
     CLI can find, and a C compiler to build them; all three are detected.
   - universal-ctags — `--output-format=json` JSON Lines with line, end,
     scope and signature fields where the language parser supplies them;
     150+ languages, no grammar builds. Detected by its `--version`
     banner: the `ctags` that ships with macOS is BSD ctags and is not it.
   - hx's own stdlib extractor. A real deliverable, not a stub: on most
     operator hosts it is the only extractor present. It must produce
     nested qualified names (`Namespace.Class.Method`), declaration spans,
     attributes and signatures for C#, TypeScript/JavaScript, Go and Rust
     by brace and indent tracking, replacing today's flat one-line regex
     table.

   No build is run; syntax only, so indexing never needs the repo to
   compile. Project manifests belong to this lane too: `.csproj`/`.sln`
   (ProjectReference, PackageReference, a test SDK marks a test project)
   and `package.json` workspaces parse with `xml.etree` and `json` into
   project nodes and `project_depends_on` edges — the cheap part of a
   build-centred graph, with no MSBuild.
2. **Resolved lane (new, optional).** When a SCIP indexer for the repo's
   language is on PATH — `scip-python`, `scip-typescript`, `scip-go`,
   `scip-java`, `scip-clang`, `scip-ruby`, `scip-dotnet`, or
   `rust-analyzer scip` — run it on the post-merge indexer checkout and
   read its `index.scip` through `scip print --json`. Import occurrences
   carrying the `Definition` role as definitions, other occurrences as
   resolved references, `signature_documentation` as the signature,
   `enclosing_symbol` as the qualifier, and `relationships[]
   .is_implementation` as `implements` candidate edges. Rows imported this
   way outrank syntax-lane rows. Absent binaries degrade to lane 1, never
   to failure. `scip-dotnet` runs `dotnet restore` before indexing, so
   resolved C# is a restore, not a no-build step; it runs only on the
   indexer checkout, never in a lane worktree.
3. **Text lane (exists).** Today's Porter + trigram FTS5 over source text
   stays, covering string DI, reflection, route literals, ORM names and
   precedent search — the syntax blind spots. Each body is stored once;
   both FTS tables become external-content tables over it.
4. **Imported lane (new).** Symbols and edges produced by analysers
   outside hx (legacy-language tooling, a vendor's call graph) load from
   JSONL into a repository namespace with `lane='imported'` and the tool
   name as `extractor`. Edge kinds are preserved as given; the known kinds
   (`calls`, `imports`, `implements`, `tests`) map onto the application
   map's candidate-edge export, the rest ride along as attributes.
5. **Curated lane (exists).** The application map remains the validated
   layer; derived lanes propose candidate records and edges only.

On top: a contract registry table in the same store, a lint mode on every
query, a freshness gate in plan validation, deterministic ranking
(personalised PageRank over the file reference graph for bounded "repo
map" answers, with fan-in counts), and community detection (Louvain with a
connected-components refinement and fixed node order, implemented in
stdlib) emitting candidate submaps.

The store is dual-purpose and shared. Every repository an instance works
on gets a namespace in the one graph: the existing codebase in a
conversion (source graph) and the application being built (target
graph). Multiple Partners in the instance all read and write that same
graph — it is instance state in `state/`, already shared by every
Partner and worker, so there is no federation layer, no cross-instance
sync, and no new authentication: a Partner is identified by the
instance's existing session identity, exactly as for the ledger. Source
and target rows link by contract and capability name, which is what
makes "has X been converted, and where is its counterpart?" one query
instead of a wave of greps.

## Key Decisions

- **CLI, not MCP.** A shell facade works on every adapter and costs no
  per-turn prompt tokens (Lesson 12). All new queries are `hx code`
  subcommands returning bounded JSON. Even MCP-exposed tools in hx go
  through `hx tools call`; if an MCP surface for the graph is ever wanted
  it is a server registered in `config/capabilities.json`, not a second
  query path.
- **Queries end questions; they do not open them.** No browse, no
  list-all (Lesson 2). Every command takes a name or path, caps its rows,
  and returns path, signature, lane, anchor state and, on request,
  owners.
- **Enforcement is an exit code.** `--expect` on every query (Lesson 3);
  the dispatch gate in `hx plan validate`; goal templates carry the lint
  lines. Prompt wording only redirects.
- **Ownership is read-through, never stored** (Lesson 4). The graph joins
  the ledger's `leases`/`runs` tables at query time; there is no owner
  column anywhere in `index.db`.
- **External indexers are orchestrated, never vendored.** hx stays
  zero-dependency Python; tree-sitter, universal-ctags, SCIP indexers and
  the `scip` CLI are operator-installed binaries detected at index time,
  prerequisite by prerequisite, with the detected set reported by
  `hx code stats`. This matches hx's existing adapter treatment of
  `claude`, `codex`, `muse`.
- **Every row names its provenance.** `lane`, `extractor`, `anchor_sha`
  and `stale` on every symbol, reference and edge row; an answer never
  merges a resolved fact with a syntactic guess or an imported claim
  without saying which is which.
- **Signatures are source text.** The declaration header from the
  symbol's first line to its body opener (`{`, `:`, `=>` or `;`),
  whitespace-normalised, stored verbatim; a ctags `signature` or SCIP
  `signature_documentation` replaces it when present.
- **Syntax before semantics.** No build, no type-check, no dataflow in
  the first phases; LSP-backed resolution rejected (a long-running server
  per language per worktree); SCIP is batch and runs once per merge.
  Interprocedural analysis is an explicit non-goal until a measured need
  exists.
- **Freshness is a gate, not a habit** (Lesson 6). Anchor hash → stale →
  invalidate consumers → dispatch refuses stale scope; post-merge rebuild
  plus completion-time incremental index of the changed paths the ledger
  already knows.
- **Derived facts are the product; promotion requires anchored validation** (Lesson 5).
  Regenerated tables are always current; candidate promotion through the
  companion path is kept and counted, never relied on.
- **Decomposition is proposed, never asserted; ranking is deterministic.**
  PageRank and Louvain run in fixed node order with a fixed seed so two
  runs on one tree give one answer.
- **One shared graph, no humans in the loop.** All Partners in an
  instance use the same store; nothing here waits on a person. Candidate
  rows are promoted by the existing machine path — companion proposal,
  deterministic validation (anchors, signatures, checks), and a second
  agent session's acceptance where the map already requires one. Human
  review is an audit of receipts after the fact, never a gate.
- **No index files in the product repository** (Lesson 11). Graft-style
  markdown graphs and Graphify's `graph.json`/`graph.html` are rejected;
  the clean-repository rule (docs/multi-instance.md §5) applies to
  derived graphs exactly as it applies to `.hx/map`.

## Steps

1. **Schema, scale and language extractors (syntax lane).** Extend
   `src/hx/code_index.py`: a `repos` table (namespace, root path, head
   SHA, dirty flag, indexed_at) and a `repo` column on every table — today
   a second `--repo` deletes the first repo's rows as "gone"; the
   namespace is the `.hx/map/manifest.json` `repo_id` when present, else
   the first git remote URL, else the resolved path. File discovery runs
   through `git ls-files -z` (os.walk fallback for non-git trees) with the
   `max_files=2000` cap removed and `MAX_FILE_BYTES` configurable; each
   body is stored once with both FTS5 tables as external-content tables
   over it; a trigram-availability check degrades to Porter-only on an old
   SQLite instead of failing the schema. Symbol rows gain `signature`,
   `lane`, `extractor`, `anchor_sha` (sha256 of the declaration span),
   `stale`, `name_folded` (indexed, Lesson 8) and `attributes` (JSON list
   of decorators/attributes); a `refs` table (repo, name, qualified guess,
   path, line, kind) holds syntax-lane references from tags or ctags;
   `projects` and `project_edges` hold manifests. Per-language extractor
   registry keyed by extension with the precedence above; the tree-sitter
   and ctags adapters are subprocess wrappers with bounded output and
   pinned-version fixtures, because the tags text format is undocumented.
   The C# extractor adds DI registrations
   (`Add{Scoped,Singleton,Transient}<I,T>()` and the string-named forms)
   and base-list `implements` candidates (`class Foo : IFoo`). Validation:
   extend `tests/core/test_code_index.py` with one fixture repo per
   language asserting signature-exact lookup, nested qualified names,
   attribute capture, case-folded hits and two-repo namespace isolation;
   adapter tests run only when the binary is detected and report skipped
   otherwise. Focused command: `pytest tests/core/test_code_index.py -q`.
2. **Bounded queries, lint mode, parity and precedent.** New `hx code`
   subcommands, each capped and each accepting `--expect
   exists|absent|one|clean|fresh` (exit 3 on a failed expectation) and
   `--owners` (lease join):
   - `type NAME`, `signature NAME` (case-folded, lane-ranked);
   - `callers SYMBOL [--count]` — every hit labelled `resolved` or
     `syntactic`; `--count` returns fan-in only (Lesson 10);
   - `register PATTERN` — DI/registration sites;
   - `tests-for PATH|SYMBOL` — test classes and functions by attribute,
     naming convention and reference;
   - `conform INTERFACE` — implementations (resolved or base-list) and
     referencing tests, with members missing from an implementation and
     signature mismatches after whitespace and parameter-name
     normalisation listed as a diff; `--expect clean` is the S54 audit as
     a lint (Lesson 7);
   - `precedent PATTERN [--language L] [--sift QUESTION]` — exact snippets
     of an existing usage from the text lane, Jev-sifted on request, never
     summarised (Lesson 7);
   - `projects [NAME]` — manifest graph;
   - `map --budget BYTES [--focus PATH...]` — personalised PageRank repo
     map with enclosing-scope headers and fan-in counts, for orientation
     before the first edit;
   - `stats` — detected extractors, coverage per language, index size,
     indexed head SHA per namespace.
   Stale rows return with `stale: true` rather than being omitted.
   Validation: CLI smoke on the hx repo itself plus fixture tests; an
   `--expect` test per mode asserting the exit code.
3. **Resolved lane.** Detect the named binaries per language, run them on
   the indexer checkout, import through `scip print --json` with
   `lane='resolved'`; search ranks resolved above syntax above text above
   imported; `implements` relationships become candidate edges.
   Validation: fixture repo indexed with and without the binary; resolved
   rows must be a superset match on the fixture's known references.
   Skipped (not failed) when no binary exists, following the live-suite
   convention.
4. **Map anchors and candidate edges for non-Python code.**
   `appmap.source_anchor` resolves `--symbol` for `.cs`, `.ts`/`.tsx`/
   `.js`/`.jsx`, `.go` and `.rs` through the syntax lane's declaration
   span when the index holds exactly one matching row for that path,
   hashing that span as the Python path does; no row or an ambiguous row
   still fails explicitly, never a silent whole-file downgrade. `hx code
   edges` emits `implements`, `calls` and `tests` candidate edges
   alongside `imports`, each with its lane. Validation:
   `tests/core/test_appmap.py` gains a C# and a TypeScript anchor case;
   `hx map check` on a fixture map with non-Python symbol anchors passes.
5. **External graph import (imported lane).** `hx code import --namespace
   NS [--extractor TOOL] --symbols FILE.jsonl --edges FILE.jsonl` with a
   documented row shape — symbols `{name, qualified, kind, path, line,
   end_line, signature}`, edges `{from, to, kind, evidence}` — stored with
   `lane='imported'`; `anchor_sha` is computed when the path exists in the
   namespace, else the row is marked `unanchored: true` and excluded from
   freshness claims. Free-form edge kinds are preserved (Lesson 9).
   Validation: import a fixture `edges.jsonl` with three custom kinds;
   `callers` and `edges` surface them with provenance; re-import is
   idempotent.
6. **Contract registry, shared and dual-purpose.** `hx code contracts
   [--register FILE]` indexes published contract records (interface
   name, path, version, producer unit, repository namespace, git SHA)
   into the shared store; `hx code contract NAME` answers existence for
   every Partner from that one index, in both the source and target
   namespaces, so conversion progress ("does the target counterpart
   exist yet?") is the same lookup as reuse, and `--expect absent` is
   the "no second interface" lint. Validation: two fixture contracts with
   the same interface name in different namespaces must both surface with
   producers and SHAs.
7. **Freshness gate and completion-time refresh.** Record the indexed
   head SHA per repo namespace; `hx plan validate` gains a hard refusal
   when the plan's scope index is stale — `git merge-base --is-ancestor
   INDEXED_HEAD HEAD` fails, `git diff --name-only INDEXED_HEAD HEAD --
   WRITE_PATHS` is non-empty, or stale rows exist in scope — with the
   rebuild command named in the refusal. Completion publication runs
   `hx code index --paths CHANGED` for the changed paths the ledger
   already knows, so the target graph stays current per completion, not
   only per merge. Companion map patches reuse the same invalidation.
   Validation: planning tests with a moved base; existing planning suite
   must stay green.
8. **Decomposition proposals.** Louvain modularity over the import/call
   graph with a connected-components refinement, fixed node order and
   seed; `hx code submaps` emits candidate feature submaps (member files,
   boundary edges, coupling score, and the modularity of the directory
   layout as the baseline a proposal must beat) as map-patch-ready JSON.
   Validation: fixture repo with two known clusters must recover both,
   identically on repeated runs; on hx itself, output is reviewed by hand
   once and recorded.
9. **Correctness and bounded-work verification.** Implement directly without an
   A/B rollout or research deliverable. Verify the shipped question set: case-folded
   type/signature, callers, tests, parity, precedent, contract existence across
   namespaces and current ownership. Fixtures cover supported languages including
   Natural, historical snapshots, stale/uncertain facts, parallel cold requests,
   changed-path updates, shared extraction and restart/replay. Operation-count
   assertions prove bounded work and deduplication; published performance figures
   are not hx claims.
10. **Rollout, lints and docs.** Update `docs/search-and-retrieval.md`,
    `docs/application-map.md`, worker-bound checks (no second interface,
    test class present, scope fresh), and
    Partner/worker prompts: search the graph (type/signature/contract/
    precedent) before proposing new code, with the same
    redirect-not-block wording as other routing. The Partner writes one paragraph
    describing behavior/dependencies; the harness writes its definition of done
    and checks. Work on the requested current branch, with focused checks followed
    by the configured full parallel suite and installed-package verification.

## Validation Plan

- Per step: the focused test files named above, run green before the
  step's commit; `tests/core/test_appmap.py`, `tests/core/test_planning.py`
  and `tests/core/test_native_completion.py` stay green through the gate.
- Adapter-backed tests (tree-sitter, universal-ctags, SCIP) skip with a
  named reason when the binary is absent and never fail the suite; the
  stdlib extractor's fixtures always run.
- Lint mode: every `--expect` value has a passing and a failing fixture
  asserting exit 0 and exit 3; usage errors stay exit 2.
- Promotion remains the existing anchored application-map validation path;
  derived candidates do not authorize work or prove absence.
- End to end: index hx itself and one polyglot repo; answer the fixed
  question set of step 9 using only `hx code` output; any question
  requiring a raw grep is a failed criterion and is recorded.
- Regression: the full configured parallel suite before final sign-off,
  per `tools/milestone-check.sh`, with skipped optional adapters reported as skipped.

## Risks / Open Questions

- Indexer availability varies per host; the degradation contract (the
  stdlib syntax lane always works) is what keeps this safe. Tree-sitter's
  CLI additionally needs grammar checkouts and a C compiler, so its
  presence is the exception; universal-ctags is the realistic middle tier
  and is absent from macOS by default (the bundled `ctags` is BSD).
- Resolved C# is a restore (NuGet access, MSBuild), which a conversion
  fleet already performs in its build checks; it runs only in the
  post-merge indexer job and failure degrades to syntax rows.
- Index size at 3–5M lines: the trigram FTS table is several times the
  source size. Bounded extraction/query fixtures verify the implementation,
  and a Porter-only mode exists if the budget demands it.
- `conform` compares signatures textually after normalisation; overloads,
  generic constraints and default values can produce false mismatches.
  The lint reports them as `uncertain`, never as `clean`, and the fixture
  covers one of each.
- Tree-sitter grammar quality varies by language; C# is the proven pain
  point, so its fixture is in step 1, not deferred. The tags output
  format is undocumented and is pinned by a versioned fixture.
- Community detection can propose plausible-but-wrong boundaries;
  candidate-only status, the directory-layout baseline, and machine/
  Partner promotion are the mitigation.
- Fuzzy intent search remains lexical + sift in this implementation.
  Ambiguous results stay explicit; optional episode-memory embeddings are
  separate from the shipped code graph.
- Imported graphs carry whatever their producer got wrong; `lane=
  'imported'` keeps them below resolved and syntax rows in ranking and
  out of freshness guarantees when unanchored.
- Resolved: no cross-instance federation and no new authentication.
  Multiple Partners share the one instance graph; cross-instance graph
  sharing is out of scope for this plan entirely.
