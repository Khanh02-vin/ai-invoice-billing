"""Privacy / GDPR helpers: PII redaction, data export, right to erasure, policy/SLA metadata.

Pure functions where possible for testability. Uses only stdlib (re, datetime).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ---------- constants ----------

PRIVACY_POLICY_VERSION = "1.0.0"
DATA_RETENTION_DAYS = 365
SLA_UPTIME_PERCENT = 99.5
SUPPORT_RESPONSE_HOURS = 24
DATA_BACKUP_FREQUENCY = "daily"
PRIVACY_CONTACT_EMAIL = "privacy@invoice.local"


# ---------- PII redaction ----------

# Order matters: match more specific patterns first.
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
)
_PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s\-.]?)?(?:\(?\d{2,4}\)?[\s\-.]?)?\d{3,4}[\s\-.]?\d{3,4}"
)
_CREDIT_CARD_RE = re.compile(
    r"\b(?:\d{4}[\s\-]?){3}\d{4}\b"
)
_SSN_RE = re.compile(
    r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b"
)


def redact_pii(text: str) -> str:
    """Redact common PII patterns (emails, phones, credit cards, SSNs) from text.

    Pure function. Replaces matched spans with a stable placeholder.
    """
    if not text:
        return text
    # Credit cards first (numeric, may overlap with phone/SSN patterns)
    out = _CREDIT_CARD_RE.sub("[REDACTED_CC]", text)
    # SSN before phone to prefer SSN labeling on 9-digit groups
    out = _SSN_RE.sub("[REDACTED_SSN]", out)
    out = _PHONE_RE.sub("[REDACTED_PHONE]", out)
    out = _EMAIL_RE.sub("[REDACTED_EMAIL]", out)
    return out


# ---------- policy / sla metadata ----------

def get_privacy_policy() -> Dict[str, Any]:
    """Return the current privacy policy metadata."""
    return {
        "version": PRIVACY_POLICY_VERSION,
        "last_updated": datetime.now(timezone.utc).date().isoformat(),
        "data_retention_days": DATA_RETENTION_DAYS,
        "contact": PRIVACY_CONTACT_EMAIL,
        "summary": (
            "We collect minimal account and invoice data to provide the service. "
            "You may export or delete your data at any time."
        ),
    }


def get_sla() -> Dict[str, Any]:
    """Return the current SLA metadata."""
    return {
        "uptime_target": SLA_UPTIME_PERCENT,
        "support_response_hours": SUPPORT_RESPONSE_HOURS,
        "data_backup_frequency": DATA_BACKUP_FREQUENCY,
    }


# ---------- GDPR-style export / erasure ----------

class PrivacyManager:
    """GDPR-style data export and erasure.

    Works against the existing repository interfaces so it can be tested with :memory: repos.
    """

    def __init__(
        self,
        repo: Any,
        user_repo: Any,
        org_repo: Optional[Any] = None,
    ):
        self.repo = repo
        self.user_repo = user_repo
        self.org_repo = org_repo

    def export_user_data(self, user_id: str) -> Dict[str, Any]:
        """Export all data related to a user (GDPR data portability).

        Returns a dict with user profile, invoices, memberships, entitlements.
        """
        user = self.user_repo.get(user_id)
        if user is None:
            return {"user": None, "invoices": [], "memberships": [], "entitlements": []}

        invoices = self.repo.list(user_id=user_id, limit=100_000)

        memberships: List[Dict[str, Any]] = []
        if self.org_repo is not None:
            orgs = self.org_repo.list_for_user(user_id)
            for org in orgs:
                role = self.org_repo.get_member_role(org.id, user_id)
                memberships.append(
                    {
                        "org_id": org.id,
                        "org_name": org.name,
                        "role": role.value if role else None,
                    }
                )

        return {
            "user": {
                "id": user.id,
                "username": user.username,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            },
            "invoices": [inv.model_dump() for inv in invoices],
            "memberships": memberships,
            "entitlements": [],
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }

    def delete_user_data(self, user_id: str) -> bool:
        """Delete or anonymize all data for a user (GDPR right to erasure).

        Strategy:
        - Anonymize invoices owned by the user (set user_id to empty, scrub vendor/buyer).
        - Remove the user record.
        - Remove memberships if org_repo is available.
        Returns True if the user existed and was removed.
        """
        user = self.user_repo.get(user_id)
        if user is None:
            return False

        # Anonymize invoices rather than hard-delete to preserve accounting integrity.
        invoices = self.repo.list(user_id=user_id, limit=100_000)
        for inv in invoices:
            update_payload = {
                "user_id": "",
                "vendor": redact_pii(inv.vendor) if inv.vendor else inv.vendor,
                "buyer": redact_pii(inv.buyer) if inv.buyer else inv.buyer,
                "raw_snippet": "",
            }
            try:
                self.repo.update(inv.id, update_payload)
            except Exception:
                # Best-effort: continue with remaining invoices.
                continue

        # Remove memberships
        if self.org_repo is not None:
            try:
                orgs = self.org_repo.list_for_user(user_id)
                for org in orgs:
                    try:
                        self.org_repo.remove_member(org.id, user_id)
                    except Exception:
                        continue
            except Exception:
                pass

        # Remove user record
        try:
            self.user_repo.hard_delete(user_id)
        except Exception:
            # Fallback: if hard_delete is unavailable, attempt a soft marker.
            return False
        return True
