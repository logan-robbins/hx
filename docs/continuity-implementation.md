# Continuity implementation

The current continuity design, behavior, and remaining integration work are documented
in [system design](system-design.md). Runtime contracts and verified capabilities are
documented in [continuity runtime](continuity-runtime.md) and [implementation status](continuity-build-status.md).

The running design uses the current goal and repository state as its reference. It
retains only task information still needed to complete active work, compresses facts
when their meaning is still needed, and deletes obsolete state. Jev classifies bounded
observation and routing deltas; the companion handles factual changes. Token limits,
autocompaction, tool boundaries, context reset, and model output caps enforce context
size at their respective runtime layers.
