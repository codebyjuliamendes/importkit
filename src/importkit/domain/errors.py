"""Domain-level failures that reject a whole upload (as opposed to row errors)."""

from __future__ import annotations


class ImportRejectedError(Exception):
    """The upload cannot be previewed at all; carries a stable machine code."""

    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
