# gtm-6: the deploy proof for real, docs in the present tense, and `hx upgrade` documented

Sent after build-4 landed. Read `goals/gtm-4.done.md` (yours), `goals/build-4.done.md`,
`handoff/orchestrator-to-gtm.md`, any other `handoff/*-to-gtm.md`, spec 17.2, 17.6.

## Build

1. `packaging/e2e-deploy.sh <scratch>` runs to PASS. Tighten it: assert the install stops with exit 4
   until a fake `seed/token` is placed, then proceeds, and that `start.sh` (fake `claude`)
   receives `CLAUDE_CODE_OAUTH_TOKEN` in its env and no credentials file exists in any home; assert `hx upgrade` refuses a
   fake `claude` reporting a version not in the tested list and accepts one that is; assert
   `hx push` to a second local bare repo lands the `hx/<id>` branch and touches no other ref.
   Paste the last 15 lines in the done file. Fix discrepancies between what build-4 built and
   the contracts you published by writing them to `handoff/gtm-to-build.md`, not by loosening
   the script.
2. `docs/two-worlds.md`: remove the "What is built today" hedge where it is now true; every
   remaining claim is verified against the code as in gtm-4 (list them).
3. `docs/deploy.md`: replace the pre-release notes with the real `hx install` transcript from
   step 1 (with the seed-login stop and its exact command), the real rendered unit paths, and
   the `launchctl`/`systemctl --user` commands the human runs; add a short `hx upgrade` section.
4. `README.md` and `CHANGELOG.md` updated to what exists.
5. `.github/workflows/ci.yml`: the `package` job runs `packaging/e2e-deploy.sh` as well as
   `e2e-install.sh`; on the Linux job, after the deploy proof, run
   `systemd-analyze --user verify` on the rendered units if `systemd-analyze` exists (it does on
   `ubuntu-latest`) and fail on error. Validate with the same structural check as before.

## Done when

- `tools/milestone-check.sh` passes for `tests/guard`, `tests/packaging`, `tests/scenario`.
- `packaging/e2e-deploy.sh <scratch>` prints `PASS`.
- Committed path-scoped. `goals/gtm-6.done.md` written, with handoffs.
