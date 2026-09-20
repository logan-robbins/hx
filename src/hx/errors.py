"""Error types shared by every hx module.

`HxError` carries the process exit code the CLI uses. Validator and parser messages
always name the file and the rule that rejected it, so the message alone is enough to
find and fix the offending file (spec 13 M0).
"""

from __future__ import annotations


class HxError(Exception):
    """A user-facing failure. `exit_code` is what `hx` exits with."""

    exit_code = 2

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if exit_code is not None:
            self.exit_code = exit_code


class HxRefusal(HxError):
    """hx refuses to act. Exit 1, and the message always contains the word `refuse`."""

    exit_code = 1


class ValidationError(HxError):
    """A config, order, or work item file failed validation. Exit 2 (spec 05)."""

    exit_code = 2


class NotFound(HxError):
    """A named id, order file, or work item does not exist.

    Distinct from `ValidationError` so a caller can tell "no such id" from "the instance is
    broken": the UI turns this into 404 and everything else into 502
    (handoff/ui-to-build.md).
    """

    exit_code = 2


class Refused(HxError):
    """A transition hx will not make: the wrong state, the wrong caller, a failing check.

    Exit 1, like `HxRefusal`, because the instance is intact and the caller asked for
    something the rules do not allow (spec 06, 08).
    """

    exit_code = 1
