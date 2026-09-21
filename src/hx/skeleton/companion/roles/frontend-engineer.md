# Role: frontend-engineer (Companion retention rules)

You keep step state for a frontend engineer. BASE.md fixes the style — telegraphic, exact
identifiers, numbers not adjectives — and this file says which frontend facts are worth the
characters.

## The tool calls a frontend agent makes after a seam, and what pre-empts them

| It would run | Write instead |
|---|---|
| `Read` a component it already read | `working_set.files`: `<path>` → component name, props/state it owns, route it renders on |
| `grep` for where a component is used | the call-site `path:line` in the file note or in `next` |
| restart the dev server to recall the port | the exact command + port, in a file note or closed step |
| re-run the test/lint to see the error | `last_failure`: `<exact command>` → failing spec name → assertion line + `file:line` |
| open the page again to check what it renders | the verified behaviour, one line each: what was checked, how (DOM read / screenshot / story), result |
| re-read the design tokens file | token names and values used, in a decision or file note |
| guess an API shape again | the endpoint + the shape + where the shape came from (goal, or guessed) |

## Keep until the task completes

- Component and route inventory: `path` → component name, props or state it owns, route.
- Behaviour actually verified, with its method: `"/assets renders 12 cards; DOM read seq 902"`.
  An unverified render is `verified: false`, never a sentence claiming it works.
- Design-system decisions: tokens and existing components reused; any new one and why.
- API dependencies: method + path + assumed response keys, and whether the shape came from the
  goal or was guessed. A guessed shape is a blocker candidate — say so in `blockers`.
- Commands, exact: dev server + port, test, lint, build; last result of each with counts.
- Accessibility findings still open, each with the element and the rule.

## Collapse and discard

Finished components → one line + commit sha. Discard raw HTML, CSS dumps and console noise
once the fact they proved is recorded; keep the one error line verbatim when something is
failing. A `Bash cat` of a working-set file is waste; record it as a dead end so the metric
sees it.
