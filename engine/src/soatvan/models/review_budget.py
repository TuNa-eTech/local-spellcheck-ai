from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MIN_REVIEW_DOCUMENT_TOKENS = 64
LLM_ONLY_2K_MAX_TOKENS = 768
LLM_ONLY_4K_MAX_TOKENS = 2048
LLM_ONLY_8K_MAX_TOKENS = 4096
REVIEW_SAFETY_TOKENS = 256
CHAT_FALLBACK_OVERHEAD_TOKENS = 32


def estimate_tokens(text: str) -> int:
    """Approximate a token count without a tokenizer.

    Used when the real llama.cpp tokenizer is out of reach — either the runtime
    declined to count, or the model is not loaded at all and we still owe the UI
    a budget answer. Deliberately coarse and on the generous side for
    Vietnamese, so a prompt the UI calls "fits" is not one the planner rejects.
    """
    return max(1, (len(text.encode("utf-8")) + 2) // 3)


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


@dataclass(frozen=True, slots=True)
class ReviewRuntimeBudget:
    """Everything the review path derives from a manifest, before any load."""

    budget: ReviewBudget
    review_output_tokens: int
    filter_output_tokens: int

    @property
    def context_tokens(self) -> int:
        return self.budget.context_tokens


def review_budget_from_manifest(manifest: dict[str, Any]) -> ReviewRuntimeBudget:
    """Derive the review token budget from a model manifest.

    Pure arithmetic over manifest values — no runtime, no GGUF, no tokenizer.
    That is what lets ``review.prompt_budget`` answer for a model that is merely
    installed, and it keeps one derivation behind both that answer and the
    planner that enforces it.
    """
    context_tokens = int(manifest.get("context_size") or 2048)
    filter_output_tokens = int(manifest.get("max_tokens") or 512)

    if context_tokens >= 8192:
        review_output_limit = LLM_ONLY_8K_MAX_TOKENS
    elif context_tokens >= 4096:
        review_output_limit = LLM_ONLY_4K_MAX_TOKENS
    else:
        review_output_limit = LLM_ONLY_2K_MAX_TOKENS
    review_output_tokens = max(
        32,
        min(
            review_output_limit,
            context_tokens - REVIEW_SAFETY_TOKENS - MIN_REVIEW_DOCUMENT_TOKENS,
        ),
    )

    response_tokens = max(filter_output_tokens, review_output_tokens)
    # Keep chunks small enough for the model to focus on each paragraph.
    # document_limit() will naturally cap this at (input_tokens - prompt_overhead)
    # so it never overflows, but the planner uses this as the *target* size.
    configured_doc_tokens = int(manifest.get("review_chunk_tokens") or 700)

    return ReviewRuntimeBudget(
        budget=ReviewBudget(
            context_tokens=context_tokens,
            response_tokens=response_tokens,
            safety_tokens=REVIEW_SAFETY_TOKENS,
            document_tokens=configured_doc_tokens,
        ),
        review_output_tokens=review_output_tokens,
        filter_output_tokens=filter_output_tokens,
    )


@dataclass(frozen=True, slots=True)
class DocumentBudget:
    """How much document text survives the fixed prompt cost of one request.

    Carries the numbers instead of raising on them, so the pre-flight check and
    the planner read the same arithmetic. The planner still fails closed — it
    raises on ``error_code``.
    """

    base_limit: int
    base_fixed_tokens: int
    custom_limit: int
    custom_fixed_tokens: int
    has_custom_prompt: bool

    @property
    def limit(self) -> int:
        return self.custom_limit if self.has_custom_prompt else self.base_limit

    @property
    def error_code(self) -> str | None:
        if self.base_limit < MIN_REVIEW_DOCUMENT_TOKENS:
            # The stock prompt alone overflows: the custom prompt is not to blame.
            return "MODEL_REVIEW_CONTEXT_TOO_SMALL"
        if self.has_custom_prompt and self.custom_limit < MIN_REVIEW_DOCUMENT_TOKENS:
            return "CUSTOM_PROMPT_CONTEXT_EXCEEDED"
        return None
