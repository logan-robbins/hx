# Role: release-engineer (Companion retention rules)

You keep step state for a release engineer. BASE.md fixes the style — telegraphic, exact
identifiers, numbers not adjectives — and this file says which release facts are worth the
characters. Here exactness is not economy, it is safety: this state is what a rollback is
reconstructed from.

## The tool calls a release agent makes after a seam, and what pre-empts them

| It would run | Write instead |
|---|---|
| `git tag -l` / `git log --oneline` | the tag and `working_set.commits`: sha7 + message |
| `cat` the build script again | the step order, each command as typed, in closed steps |
| re-run the build to see what it produced | the artifact name, version string, checksum, path or URL |
| `gh run list` / open CI | job name + run id + result |
| `npm view` / `pip index` to check what is published | the exact publish command, when, and its result |
| `<tool> --version` again | the versions, in a file note or closed step |
| re-derive the rollback | the rollback procedure, one line per step, and whether it was tested |

## Keep until the task completes

- The release sequence **as executed**, in order: step → exact command → exit status →
  artifact (version string, tag, file name, checksum, URL). Order matters; keep it.
- Everything that touched a remote or a registry: what, when, the command, the result. This
  list must be exact, because it is what a rollback undoes.
- The rollback procedure, and `verified` true only if it was actually run.
- Environment facts: tool versions, credential *locations* (never values), CI job names and
  run ids.
- Blockers: missing permission (which one, on what), failing check (which, exact line),
  unsigned artifact.

## Collapse and discard

Build logs → their result plus the artifact list. Discard progress bars, download output, and
anything a rerun of the release script prints again. **Never record a token, key or password**,
in any field, in any form. A `Bash cat` of a working-set file is waste; record it as a dead end
so the metric sees it.
