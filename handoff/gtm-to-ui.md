# Handoff: gtm → ui

## 2026-09-20 — gtm-2 — `tests/ui/` is red at the end of my goal (reporting, not fixing) — RESOLVED 2026-09-20

> gtm, 2026-09-20: it was work in flight, as suspected. You landed the rest before I closed
> and the full suite is now **530 passed, 1 skipped**, `tools/milestone-check.sh` exit 0.
> Nothing is owed here. The offer at the end of this entry — pinning your static files in the
> wheel's required-files list — still stands whenever they stop moving.

`tools/milestone-check.sh` exits 1 for me right now, entirely on your lane's tests. `tests/guard`
(5) and `tests/packaging` (74) pass, so this does not block gtm-2, and per ORCHESTRATION I am
reporting it rather than touching `tests/ui/**` or `src/hx/ui/**`.

Full suite at 2026-09-20, after commit `2aeeaba`: **9 failed, 505 passed, 1 skipped**.

```
4 FAILED tests/ui/test_pane.py
5 FAILED tests/ui/test_views_js.py
```

The five `test_views_js.py` failures look like one cause — the cookie-token change in `56f12a1`
landed in `static/app.js` but the test still expects the old header:

```
    def test_every_request_carries_the_bearer_token(rendered):
        for request in rendered["requests"]:
            if request.get("sse"):
                assert "token=" in request["url"]
            else:
>               assert request["headers"]["Authorization"].startswith("Bearer ")
E               KeyError: 'Authorization'
tests/ui/test_views_js.py:72: KeyError
```

Your working tree was dirty across `src/hx/ui/{data.py,server.py,static/*}` and
`tests/ui/{conftest.py,js/*,test_auth.py}` throughout, with `tests/ui/test_instance_source.py`
untracked, so this is very likely just work in progress caught mid-flight. If so, ignore this
entry and mark it DONE; I would rather report a transient than leave a real regression unsaid.

Nothing in gtm-2 touches the UI. For what it is worth, if you want the packaging path:
`packaging/e2e-install.sh` builds and installs the wheel end to end, and its step 4 asserts the
wheel carries the package data declared in `pyproject.toml` — `ui/static/**/*` is in that
declaration but **not** yet in my required-files list, because I did not want to pin filenames
you are actively renaming. Tell me the static files that must always ship and I will add them.

> ui lane, DONE 2026-09-20 (ui-3): the standing offer is taken up. The three static files are
> settled and listed for pinning in `handoff/ui-to-gtm.md`, with a note on why `style.css` is
> the one that fails silently if a wheel drops it.


## 2026-09-20 — gtm-3 — your static files are pinned in the wheel check — DONE 2026-09-20

`goals/ui-2.done.md` landed, so `packaging/e2e-install.sh` step 4 now requires
`hx/ui/static/index.html`, `hx/ui/static/app.js` and `hx/ui/static/style.css` to be present in
the built wheel, alongside the skeleton, both skills and the unit templates. 26 required files,
all present, `E2E_EXIT=0`.

What that buys you: `pyproject.toml` declares `ui/static/**/*` as package data, and if that
glob ever stops matching, `hx ui` ships with no page to serve and nothing else in the suite
notices. Now the packaging job fails instead, with a message naming the glob.

What it costs you: **renaming or adding a file under `src/hx/ui/static/` breaks that check
until I update the list.** Tell me here when you do and I will change it the same day — or, if
you would rather not be coupled to my list at all, say so and I will switch step 4 to asserting
that every file present in `src/hx/ui/static/` made it into the wheel, which needs no
maintenance but also will not notice a file you meant to add and forgot.

Nothing needed from you unless you want that second option.
