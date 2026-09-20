"""The shipped texts, checked against the spec constraints they have to satisfy.

These are prompts, templates, and unit files: nothing imports them, so nothing else in the
suite notices when one drifts out of the shape the spec (and `hx dispatch`, and `launchctl`,
and `systemd`) requires of it.

Constraints checked here, with the section each comes from:

* order-shaped examples: `## Order`, `## Definition of done`, and a non-empty fenced ``bash``
  block under `### Checks` — spec 06, `CONTRACTS.md` "Orders", and what `hx dispatch` refuses
* `templates/work-item.md`: the frontmatter keys and every body section of spec 06
* every `AGENTS.md`: exactly one `## UPDATES BELOW ONLY` — spec 03, 04, guard rule 1
* both `SKILL.md` files: valid frontmatter with `name` and `description` — spec 17.5 and
  code.claude.com/docs/en/skills
* `packaging/launchd/*.plist`: parse as plists (and pass `plutil -lint` where it exists)
* `packaging/systemd/*`: parse as INI with the sections systemd requires
* `packaging/tested-claude-versions.json`: parses, and is a non-empty list of versions —
  spec 17.2 step 1, 17.6
"""

import configparser
import json
import pathlib
import plistlib
import re
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SKELETON = REPO / "src" / "hx" / "skeleton"
SKILLS = REPO / "src" / "hx" / "skills"
PACKAGING = REPO / "packaging"

# Order-shaped files: everything `hx dispatch` would have to accept. The work-item template
# embeds an order rather than being one, so it is checked separately below.
ORDER_EXAMPLES = [SKELETON / "templates" / "order.md"]

SKILL_FILES = [
    SKILLS / "hx-partner" / "SKILL.md",
    SKILLS / "hx-worker" / "SKILL.md",
]

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


def test_order_example_frontmatter_after_is_a_list_of_ids():
    text = (SKELETON / "templates" / "order.md").read_text()
    assert text.startswith("---\n"), "the example is meant to show the optional frontmatter"
    front = text.split("---\n", 2)[1]
    m = re.search(r"^after:\s*\[(.*)\]\s*$", front, re.MULTILINE)
    assert m, f"no `after:` list in the frontmatter: {front!r}"
    for entry in (e.strip() for e in m.group(1).split(",") if e.strip()):
        assert re.fullmatch(r"partner|[a-z]+-[0-9]{3}", entry), f"bad id in `after`: {entry!r}"


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
    for key in ("id:", "pod:", "after:", "outcome:", "dispatched:"):
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
    assert found == {"id", "pod", "after", "dispatched", "order"}, found


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
    above, _, below = path.read_text().partition(MUTABLE_HEADER)
    assert above.strip(), f"{path}: nothing above the header, so persona.md would be empty"
    assert below.strip(), f"{path}: nothing below the header to tell the agent it is theirs"


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
    # Relative, because hx install copies the skeleton verbatim into any HARNESS_ROOT.
    assert cfg["workdir"] == "wt/{{id}}", cfg["workdir"]
    assert cfg["branch"] == "agent/{{id}}", cfg["branch"]
    assert not pathlib.PurePosixPath(cfg["workdir"]).is_absolute()
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


def test_the_two_skills_spec_17_5_names_are_the_ones_that_ship():
    assert sorted(p.name for p in SKILLS.iterdir()) == ["hx-partner", "hx-worker"], (
        "spec 17.5: hx ships exactly hx-partner and hx-worker; autodev's operator and GM "
        "skills are dropped"
    )


# ------------------------------------------------------------------------ packaging


def plists() -> list[pathlib.Path]:
    return sorted((PACKAGING / "launchd").glob("*.plist"))


def units() -> list[pathlib.Path]:
    return sorted(
        p for p in (PACKAGING / "systemd").iterdir()
        if p.suffix in (".service", ".timer")
    )


def test_the_packaging_units_spec_17_2_names_all_exist():
    assert [p.name for p in plists()] == ["com.hx.heartbeat.plist", "com.hx.up.plist"]
    assert [p.name for p in units()] == [
        "hx-heartbeat.service", "hx-heartbeat.timer", "hx-up.service",
    ]


@pytest.mark.parametrize("path", plists(), ids=_ids(plists()))
def test_plist_parses(path):
    with path.open("rb") as fh:
        data = plistlib.load(fh)
    assert data["Label"] == path.stem, f"{path}: Label {data['Label']!r} != {path.stem!r}"
    args = data["ProgramArguments"]
    assert args[0] == "@HX_BIN@", f"{path}: first argument must be the templated binary path"
    assert args[1] in ("up", "heartbeat"), args
    assert data["EnvironmentVariables"]["HARNESS_ROOT"] == "@HARNESS_ROOT@"


