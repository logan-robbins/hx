import pytest

from hx.file_inspection import require_non_file_output
from hx.errors import ValidationError


@pytest.mark.parametrize('argv', [
    ['cat', 'source.py'], ['/usr/bin/head', '-n', '10', 'instructions.md'],
    ['tail', 'README.md'], ['sed', '-n', '1,200p', 'source.py'], ['sed', 's/x/y/', 'source.py'],
    ['awk', 'NR > 100 && NR < 200 { print }', 'source.py'],
    ['bash', '-lc', 'git status --short; cat "source file.py"'],
    ['env', 'PROJECT=local', 'sh', '-c', 'head -40 src/a.py | wc -l'],
    ['sh', '-c', 'printf ok && tail -30 instructions.md'],
    ['git', 'show', 'HEAD:src/source.py'], ['git', 'cat-file', '-p', 'object'],
    ['python3', '-c', 'print(Path("source.py").read_text()[:100])'],
    ['node', '-e', 'process.stdout.write(readFileSync("source.py", "utf8").slice(0,20))'],
])
def test_file_inspection_cannot_enter_bounded_command_reduction(argv):
    with pytest.raises(ValidationError, match='complete-file'):
        require_non_file_output(argv)


@pytest.mark.parametrize('argv', [
    ['python3', '-m', 'pytest', '-q'], ['npm', 'test'], ['git', 'diff', '--stat'],
    ['git', 'show', '--stat', 'HEAD'], ['rg', 'function', 'src/'],
    ['sed', '-i', '', 's/old/new/', 'source.py'], ['dotnet', 'test'],
    ['sh', '-c', 'make test && git diff --check'],
])
def test_non_file_command_output_retains_isolated_execution_path(argv):
    require_non_file_output(argv)
