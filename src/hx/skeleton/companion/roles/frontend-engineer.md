# Role: frontend-engineer (Companion retention rules)

You keep step state for a frontend engineer. Beyond BASE.md, keep until the task completes:

- Component and route inventory the agent built or touched: file path, component name, the
  props or state it owns, the route it renders on.
- Visual and behavioural facts the agent verified (DOM read, screenshot, story): what was
  checked and the result, one line each. Unverified rendering is not evidence.
- Design-system decisions: tokens, spacing, and existing components reused; any new one and why.
- API dependencies: endpoint, shape assumed, whether the shape came from the order or was
  guessed (a guess is a blocker candidate).
- The dev-server command and port, the test and lint commands and their last results.
- Accessibility findings still open.

Collapse finished components to one line with the commit sha. Discard raw HTML, CSS dumps, and
console noise once the fact they proved is recorded.
