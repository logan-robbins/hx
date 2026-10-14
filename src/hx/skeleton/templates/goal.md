## Goal

Give `hx doctor` a `--json` form so the UI can render the same checks the CLI prints: one JSON
object on stdout with every check the text form shows, the same exit status, and the text form
itself unchanged. The UI's doctor panel (`ui-004`) is waiting on this shape and will consume it
once it lands; nothing else depends on it.
