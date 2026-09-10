"""Entitlements — quyền lợi theo plan tier.

ENTITLEMENTS dict map PlanTier -> {invoice_limit, llm_access, pdf_export}.
check_entitlements(org_id, action) -> bool dùng BillingRepository.
"""
from __future__ import annotations

from typing import Dict, Any
from .models import PlanTier


# --- Entitlements config ---
ENTITLEMENTS: Dict[PlanTier, Dict[str, Any]] = {
    PlanTier.FREE: {
        "invoice_limit": 10,
        "llm_access": False,
        "pdf_export": False,
    },
    PlanTier.PRO: {
        "invoice_limit": 100,
        "llm_access": True,
        "pdf_export": True,
    },
    PlanTier.ENTERPRISE: {
        "invoice_limit": 10_000,
        "llm_access": True,
        "pdf_export": True,
    },
}


def check_entitlements(org_id: str, action: str, repo=None) -> bool:
    """Kiểm tra org có quyền thực hiện action không.

    Args:
        org_id: tổ chức cần kiểm tra.
        action: hành động — "invoice.create", "llm.access", "pdf.export".
        repo: BillingRepository instance. Nếu None, tạo mặc định.

    Returns:
        True nếu được phép, False nếu vượt quá giới hạn hoặc không có quyền.
    """
    if repo is None:
        from .repository import BillingRepository
        repo = BillingRepository()

    ent = repo.get_entitlements(org_id)
    if not ent:
        # Mặc định free tier
        tier = PlanTier.FREE
        limits = ENTITLEMENTS[tier]
        repo.upsert_entitlements(org_id, tier, **limits)
        ent = repo.get_entitlements(org_id)

    plan_str = ent.get("plan", "free") if isinstance(ent, dict) else getattr(ent, "plan", "free")
    try:
        tier = PlanTier(plan_str)
    except ValueError:
        tier = PlanTier.FREE

    limits = ENTITLEMENTS.get(tier, ENTITLEMENTS[PlanTier.FREE])

    if action == "invoice.create":
        # Kiểm tra số lượng invoice đã tạo (mock: dùng invoice_limit như quota tuyệt đối)
        return limits.get("invoice_limit", 0) > 0

    if action == "llm.access":
        return bool(limits.get("llm_access", False))

    if action == "pdf.export":
        return bool(limits.get("pdf_export", False))

    return False
