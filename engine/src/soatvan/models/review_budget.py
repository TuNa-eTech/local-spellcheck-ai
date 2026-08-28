from __future__ import annotations

from dataclasses import dataclass

MIN_REVIEW_DOCUMENT_TOKENS = 64


@dataclass(frozen=True, slots=True)
class ReviewBudget:
    """Immutable token limits for one full-review request.

    ``document_tokens`` is the configured target-text cap. Fixed chat/prompt
    tokens are accounted for separately when the planner derives the usable
    document limit for a request.
    """

    context_tokens: int
    response_tokens: int
    safety_tokens: int
    document_tokens: int

    def __post_init__(self) -> None:
        values = (
            self.context_tokens,
            self.response_tokens,
            self.safety_tokens,
            self.document_tokens,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ValueError("MODEL_REVIEW_CONTEXT_TOO_SMALL")
        if (
            self.context_tokens < 1
            or self.response_tokens < 1
            or self.safety_tokens < 0
            or self.document_tokens < 1
            or self.input_tokens < MIN_REVIEW_DOCUMENT_TOKENS
        ):
            raise ValueError("MODEL_REVIEW_CONTEXT_TOO_SMALL")

    @property
    def input_tokens(self) -> int:
        return self.context_tokens - self.response_tokens - self.safety_tokens

    def document_limit(self, fixed_request_tokens: int) -> int:
        """Return the effective document cap without reducing output reserve."""
        if (
            isinstance(fixed_request_tokens, bool)
            or not isinstance(fixed_request_tokens, int)
            or fixed_request_tokens < 0
        ):
            raise ValueError("MODEL_REVIEW_CONTEXT_TOO_SMALL")
        return min(self.document_tokens, self.input_tokens - fixed_request_tokens)
