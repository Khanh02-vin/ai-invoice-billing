"""Ops package: backup/restore, privacy/GDPR helpers.

Phase 4 ops + privacy + accessibility:
- backup.py: BackupManager for SQLite DB + storage dir (zipfile, stdlib only).
- privacy.py: PrivacyManager for GDPR-style export/erasure, PII redaction, policy/SLA metadata.
"""
from .backup import BackupManager
from .privacy import PrivacyManager

__all__ = ["BackupManager", "PrivacyManager"]
