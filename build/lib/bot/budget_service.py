from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class BudgetDecision:
    allowed: bool
    reason: str | None = None


class BudgetService:
    def __init__(self, daily_token_budget: int, topic_token_budget: int) -> None:
        self.daily_token_budget = daily_token_budget
        self.topic_token_budget = topic_token_budget

    def allow(self, estimated_topic_tokens: int, estimated_daily_tokens: int) -> BudgetDecision:
        if estimated_topic_tokens > self.topic_token_budget:
            return BudgetDecision(False, "topic budget exceeded")
        if estimated_daily_tokens > self.daily_token_budget:
            return BudgetDecision(False, "daily budget exceeded")
        return BudgetDecision(True)
