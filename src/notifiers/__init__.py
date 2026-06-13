from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Notifier(Protocol):
    async def send_review_card(self, item_id: str, card: dict) -> int | None:
        """推一张审稿卡片。返回平台 message_id。不支持交互的通道返回 None。"""
        ...

    async def send_final_report(self, markdown: str, summary: dict) -> None:
        """发送定稿日报。summary = {date_label, item_count, must_read_count}。"""
        ...

    async def poll_decisions(self) -> list[tuple[str, str]]:
        """取出待处理决策队列 [(item_id, action), ...]。"""
        ...


class FakeNotifier:
    """测试用的内存实现，记录所有调用。"""

    def __init__(self):
        self.sent_cards: list[tuple[str, dict]] = []
        self.final_report: str | None = None
        self._decisions: list[tuple[str, str]] = []

    def queue_decision(self, item_id: str, action: str) -> None:
        self._decisions.append((item_id, action))

    async def send_review_card(self, item_id: str, card: dict) -> int | None:
        self.sent_cards.append((item_id, card))
        return len(self.sent_cards)

    async def send_final_report(self, markdown: str, summary: dict) -> None:
        self.final_report = markdown

    async def poll_decisions(self) -> list[tuple[str, str]]:
        out, self._decisions = self._decisions, []
        return out
