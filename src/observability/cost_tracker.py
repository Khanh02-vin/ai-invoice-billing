"""Cost tracker — track AI/LLM costs per user for budget enforcement.

Features:
- Track tokens and USD cost per user
- Budget limits per user
- Periodic cost aggregation
- Export for billing integration
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class CostRecord:
    """Single cost record for an operation."""
    user_id: str
    operation: str  # "llm_extraction", "ocr", "embedding"
    tokens: int = 0
    cost_usd: float = 0.0
    timestamp: float = field(default_factory=time.time)
    metadata: Dict = field(default_factory=dict)


class CostTracker:
    """Track AI/LLM costs per user for budget enforcement.

    Features:
    - Thread-safe cost recording
    - Per-user budget limits
    - Periodic cost aggregation
    - Export for billing integration
    """

    def __init__(self):
        self._records: List[CostRecord] = []
        self._lock = threading.Lock()
        self._budgets: Dict[str, float] = {}  # user_id -> max_usd
        self._alert_callbacks: List = []

    def record(
        self,
        user_id: str,
        operation: str,
        tokens: int = 0,
        cost_usd: float = 0.0,
        metadata: Optional[Dict] = None,
    ):
        """Record a cost event."""
        with self._lock:
            self._records.append(CostRecord(
                user_id=user_id,
                operation=operation,
                tokens=tokens,
                cost_usd=cost_usd,
                metadata=metadata or {},
            ))

        # Check budget
        if not self.check_budget(user_id, cost_usd):
            self._alert_budget_exceeded(user_id, cost_usd)

    def get_user_cost(
        self,
        user_id: str,
        period_hours: int = 24,
    ) -> float:
        """Get total cost for a user in the last N hours."""
        cutoff = time.time() - (period_hours * 3600)
        with self._lock:
            return sum(
                r.cost_usd for r in self._records
                if r.user_id == user_id and r.timestamp > cutoff
            )

    def get_user_tokens(
        self,
        user_id: str,
        period_hours: int = 24,
    ) -> int:
        """Get total tokens used by a user in the last N hours."""
        cutoff = time.time() - (period_hours * 3600)
        with self._lock:
            return sum(
                r.tokens for r in self._records
                if r.user_id == user_id and r.timestamp > cutoff
            )

    def check_budget(self, user_id: str, estimated_cost: float) -> bool:
        """Check if user is within budget. Returns True if OK."""
        if user_id not in self._budgets:
            return True  # No budget set = unlimited

        current_cost = self.get_user_cost(user_id, period_hours=24)
        return (current_cost + estimated_cost) <= self._budgets[user_id]

    def set_budget(self, user_id: str, max_usd: float):
        """Set budget limit for a user."""
        self._budgets[user_id] = max_usd
        logger.info(f"Budget set for {user_id}: ${max_usd:.2f}/day")

    def add_alert_callback(self, callback):
        """Add callback for budget alerts."""
        self._alert_callbacks.append(callback)

    def _alert_budget_exceeded(self, user_id: str, cost: float):
        """Alert when budget exceeded."""
        current = self.get_user_cost(user_id)
        limit = self._budgets.get(user_id, float("inf"))
        logger.warning(
            f"Budget exceeded for {user_id}: ${current:.2f} / ${limit:.2f}"
        )
        for callback in self._alert_callbacks:
            try:
                callback(user_id, current, limit)
            except Exception as e:
                logger.error(f"Alert callback failed: {e}")

    def export_records(
        self,
        user_id: Optional[str] = None,
        period_hours: int = 24,
    ) -> List[Dict]:
        """Export cost records for billing integration."""
        cutoff = time.time() - (period_hours * 3600)
        with self._lock:
            records = [
                {
                    "user_id": r.user_id,
                    "operation": r.operation,
                    "tokens": r.tokens,
                    "cost_usd": r.cost_usd,
                    "timestamp": datetime.fromtimestamp(r.timestamp, tz=timezone.utc).isoformat(),
                    "metadata": r.metadata,
                }
                for r in self._records
                if r.timestamp > cutoff and (user_id is None or r.user_id == user_id)
            ]
        return records

    def reset(self):
        """Reset all records (for testing)."""
        with self._lock:
            self._records.clear()
            self._budgets.clear()


# Singleton instance
cost_tracker = CostTracker()
