"""The shipped texts, checked against the spec constraints they have to satisfy.

These are prompts and templates: nothing imports them, so nothing else in the suite notices
when one drifts out of the shape the spec (and `hx dispatch`) requires of it.

Constraints checked here, with the section each comes from:

* order-shaped examples: `## Order`, `## Definition of done`, and a non-empty fenced ``bash``
  block under `### Checks` — spec 06, `CONTRACTS.md` "Orders", and what `hx dispatch` refuses
* `templates/work-item.md`: the frontmatter keys and every body section of spec 06
* every `AGENTS.md`: exactly one `## UPDATES BELOW ONLY` — spec 03, 04, guard rule 1
* both `SKILL.md` files: valid frontmatter with `name` and `description` — spec 17.5 and
  code.claude.com/docs/en/skills

The unit templates and the tested-versions list moved into the wheel at
`src/hx/packaging/`; they are covered by `test_units.py`, which renders them first.
"""

import json
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SKELETON = REPO / "src" / "hx" / "skeleton"
SKILLS = REPO / "src" / "hx" / "skills"

# Order-shaped files: everything `hx dispatch` would have to accept. The work-item template
# embeds an order rather than being one, so it is checked separately below.
ORDER_EXAMPLES = [SKELETON / "templates" / "order.md"]

#: Every skill that ships, for the checks that apply to any skill.
SKILL_FILES = [
    SKILLS / "hx-companion" / "SKILL.md",
    SKILLS / "hx-memory" / "SKILL.md",
    SKILLS / "hx-partner" / "SKILL.md",
    SKILLS / "hx-setup" / "SKILL.md",
    SKILLS / "hx-worker" / "SKILL.md",
]

#: The skills a *HarnessAgent* loads. The Companion is not one: it has no `/goal`, takes no
#: seams, and never sees spec 09.1's context line — so the boundary-read checks below are
#: about these two and would be meaningless against hx-companion.
AGENT_SKILL_FILES = [
    SKILLS / "hx-partner" / "SKILL.md",
    SKILLS / "hx-worker" / "SKILL.md",
]

CONTRACTS = REPO / "CONTRACTS.md"

SPEC_HOOKS = REPO / "spec" / "09-hooks.md"

MUTABLE_HEADER = "## UPDATES BELOW ONLY"


def _ids(paths):
    return [str(p.relative_to(REPO)) for p in paths]


# --------------------------------------------------------------------------- orders


def _checks_bash_blocks(text: str) -> list[str]:
    """Fenced ```bash blocks that appear under a `### Checks` heading."""
    after = text.split("### Checks", 1)
    if len(after) == 1:
        return []
    # Stop at the next heading of the same or higher level, so a later section's bash
    # block cannot stand in for a missing one here.
    tail = re.split(r"^#{1,3} ", after[1], maxsplit=1, flags=re.MULTILINE)[0]
    return re.findall(r"^```bash\n(.*?)^```", tail, flags=re.MULTILINE | re.DOTALL)


@pytest.mark.parametrize("path", ORDER_EXAMPLES, ids=_ids(ORDER_EXAMPLES))
def test_order_example_has_the_sections_dispatch_requires(path):
    text = path.read_text()
    assert re.search(r"^## Order$", text, re.MULTILINE), f"{path}: no `## Order` section"
    assert re.search(r"^## Definition of done$", text, re.MULTILINE), (
        f"{path}: no `## Definition of done` section"
    )
    assert re.search(r"^### Checks$", text, re.MULTILINE), f"{path}: no `### Checks` heading"