@pytest.mark.parametrize("path", plists(), ids=_ids(plists()))
def test_plist_passes_plutil_lint_when_available(path):
    plutil = shutil.which("plutil")
    if plutil is None:
        pytest.skip("plutil is macOS-only; plistlib parsing covers the rest")
    r = subprocess.run([plutil, "-lint", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, f"{path}: {r.stdout}{r.stderr}"


def test_the_heartbeat_plist_runs_every_900_seconds():
    with (PACKAGING / "launchd" / "com.hx.heartbeat.plist").open("rb") as fh:
        data = plistlib.load(fh)
    assert data["StartInterval"] == 900, data.get("StartInterval")


def test_the_up_plist_runs_at_load():
    with (PACKAGING / "launchd" / "com.hx.up.plist").open("rb") as fh:
        data = plistlib.load(fh)
    assert data["RunAtLoad"] is True


def read_unit(path: pathlib.Path) -> configparser.ConfigParser:
    """systemd units are INI. `#` starts a comment; duplicate keys are legal, so the parser
    is configured to keep the last one rather than raise."""
    parser = configparser.ConfigParser(
        strict=False, interpolation=None, comment_prefixes=("#", ";"),
    )
    parser.optionxform = str
    parser.read_string(path.read_text(), source=str(path))
    return parser


@pytest.mark.parametrize("path", units(), ids=_ids(units()))
def test_systemd_unit_parses_as_ini_with_a_unit_section(path):
    parser = read_unit(path)
    assert "Unit" in parser, f"{path}: no [Unit] section"
    assert parser["Unit"].get("Description"), f"{path}: [Unit] has no Description"
    expected = {".service": "Service", ".timer": "Timer"}[path.suffix]
    assert expected in parser, f"{path}: no [{expected}] section"
    for section in parser.sections():
        assert section in ("Unit", "Service", "Timer", "Install"), f"{path}: [{section}]"


@pytest.mark.parametrize(
    "path", [p for p in units() if p.suffix == ".service"],
    ids=_ids([p for p in units() if p.suffix == ".service"]),
)
def test_systemd_service_execs_the_templated_binary(path):
    svc = read_unit(path)["Service"]
    assert svc["ExecStart"].startswith("@HX_BIN@ "), svc["ExecStart"]
    assert svc["ExecStart"].split()[1] in ("up", "heartbeat"), svc["ExecStart"]
    assert svc["Environment"] == "HARNESS_ROOT=@HARNESS_ROOT@", svc.get("Environment")
    assert svc["Type"] == "oneshot", svc.get("Type")


def test_systemd_heartbeat_timer_fires_every_900_seconds():
    timer = read_unit(PACKAGING / "systemd" / "hx-heartbeat.timer")["Timer"]
    assert timer["OnUnitActiveSec"] == "900", timer.get("OnUnitActiveSec")
    assert timer["Unit"] == "hx-heartbeat.service", timer.get("Unit")


def test_only_the_up_service_and_the_timer_are_enabled_directly():
    # hx-heartbeat.service is triggered by its timer and must not carry [Install].
    assert "Install" in read_unit(PACKAGING / "systemd" / "hx-up.service")
    assert "Install" in read_unit(PACKAGING / "systemd" / "hx-heartbeat.timer")
    assert "Install" not in read_unit(PACKAGING / "systemd" / "hx-heartbeat.service")


@pytest.mark.parametrize("path", plists() + units(), ids=_ids(plists() + units()))
def test_unit_files_carry_no_absolute_host_paths(path):
    """Both placeholders are substituted by `hx install`; a real path baked in here would
    install a fleet pointed at whoever's machine built the package."""
    text = path.read_text()
    assert "@HARNESS_ROOT@" in text, f"{path}: nothing templated on HARNESS_ROOT"
    for bad in ("/Users/", "/home/", "/srv/hx"):
        assert bad not in text, f"{path}: hard-coded path {bad!r}"


def test_tested_claude_versions_parses_and_is_a_non_empty_version_list():
    data = json.loads((PACKAGING / "tested-claude-versions.json").read_text())
    assert set(data) == {"versions"}, sorted(data)
    versions = data["versions"]
    assert isinstance(versions, list) and versions, "the tested list must not be empty"
    for v in versions:
        assert isinstance(v, str) and re.fullmatch(r"\d+\.\d+\.\d+", v), (
            f"{v!r}: entries are bare version numbers, as `hx install` compares them"
        )
    assert len(set(versions)) == len(versions), "duplicate versions in the tested list"


# ---------------------------------------------------------------------- companion


def test_companion_prompts_exist_for_every_role_that_ships():
    base = SKELETON / "companion" / "BASE.md"
    assert base.is_file() and base.read_text().strip(), "companion/BASE.md is missing or empty"
    roles = sorted(p.stem for p in (SKELETON / "companion" / "roles").glob("*.md"))
    assert roles == ["engineer", "partner", "reviewer"], roles


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
    assert "Read" in text and "first action" in text, (
        "config/CLAUDE.md must say the first action after a boundary is one Read of the "
        "path the hook printed"
    )


def test_partner_md_ships_as_the_partners_initial_state_doc():
    text = (SKELETON / "PARTNER.md").read_text()
    assert text.strip(), "PARTNER.md is empty"
    assert "## Open questions for the human" in text, text[:200]
