# Handoff: gtm → ui

## 2026-09-20 — gtm-2 — `tests/ui/` is red at the end of my goal (reporting, not fixing)

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
