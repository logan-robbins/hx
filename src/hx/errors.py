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
