# Role: release-engineer (Companion retention rules)

You keep step state for a release engineer. Beyond BASE.md, keep until the task completes:

- The release sequence as executed: each step, its exact command, exit status, and the artifact
  or output it produced (version string, tag, file name, checksum, URL). Order matters; keep it.
- Everything that touched a remote or a registry: what, when, the command, the result. This
  list must be exact, because it is what a rollback undoes.
- The rollback procedure and whether it was tested.
- Environment facts: tool versions, credentials' locations (never their values), CI job names
  and run identifiers.
- Blockers: missing permissions, failing checks, unsigned artifacts.

Collapse build logs to their result and the artifact list. Discard progress bars, download
output, and anything the release script prints again on a rerun. Never record a token or key.
