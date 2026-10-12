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

`hx code index --repo PATH` builds a standalone SQLite index at
`state/code-index/index.db`: per-file hashes for incremental reindexing,
symbol definitions (Python qualified names from the AST; best-effort
definitions for TypeScript, JavaScript, Rust, and Go), import edges, and two
FTS5 tables over source text — a Porter table and a trigram table.

```sh
hx code index --repo /path/to/repo
hx code search "useEffect cleanup"
hx code symbols login
hx code impact src/auth.py
hx code edges
```

Search merges exact symbol matches with both FTS rankings via reciprocal
rank fusion (k=60), reranks multi-term queries by term proximity, and falls
back to Levenshtein correction over symbol names when a query finds nothing.
`impact` reports the symbols a changed file defines, the files that mention
them, and candidate test files. `edges` emits candidate import edges
(`status: candidate`) for the application map; only the existing map
validation path can promote an edge to validated.

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
