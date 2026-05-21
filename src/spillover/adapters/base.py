from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ConversationTurn:
    role: str
    content: Any
    tool_calls: list[dict] = field(default_factory=list)
    token_count: int = 0
    source_index: int | None = None  # original position in inbound payload
    source: Literal["live", "injected"] = "live"  # set to "injected" by retriever (Plan 2)


@dataclass
class Conversation:
    system: str | list[dict] | None = None
    system_tokens: int = 0
    turns: list[ConversationTurn] = field(default_factory=list)
    model: str | None = None
    max_tokens: int = 4096
    extra: dict = field(default_factory=dict)  # provider-specific passthrough

    @property
    def total_input_tokens(self) -> int:
        return self.system_tokens + sum(t.token_count for t in self.turns)


class Adapter(ABC):
    @abstractmethod
    def parse(self, payload: dict) -> Conversation:
        ...

    @abstractmethod
    def build(self, conversation: Conversation) -> dict:
        ...

    @abstractmethod
    def extract_usage_non_streaming(self, body: bytes) -> tuple[int, int] | None:
        ...

    @abstractmethod
    def extract_usage_sse(self, captured: list[bytes]) -> tuple[int, int] | None:
        ...

    @abstractmethod
    def parse_response_text(self, resp_json: dict) -> str:
        ...

    @abstractmethod
    def extract_assistant_text_sse(self, captured: list[bytes]) -> str:
        ...

    @abstractmethod
    def inject_ltm(self, payload: dict, ltm_text: str) -> None:
        """Mutate payload in place to insert the LTM block at the right place."""
        ...
