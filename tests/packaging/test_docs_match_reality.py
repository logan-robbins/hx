"""Public operating examples name live packet commands and shipped roles."""
from pathlib import Path
import re
from hx import cli
REPO=Path(__file__).resolve().parents[2]


def test_operating_pages_use_current_commands():
    for name in ('getting-started.md','operating.md','deploy.md','planning-runtime.md'):
        text=(REPO/'docs'/name).read_text()
        # Executable examples and inline command spans, not quoted installer prose.
        examples=re.findall(r'```(?:sh|bash|shell)\n(.*?)```',text,re.S)
        inline=re.findall(r'(?<!`)`(hx [^`\n]+)`(?!`)',text)
        names={match for snippet in [*examples,*inline]
               for match in re.findall(r'(?:^|\n)hx\s+([a-z][a-z-]*)',snippet)}
        assert not names-set(cli.COMMANDS),(name,names-set(cli.COMMANDS))


def test_public_workflow_describes_graph_packets_and_admitted_native_launch():
    text=(REPO/'docs'/'operating.md').read_text()
    assert 'hx plan' in text and '--run' in text and '--request' in text
    assert 'hx dispatch' not in text and 'hx resume' not in text
    assert 'hx memory' not in text


def test_install_page_keeps_the_human_token_step():
    text=(REPO/'docs'/'getting-started.md').read_text()
    assert 'hx install' in text and 'seed/token' in text


def test_primary_product_pages_preserve_one_graph_flow_without_size_routing():
    paths=[REPO/'README.md',REPO/'CONTRACTS.md',*(REPO/'docs'/name for name in
        ('system-design.md','planning-runtime.md','agent-scale-runtime.md','operating.md'))]
    for path in paths:
        text=path.read_text().lower()
        for obligation in ('every directive','graph','search','reuse','parallel','packet count'):
            assert obligation in text,(path.name,obligation)
        for stale in ('goal files are the simple path','a small request is a one-packet','version-1 plans','cutover remains open','hx dispatch','tasks.json'):
            assert stale not in text,(path.name,stale)


def test_public_plan_contract_documents_current_worker_owned_checks():
    text=' '.join((REPO/'CONTRACTS.md').read_text().split())
    for field in ('check_policy','required_checks','resources','prerequisites','stand_in','provider_version'):
        assert field in text
    for gate in ('independent reviewer','frozen hash','before edits/proof','exact run-bound receipts','managed completion'):
        assert gate in text
    assert 'Assignment reserves a run; it does not imply native startup' in text
    assert 'Empty write scope requires a' in text and 'current repository graph' in text


def test_public_docs_links_target_real_repository_documents():
    for path in [REPO/'README.md',REPO/'CONTRACTS.md',*(REPO/'docs').glob('*.md')]:
        for url in re.findall(r'\]\(([^)]+)\)',path.read_text()):
            if '://' in url or url.startswith(('#','/')):
                continue
            target=url.split('#',1)[0]
            if target:
                assert (path.parent/target).is_file(),(path.relative_to(REPO),url)
