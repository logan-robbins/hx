"""Spec author's rule (spec 02 "Model calls"): every model call in hx is a Claude Code session in
tmux operated by pasting. No headless `claude -p`, no `--print`, no API client in src/."""
import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "hx"
PAT = re.compile(r"""--output-format|["']--json-schema["']|["']--print["']|anthropic\.com/v1|import anthropic""")
DASH_P = re.compile(r"""["']-p["']""")
TMUX_OR_PS = re.compile(r"""capture-pane|["']ps["']|tmux""")


def test_no_headless_claude_calls_in_src():
    hits = []
    for p in SRC.rglob("*"):
        if p.suffix in {".py", ".sh"} and p.is_file():
            for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                if PAT.search(line) or (DASH_P.search(line) and not TMUX_OR_PS.search(line)):
                    hits.append(f"{p.relative_to(SRC.parent.parent)}:{n}: {line.strip()}")
    assert not hits, "headless model calls are forbidden (spec 02 Model calls):\n" + "\n".join(hits)
