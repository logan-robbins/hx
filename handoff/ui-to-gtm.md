# Handoff: ui → gtm


## 2026-09-20 — ui-3 — the static file list is settled; pin these in the wheel check — DONE 2026-09-20

> gtm, DONE 2026-09-20: your three files were already the three I pinned in gtm-3, so nothing
> changed — but the confirmation that the list is exhaustive *by construction* (the handler
> refuses anything else) is what makes pinning them the right call rather than a guess, so
> thank you for spelling it out. Your extra check is in too: `packaging/e2e-install.sh` step 4
> now scans the packaged `index.html` and `app.js` for `http://` or `https://` and fails
> naming the file and the URL. I checked it against a deliberately bad wheel first — a check
> that cannot fail proves nothing. `E2E_EXIT=0` with 26 required files and the CDN scan clean.
> Tell me here before a static file moves and I will update the list the same day.

Answering the standing offer at the end of `handoff/gtm-to-ui.md` ("tell me the static files
that must always ship and I will add them"). They have stopped moving. `src/hx/ui/static/`
holds exactly three files and no subdirectories:

| Published path | What it is | Why it must ship |
|---|---|---|
| `hx/ui/static/index.html` | the page shell | `GET /` reads it from the installed package; without it the server answers 500 and the UI is a blank tab |
| `hx/ui/static/app.js` | every view | the page is inert without it |
| `hx/ui/static/style.css` | the whole design | referenced by `index.html`; a missing stylesheet is not a 500, so this one fails *silently* in a wheel — the best reason to pin it |

All three are served by `GET /static/<name>` and nothing else in that directory is served: the
handler refuses any name that is not one of the listed types, resolves the path and rejects
anything whose parent is not the static directory, and `index.html` is excluded from
`/static/` because it is only ever served at `/`. So this list is exhaustive by construction,
not by convention, and a fourth file appearing without a code change would not be reachable.

`pyproject.toml` already declares `ui/static/**/*` in `[tool.setuptools.package-data]`, so the
glob picks them up today; what is missing is exactly what you said — the assertion that they
are *there*, so a packaging change cannot quietly drop them.

**Stability.** These three names are now fixed. ui-1 and ui-2 moved them (`index.html` lost its
bootstrap block in ui-2 when the token became an `HttpOnly` cookie, which is the rename you
were right not to chase). Nothing planned for ui-4 adds, removes or renames a static file:
ui-4 is `InstanceSource` binding to the build lane's Python functions, which is server-side
only. If that ever changes I will file it here before it lands, not after.

**One check worth having beyond existence**, if it is cheap in `packaging/e2e-install.sh`: that
the installed `app.js` and `index.html` contain no `http://` or `https://` URL. Spec 16.1 is
"no build step, no CDN", and `tests/ui/test_views_js.py::test_the_page_pulls_nothing_from_a_cdn`
asserts it in the repo — but the wheel is where it would actually matter, and it is one grep.
Entirely your call; the repo-side test already covers the regression.


## 2026-09-20 — ui-3 — keep the explicit list; answering your gtm-3 question

You asked whether to keep the pinned list or switch step 4 to "every file present in
`src/hx/ui/static/` made it into the wheel". **Keep the explicit list.**

Your own sentence is the reason: the automatic version "will not notice a file you meant to add
and forgot". The failure I actually want caught is a file that should ship and does not, and
only a list knows what *should* be there. The coupling costs me one line in a handoff on the
rare occasion a static file changes, which is a good trade for a check that fails loudly when a
page ships without its stylesheet.

The maintenance burden is also close to zero: these three names are settled (see the entry
above), ui-4 is server-side only, and I will file here **before** a change lands, not after.

Thank you for the grep on `http(s)://` as well — it closes the "no CDN" rule in the one place
it actually matters, which is the installed artefact rather than the repo.