@pytest.mark.parametrize("path", ORDER_EXAMPLES, ids=_ids(ORDER_EXAMPLES))
def test_order_example_has_a_non_empty_checks_bash_block(path):
    blocks = _checks_bash_blocks(path.read_text())
    assert blocks, f"{path}: no fenced ```bash block under `### Checks`"
    commands = [
        line for b in blocks for line in b.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert commands, f"{path}: the `### Checks` bash block has no commands"


@pytest.mark.parametrize("path", ORDER_EXAMPLES, ids=_ids(ORDER_EXAMPLES))
def test_order_example_has_only_the_two_top_level_sections(path):
    # Spec 06: "contains exactly two sections, `## Order` and `## Definition of done`".
    headings = re.findall(r"^## (.+)$", path.read_text(), re.MULTILINE)
    assert headings == ["Order", "Definition of done"], f"{path}: {headings}"


def test_order_example_has_no_frontmatter():
    """Spec 14 D25 cut `after` and with it the only reason an order file had frontmatter. An
    example that still carried some would teach the Partner to write a field nothing reads."""
    text = (SKELETON / "templates" / "order.md").read_text()
    assert not text.startswith("---"), "templates/order.md still opens with frontmatter"
    assert "after:" not in text, "templates/order.md still names `after`"


def test_addendum_example_is_prose_that_lands_under_the_order_heading():
    # `hx resume` appends the file verbatim beneath `## Order` as `## Order addendum <ts>`
    # (spec 06, 08). A `##` heading in the addendum would break the work item's structure.
    text = (SKELETON / "templates" / "addendum.md").read_text()
    assert text.strip(), "the addendum example is empty"
    assert not re.search(r"^## ", text, re.MULTILINE), (
        "the addendum must not introduce `##` headings: it is appended under `## Order`"
    )
    assert not text.startswith("---\n"), "an addendum carries no frontmatter"


# ----------------------------------------------------------------- work-item template


def test_work_item_template_frontmatter_keys():
    text = (SKELETON / "templates" / "work-item.md").read_text()
    assert text.startswith("---\n"), "the template must open with frontmatter"
    front = text.split("---\n", 2)[1]
    assert "after:" not in front, "the work item still carries `after` (cut, spec 14 D25)"
    for key in ("id:", "pod:", "outcome:", "dispatched:"):
        assert re.search(rf"^{re.escape(key)}", front, re.MULTILINE), (
            f"work-item.md frontmatter is missing `{key}`"
        )


def test_work_item_template_has_every_section_spec_06_names():
    text = (SKELETON / "templates" / "work-item.md").read_text()
    expected = [
        "Standing instructions",
        "Tasks",
        "Deliverables",
        "Commands",
        "Open decision",
        "Digest",
    ]
    headings = re.findall(r"^## (.+)$", text, re.MULTILINE)
    assert headings == expected, f"work-item.md sections {headings} != {expected}"


def test_work_item_template_standing_instructions_are_the_spec_text():
    """Spec 06 / decision D9 fixes these bullets; the wording is the agent's whole briefing."""
    text = (SKELETON / "templates" / "work-item.md").read_text()
    for fragment in (
        "Keep `## Tasks` current",
        "Commit each finished sub-task immediately",
        "Read a file once",
        "Use subagents freely",
        "`## UPDATES BELOW ONLY`",
        "`hx complete done` runs `### Checks`",
        "Your last action is `hx complete <outcome>`",
    ):
        assert fragment in text, f"work-item.md standing instructions lost: {fragment!r}"


def test_work_item_template_placeholders_are_rendered_by_dispatch():
    text = (SKELETON / "templates" / "work-item.md").read_text()
    found = set(re.findall(r"\{\{(\w+)\}\}", text))
    assert found == {"id", "pod", "dispatched", "order"}, found


# ----------------------------------------------------------------------- AGENTS.md


def agents_files() -> list[pathlib.Path]:
    return sorted(SKELETON.rglob("AGENTS.md"))


def test_the_skeleton_ships_agents_files():
    found = agents_files()
    assert found, "no AGENTS.md in the skeleton"
    names = {p.parent.name for p in found}
    assert "partner" in names, f"no config/partner/AGENTS.md; found {sorted(names)}"


@pytest.mark.parametrize("path", agents_files(), ids=_ids(agents_files()))
def test_agents_file_has_exactly_one_mutable_header(path):
    lines = path.read_text().splitlines()
    hits = [n for n, line in enumerate(lines, 1) if line.strip() == MUTABLE_HEADER]
    assert len(hits) == 1, f"{path}: {len(hits)} `{MUTABLE_HEADER}` lines, expected exactly 1"


@pytest.mark.parametrize("path", agents_files(), ids=_ids(agents_files()))
def test_agents_file_has_a_persona_above_and_room_below(path):
    """Above the header is the persona `start.sh` copies to run/<id>/persona.md (spec 11);
    below it is the agent's own memory, which travels in the context file (spec 03)."""
    text = path.read_text()
    above, header, _ = text.partition(MUTABLE_HEADER)
    assert header, f"{path}: no `{MUTABLE_HEADER}` line"
    assert above.strip(), f"{path}: nothing above the header, so persona.md would be empty"
    # Below the header may be empty: it is the agent's own memory, and a freshly installed
    # persona has none yet. What matters is that the header is there to divide the two.


def test_every_shipped_persona_has_exactly_one_mutable_header():
    """`personas/<role>/AGENTS.md` are the texts the Partner copies over `config/<id>/AGENTS.md`
    when it creates a worker. `start.sh` refuses an AGENTS.md with no header, so a persona
    missing one would be a worker that cannot launch."""
    personas = sorted((SKELETON / "personas").iterdir())
    assert [p.name for p in personas] == [
        "backend-engineer", "frontend-engineer", "partner", "release-engineer",
    ], [p.name for p in personas]
    for directory in personas:
        path = directory / "AGENTS.md"
        assert path.is_file(), f"{directory.name} has no AGENTS.md"
        lines = path.read_text().splitlines()
        hits = [n for n, line in enumerate(lines, 1) if line.strip() == MUTABLE_HEADER]
        assert len(hits) == 1, f"{path}: {len(hits)} `{MUTABLE_HEADER}` lines, expected 1"


def test_the_installed_partner_persona_is_the_shipped_one():
    """`config/partner/AGENTS.md` is what the Partner actually launches with; it must not drift
    from `personas/partner/AGENTS.md`, which is the text under review."""
    installed = (SKELETON / "config" / "partner" / "AGENTS.md").read_text()
    source = (SKELETON / "personas" / "partner" / "AGENTS.md").read_text()
    assert installed == source, (
        "config/partner/AGENTS.md differs from personas/partner/AGENTS.md; copy the persona"
    )


def test_a_fresh_instance_installs_only_the_partner():
    """CONTRACTS.md "Fresh instance contents" / spec 17.2 step 2: `hx install` creates
    config/partner/ and no worker. The example worker ships as templates/worker/ for the
    Partner to copy, so `hx doctor` and `hx board` see only `partner` in a fresh root."""
    id_dirs = sorted(p.name for p in (SKELETON / "config").iterdir() if p.is_dir())
    assert id_dirs == ["partner"], f"config/ installs worker ids: {id_dirs}"


def test_every_config_id_dir_has_agents_subagents_and_harness_json():
    config = SKELETON / "config"
    id_dirs = sorted(p for p in config.iterdir() if p.is_dir())
    assert id_dirs, "no config/<id>/ directories in the skeleton"
    for d in id_dirs:
        for name in ("AGENTS.md", "SUBAGENTS.md", "harness.json"):
            assert (d / name).is_file(), f"{d.relative_to(REPO)}: missing {name}"


def test_worker_template_ships_all_three_identity_files():
    worker = SKELETON / "templates" / "worker"
    assert sorted(p.name for p in worker.iterdir()) == [
        "AGENTS.md", "SUBAGENTS.md", "harness.json",
    ], sorted(p.name for p in worker.iterdir())


def test_worker_template_is_templated_on_id_and_pod():
    """The Partner copies this to config/<id>/ and substitutes. A leftover placeholder must
    fail validation loudly rather than install a worker named `{{id}}`."""
    worker = SKELETON / "templates" / "worker"
    for name in ("AGENTS.md", "SUBAGENTS.md", "harness.json"):
        text = (worker / name).read_text()
        assert "{{id}}" in text, f"templates/worker/{name}: nothing templated on the id"
    cfg = json.loads((worker / "harness.json").read_text())
    assert cfg["id"] == "{{id}}" and cfg["pod"] == "{{pod}}", cfg
    # Spec 14 D25: hx manages no git, so there is no branch, and the workdir is an absolute
    # path the Partner chooses — a placeholder here, so an unedited copy fails loudly.
    assert "branch" not in cfg, "hx creates no branch; the template must not name one"
    assert cfg["workdir"] == "{{workdir}}", cfg["workdir"]
    role_file = SKELETON / "companion" / "roles" / f"{cfg['role']}.md"
    assert role_file.is_file(), f"templates/worker: no role file for {cfg['role']}"


def test_hx_partner_skill_tells_the_partner_how_to_create_a_worker():
    text = (SKILLS / "hx-partner" / "SKILL.md").read_text()
    assert "templates/worker/" in text, (
        "the Partner is the only one who creates workers; the skill must name the template"
    )
    assert "hx launch" in text


@pytest.mark.parametrize(
    "path",
    sorted((SKELETON / "config").rglob("harness.json")),
    ids=_ids(sorted((SKELETON / "config").rglob("harness.json"))),
)
def test_harness_json_matches_spec_05(path):
    cfg = json.loads(path.read_text())
    assert cfg["id"] == path.parent.name, f"{path}: id != directory name"
    for key in ("id", "pod", "role", "model", "effort", "companion"):
        assert key in cfg, f"{path}: missing `{key}`"
    assert cfg["effort"] in ("low", "medium", "high", "xhigh", "max"), cfg["effort"]
    # Models are always full ids, never aliases, which drift (spec 08).
    assert "-" in cfg["model"] and not cfg["model"].endswith("-latest"), cfg["model"]
    comp = cfg["companion"]
    for key in (
        "provider", "model", "batch_records", "cache_ttl",
        "state_budget_tokens", "seam_min_context_tokens", "seam_min_interval_s",
    ):
        assert key in comp, f"{path}: companion is missing `{key}`"
    # Spec 05: the Partner has no workdir and no branch; a worker has both.
    if cfg["id"] == "partner":
        assert cfg["pod"] == "partner" and cfg["role"] == "partner", cfg
        assert "workdir" not in cfg and "branch" not in cfg, f"{path}: partner has no worktree"
    else:
        assert cfg["workdir"] and cfg["branch"], f"{path}: a worker needs workdir and branch"
    # Every role must have a companion role file (spec 05 validation).
    role_file = SKELETON / "companion" / "roles" / f"{cfg['role']}.md"
    assert role_file.is_file(), f"{path}: no {role_file.relative_to(REPO)} for role {cfg['role']}"


# -------------------------------------------------------------------------- skills


def parse_frontmatter(text: str) -> dict[str, str]:
    """The subset of YAML a SKILL.md frontmatter uses: `key: value`, one per line.

    Claude Code requires the opening `---` to be the file's very first line, or the whole
    file is treated as content (code.claude.com/docs/en/skills).
    """
    assert text.startswith("---\n"), "frontmatter must start on the file's first line"
    end = text.index("\n---\n", 3)
    body = text[4:end + 1]
    fields: dict[str, str] = {}
    key = None
    for line in body.splitlines():
        if not line.strip():
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            fields[key] = value
        else:
            assert key is not None, f"frontmatter line is not `key: value`: {line!r}"
            fields[key] += " " + line.strip()
    return fields


@pytest.mark.parametrize("path", SKILL_FILES, ids=_ids(SKILL_FILES))
def test_skill_has_valid_frontmatter(path):
    fields = parse_frontmatter(path.read_text())
    assert fields.get("name"), f"{path}: no `name` in frontmatter"
    assert fields.get("description"), f"{path}: no `description` in frontmatter"
    # The name field is the display label; the invocation name is the directory (docs).
    assert fields["name"] == path.parent.name, (
        f"{path}: frontmatter name {fields['name']!r} != directory {path.parent.name!r}"
    )
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", fields["name"]), fields["name"]
    # description + when_to_use are capped at 1536 characters combined (docs).
    combined = len(fields["description"]) + len(fields.get("when_to_use", ""))
    assert combined <= 1536, f"{path}: description is {combined} chars, cap is 1536"


@pytest.mark.parametrize("path", SKILL_FILES, ids=_ids(SKILL_FILES))
def test_skill_body_is_present_and_within_the_recommended_length(path):
    text = path.read_text()
    body = text[text.index("\n---\n", 3) + 5:]
    assert body.strip(), f"{path}: frontmatter but no instructions"
    assert len(body.splitlines()) <= 500, (
        f"{path}: {len(body.splitlines())} lines; the docs recommend keeping SKILL.md "
        f"under 500 lines and moving reference material to sibling files"
    )


def test_the_skills_that_ship_are_the_ones_the_spec_names():
    assert sorted(p.name for p in SKILLS.iterdir()) == [
        "hx-companion", "hx-fleet", "hx-memory", "hx-partner", "hx-setup", "hx-worker",
    ], (
        "spec 17.5 ships hx-worker into a worker home and hx-partner into the Partner's; "
        "spec 10 adds hx-companion for the Companion's home; hx-fleet is the Partner's manual "
        "for making and retiring agents, and is the Partner's alone; hx-memory is how either "
        "kind of HarnessAgent searches the instance's episode store (docs/memory.md); "
        "hx-setup is for the coding agent or harness that installs hx, and is never copied "
        "into an agent home"
    )


def test_the_setup_skill_is_not_installed_into_any_home():
    """It is read by whoever sets hx up, on the human's machine — never by a HarnessAgent."""
    install_sh = (SKELETON / "adapters" / "claude" / "install.sh").read_text()
    assert "hx-setup" not in install_sh


def test_the_setup_skill_keeps_the_token_step_the_humans():
    text = (SKILLS / "hx-setup" / "SKILL.md").read_text()
    for line in ("claude setup-token", "exit code 4", "never run it for them",
                 "Never touch the human's own Claude", "hx install --root"):
        assert line in text, line
    for cut in ("claude -p ", "hx push", "bare mirror"):
        assert cut not in text.replace("Use `claude -p`", ""), cut


FLEET_SKILL = SKILLS / "hx-fleet" / "SKILL.md"


def test_the_fleet_skill_names_only_paths_that_exist():
    """It is the Partner's manual for creating agents, so every path in it is one the Partner
    will actually copy. A path that does not exist is a worker that cannot be made."""
    text = FLEET_SKILL.read_text()
    for relative in (
        "templates/worker", "personas/backend-engineer/AGENTS.md",
        "personas/frontend-engineer/AGENTS.md", "personas/release-engineer/AGENTS.md",
        "companion/roles/backend-engineer.md", "config/CLAUDE.md",
    ):
        assert relative in text, f"hx-fleet does not mention {relative}"
        assert (SKELETON / relative).exists(), f"hx-fleet names {relative}, which does not ship"


def test_the_fleet_skill_roles_match_the_personas_and_the_role_files():
    text = FLEET_SKILL.read_text()
    roles = sorted(p.name for p in (SKELETON / "personas").iterdir() if p.name != "partner")
    for role in roles:
        assert role in text, f"hx-fleet does not name the {role} role"
        assert (SKELETON / "companion" / "roles" / f"{role}.md").is_file()


def test_the_fleet_skill_protects_the_agents_own_memory():
    """Below the header is the worker's memory. Editing it, or copying one worker's into
    another, writes false memories into a running agent — the one irreversible mistake
    available here."""
    text = FLEET_SKILL.read_text()
    assert "## UPDATES BELOW ONLY" in text
    assert "below the header" in text
    assert "memory" in text


# --------------------------------------------------------------- the Companion skill


def companion_skill() -> str:
    return (SKILLS / "hx-companion" / "SKILL.md").read_text()


def companion_pointer() -> str:
    """The fixed line hx pastes, taken from CONTRACTS.md itself.

    Read out of the contract rather than hard-coded, for the same reason the boundary line is
    read out of spec 09.1: when it is reworded, this fails and the skill gets updated, instead
    of the skill quoting a line the Companion will never see.
    """
    text = CONTRACTS.read_text()
    match = re.search(r"`(Companion pass: read [^`]+)`", text)
    assert match, "CONTRACTS.md no longer carries the Companion pass pointer"
    return match.group(1)


def test_contracts_still_defines_the_companion_pointer():
    pointer = companion_pointer()
    assert pointer.startswith("Companion pass: read "), pointer
    assert "do what it says" in pointer, pointer


def test_the_companion_skill_quotes_the_pointer_verbatim():
    assert companion_pointer() in companion_skill(), (
        "hx-companion does not quote CONTRACTS.md's pass pointer verbatim; the Companion "
        "would be looking for a line it never receives"
    )


def test_the_companion_skill_names_read_and_write_as_its_only_tools():
    text = companion_skill()
    assert "Read and Write" in text, "the skill does not name its two tools together"
    # The tools it must refuse are named, not left to inference.
    for forbidden in ("run a command",):
        assert forbidden in text, f"the skill does not say it cannot {forbidden}"
    assert "Bash" not in text, (
        "hx-companion must not mention Bash as something it might use; its home denies it"
    )


def test_the_companion_skill_covers_the_pass_file_fields():
    text = companion_skill()
    for field in ("stream:", "state:", "log:", "from_seq:", "write:", "retry_reason:",
                  "context_tokens:", "last_seam_ts:", "open_subagents:"):
        assert field in text, f"hx-companion does not mention the pass field {field!r}"


def test_the_companion_skill_forbids_reading_the_agents_files():
    text = companion_skill()
    for path in ("pods/", "orders/", "tasks.json", "worktree"):
        assert path in text, f"hx-companion does not say it must not read {path}"


def test_the_companion_skill_explains_retry_reason():
    text = companion_skill()
    assert "retry_reason" in text
    assert "one retry" in text.lower(), (
        "the skill does not say the retry happens once; a Companion that expects a loop will "
        "not treat the first failure as expensive"
    )


def test_base_md_sends_the_object_to_the_write_path_not_to_stdout():
    """The Companion is a tmux session now: its answer is a file written with the Write tool,
    not something printed. A BASE.md still describing stdout would be describing the old
    headless design."""
    text = (SKELETON / "companion" / "BASE.md").read_text()
    assert "Write tool" in text, "companion/BASE.md does not say the object is written"
    assert "`write:` path" in text, "companion/BASE.md does not name the pass's write path"
    assert "stdin" not in text and "stdout" not in text, (
        "companion/BASE.md still describes the headless call: the Companion reads a pass file "
        "and writes a file (CONTRACTS.md 'The Companion is a tmux session')"
    )
    for field in ("context_tokens", "last_seam_ts", "open_subagents"):
        assert field in text, f"BASE.md's seam policy does not read {field} from the pass"


# ---------------------------------------------------------------------- companion


def test_companion_prompts_exist_for_every_role_that_ships():
    base = SKELETON / "companion" / "BASE.md"
    assert base.is_file() and base.read_text().strip(), "companion/BASE.md is missing or empty"
    roles = sorted(p.stem for p in (SKELETON / "companion" / "roles").glob("*.md"))
    personas = sorted(p.name for p in (SKELETON / "personas").iterdir())
    assert roles == personas, (
        f"every shipped persona needs a companion role file of the same name: "
        f"roles {roles}, personas {personas}"
    )


def test_companion_base_carries_the_step_state_schema_keys():
    """The Companion returns this object and nothing else; hx validates it (spec 07.2)."""
    text = (SKELETON / "companion" / "BASE.md").read_text()
    for key in (
        "seq", "prompt_version", "goal", "constraints", "decisions", "open_steps",
        "closed_steps", "dead_ends", "working_set", "blockers", "subagents_open",
    ):
        assert f'"{key}"' in text, f"companion/BASE.md does not name the `{key}` field"


def test_companion_base_states_the_seam_policy_conditions():
    text = (SKELETON / "companion" / "BASE.md").read_text()
    for fragment in ("seam_min_context_tokens", "seam_min_interval_s", "subagents_open"):
        assert fragment in text, f"companion/BASE.md seam policy is missing {fragment!r}"
    assert "run/<id>/seam" in text, "companion/BASE.md does not name the seam marker path"


def test_companion_base_puts_the_blocker_or_question_first_in_a_digest():
    text = (SKELETON / "companion" / "BASE.md").read_text()
    assert "blocker first" in text and "question first" in text, (
        "spec 10: for `blocked` and `decision` the digest states the blocker or the question "
        "first, so the Partner's addendum can answer it"
    )


# ------------------------------------------------------------------- global CLAUDE.md


def test_there_is_exactly_one_claude_md_in_the_skeleton():
    found = sorted(SKELETON.rglob("CLAUDE.md"))
    assert [p.relative_to(SKELETON).as_posix() for p in found] == ["config/CLAUDE.md"], found


def test_global_claude_md_covers_what_spec_17_5_requires_of_it():
    text = (SKELETON / "config" / "CLAUDE.md").read_text()
    for fragment in ("/goal", "work item", "hx complete"):
        assert fragment in text, f"config/CLAUDE.md does not mention {fragment!r}"


def test_global_claude_md_names_the_read_tool_and_forbids_cat():
    """A live run (build-3, Claude Code 2.1.278) had an agent `Bash cat` the context file and
    then `Read` it. "Read" as an English verb is satisfied by `cat`; naming the tool is what
    makes the difference, and a `Bash` read spends the tokens without counting toward the M7
    metric. This paragraph is what has to say so."""
    text = (SKELETON / "config" / "CLAUDE.md").read_text()
    boundary = next(
        (para for para in text.split("\n\n") if "boundary" in para and "Read" in para), ""
    )
    assert boundary, "config/CLAUDE.md has no paragraph about the boundary read"
    assert "Read tool" in boundary, (
        "the boundary paragraph must name the Read *tool*, not just the action: "
        f"{boundary!r}"
    )
    assert "`cat`" in boundary, "the boundary paragraph must forbid `cat` by name"
    for forbidden in ("once", "before anything else"):
        assert forbidden in boundary, f"the boundary paragraph does not say {forbidden!r}"
    assert "working set" in text, (
        "config/CLAUDE.md must say that files the working set already covers are not re-read"
    )


def test_global_claude_md_stays_short():
    """It is loaded into every turn of every agent, so length is a running cost."""
    lines = (SKELETON / "config" / "CLAUDE.md").read_text().splitlines()
    assert len(lines) <= 60, f"config/CLAUDE.md is {len(lines)} lines; keep it short"


def test_partner_md_ships_as_the_partners_initial_state_doc():
    text = (SKELETON / "PARTNER.md").read_text()
    assert text.strip(), "PARTNER.md is empty"
    assert "## Open questions for the human" in text, text[:200]


# ----------------------------------------------------------- the boundary hook line


def spec_09_1_context_line() -> str:
    """The stdout line the `context` hook prints, taken from spec 09.1 itself.

    Reading it out of the spec rather than hard-coding it is the point: when the orchestrator
    rewords that line, these tests fail and the skills get updated, instead of quietly
    quoting something the hook no longer prints.
    """
    row = next(
        line for line in SPEC_HOOKS.read_text().splitlines()
        if line.startswith("| `context` |")
    )
    match = re.search(r"print one line to stdout: `([^`]+)`", row)
    assert match, f"spec 09.1's `context` row no longer names the stdout line:\n{row}"
    return match.group(1)


def test_spec_09_1_still_names_a_context_line():
    line = spec_09_1_context_line()
    assert "Read tool" in line, (
        f"spec 09.1's hook line no longer names the Read tool: {line!r}. If that is "
        "deliberate, the skills and config/CLAUDE.md need the same change."
    )


@pytest.mark.parametrize("path", AGENT_SKILL_FILES, ids=_ids(AGENT_SKILL_FILES))
def test_skill_quotes_the_hook_line_verbatim(path):
    """Both skills show the agent the line it will actually see. A paraphrase here is worse
    than nothing: the agent would be looking for text that never appears."""
    assert spec_09_1_context_line() in path.read_text(), (
        f"{path} does not quote spec 09.1's context line verbatim"
    )


@pytest.mark.parametrize("path", AGENT_SKILL_FILES, ids=_ids(AGENT_SKILL_FILES))
def test_skill_boundary_step_names_the_read_tool_and_forbids_cat(path):
    text = path.read_text()
    assert "Use the Read tool" in text, f"{path}: the boundary step does not name the Read tool"
    assert "`cat`" in text, f"{path}: the boundary step does not forbid `cat` by name"


def test_companion_base_records_a_bash_read_as_waste():
    """spec 07.4 / 13 M7 count Read-tool calls, so a `Bash cat` is invisible to the metric
    unless the Companion writes it down."""
    text = (SKELETON / "companion" / "BASE.md").read_text()
    assert "Read-tool calls" in text, "companion/BASE.md does not say the metric counts the tool"
    assert "dead_ends" in text.split("Reads, and reads that do not count")[-1], (
        "companion/BASE.md does not say where a Bash read of a tracked file is recorded"
    )
    for fragment in ("`cat`", "context file"):
        assert fragment in text.split("Reads, and reads that do not count")[-1], fragment
