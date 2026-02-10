"""Simple conversation memory implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class MemoryRecord:
    role: str
    content: str
    metadata: Dict[str, str] | None = None


class ConversationBufferMemory:
    """Stores a bounded list of conversation records in memory."""

    def __init__(self, max_items: int = 20) -> None:
        self.max_items = max_items
        self._items: List[MemoryRecord] = []

    def add(self, role: str, content: str, metadata: Dict[str, str] | None = None) -> None:
        self._items.append(MemoryRecord(role=role, content=content, metadata=metadata))
        if len(self._items) > self.max_items:
            self._items = self._items[-self.max_items :]

    def dump(self) -> List[MemoryRecord]:
        return list(self._items)

    def clear(self) -> None:
        self._items.clear()
