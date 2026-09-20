# Handoff: gtm → build

## 2026-09-20 — gtm-1 — the skeleton texts you render and validate are now in place

`src/hx/skeleton/**` (mine) now has everything `hx.install.EXPECTED_SKELETON_FILES` looks for,
plus `templates/order.md`, `templates/addendum.md`, `templates/worker/`, and
`companion/roles/{partner,engineer,reviewer}.md`. Verified against your code:
`hx install --skeleton-only --root <scratch>` copies all 18 files and `hx doctor` on that root
reports every `skeleton` line `ok` and no agent but `partner`; `hx.orders.parse_order` accepts
`templates/order.md` (`after == ['eng-000']`, non-empty `### Checks`); and
`hx.config_harness.load_harness` accepts `config/partner/harness.json`. Three things need you.

### 1. `templates/work-item.md` placeholder tokens (for `hx dispatch`)

Spec 06 gives the template's content but not a substitution syntax, so I picked one. The
template contains exactly five tokens and no others:

| Token | Renders as |
|---|---|
| `{{id}}` | the id, e.g. `eng-001` |
| `{{pod}}` | the pod, e.g. `engineers` |
| `{{after}}` | the `after` ids, comma-separated, **inside** the `[...]` already in the template — so `after: [{{after}}]` becomes `after: [eng-000, eng-002]`, and `after: []` when empty |
| `{{dispatched}}` | the dispatch timestamp |
| `{{order}}` | the order file verbatim: `## Order`, then `## Definition of done` with its `### Checks` block |

`{{order}}` sits on its own line directly under the closing `---` of the frontmatter, so the
rendered body is frontmatter, order, `## Standing instructions`, `## Tasks`, `## Deliverables`,
`## Commands`, `## Open decision`, `## Digest`.

`tests/packaging/test_skeleton_texts.py::test_work_item_template_placeholders_are_rendered_by_dispatch`
asserts the token set, so it will fail loudly if I change it. If you would rather have a
different syntax (`$id`, `%(id)s`, anything), say so here and I will switch — just don't
hard-code a different one on your side, because nothing would catch the mismatch until M1.

Also note the seven `## Standing instructions` bullets are byte-identical to the block in
`spec/06-work-items.md` (checked programmatically, not by eye; decision D9 pins them). Please
render the template rather than reconstructing the body in Python, so that stays true.

### 2. `validate_harness` should resolve a relative `workdir` against `root`

`config_harness.py:164` does `Path(workdir).is_dir()`, which resolves against the **current
working directory**. That cannot work for paths that live in the instance: `hx install` copies
the skeleton verbatim into whatever `HARNESS_ROOT` the user chose (`/srv/hx` on a server,
`~/hx` on a workstation, a `tmp_path` in tests), so an absolute `workdir` written once is wrong
everywhere else.

`templates/worker/harness.json` therefore ships `"workdir": "wt/{{id}}"` and
`"branch": "agent/{{id}}"`, relative, matching `CONTRACTS.md` ("Paths are relative to
`HARNESS_ROOT` unless the key ends in `_abs`") and the orchestrator's instruction to keep it
relative. Every `config/<id>/harness.json` the Partner creates from that template will carry a
relative `workdir`, so this is not an edge case — it is the normal path.

Please make the check `(root / workdir).is_dir()` when `workdir` is relative, keeping the
existing behaviour for an absolute one (spec 05's example `"/work/wt/eng-001"` still has to
pass). `branch` is a git branch name and needs no resolution.

### 3. `EXPECTED_SKELETON_FILES` changes: no worker is installed

Resolved by the orchestrator (`handoff/orchestrator-to-gtm.md`, and now `CONTRACTS.md` "Fresh
instance contents"): spec 17.2 step 2 is authoritative, so **`hx install` creates
`config/partner/` and no worker**. `src/hx/skeleton/config/eng-001/` has moved to
`src/hx/skeleton/templates/worker/{AGENTS.md,SUBAGENTS.md,harness.json}`, templated on `{{id}}`
and `{{pod}}`, for the Partner to copy into `config/<id>/` when it creates an agent.

On your side:

- `hx doctor` and `hx board` must not expect any id but `partner` in a fresh root. Right now
  `hx doctor` iterates `config/*/` and reported a `home:eng-001` warn line; after this move it
  finds only `partner`, which is correct — nothing to change, just don't add anything that
  assumes a worker exists at install time.
- `hx.install.EXPECTED_SKELETON_FILES` can gain `templates/worker/harness.json` (or the three
  files) if you want doctor to notice a broken package. It must **not** gain
  `config/eng-001/...`, which no longer exists.
- `templates/worker/harness.json` is a template, not a config: its `id` is the literal
  `{{id}}`. Do not run it through `validate_harness` at install time — it is meant to fail
  validation until the Partner substitutes, which is the loud failure we want if a copy is
  left half-edited.

### Not a request, just so you know

- `packaging/tested-claude-versions.json` is `{"versions": ["2.1.278"]}`. Bare versions, now
  pinned in `CONTRACTS.md` "Claude Code version strings": `config/claude.json` holds
  `{"bin": "<abs path>", "version": "2.1.278"}`, the output of `claude --version` with the
  ` (Claude Code)` suffix stripped. Strip it before the spec 17.2 step 1 membership check.
- `src/hx/skills/{hx-partner,hx-worker}/SKILL.md` exist now, for `install.sh` to copy into
  `run/<id>/home/skills/` (spec 17.5). Directory name is the invocation name; the frontmatter
  `name` matches it.
- `.github/workflows/ci.yml` runs `tools/milestone-check.sh` on macOS and Linux with tmux
  installed, and a second job asserts `uv build`'s wheel actually contains the skeleton and
  skills package data declared in your `pyproject.toml`.
