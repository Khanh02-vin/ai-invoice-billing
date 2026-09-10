"""Organization + Membership repository trên SQLite.

Tables:
  organizations (id TEXT PK, name TEXT, created_by TEXT, created_at TEXT)
  memberships   (org_id TEXT, user_id TEXT, role TEXT, joined_at TEXT, PRIMARY KEY(org_id, user_id))
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from typing import List, Optional

from ..domain.orgs import Membership, Organization, OrgRole
from .db import SQLiteRepo


class OrganizationRepository(SQLiteRepo):
    """Repository cho organizations + memberships."""

    def _ensure_schema(self, conn: sqlite3.Connection):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS organizations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memberships (
                org_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                joined_at TEXT NOT NULL,
                PRIMARY KEY (org_id, user_id),
                FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memberships_org ON memberships(org_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_organizations_created_by ON organizations(created_by)")

    # ---------- helpers ----------

    def _row_to_org(self, row: sqlite3.Row) -> Organization:
        return Organization(
            id=row["id"],
            name=row["name"],
            created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _row_to_membership(self, row: sqlite3.Row) -> Membership:
        return Membership(
            org_id=row["org_id"],
            user_id=row["user_id"],
            role=OrgRole(row["role"]),
            joined_at=datetime.fromisoformat(row["joined_at"]),
        )

    # ---------- CRUD ----------

    def create_org(self, name: str, user_id: str) -> Organization:
        """Create org + add creator as owner. Returns Organization."""
        if not name or not name.strip():
            raise ValueError("Organization name is required")
        org_id = uuid.uuid4().hex[:12]
        now = datetime.utcnow().isoformat()
        org = Organization(
            id=org_id,
            name=name.strip(),
            created_by=user_id,
            created_at=datetime.fromisoformat(now),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO organizations (id, name, created_by, created_at) VALUES (?, ?, ?, ?)",
                (org.id, org.name, org.created_by, org.created_at.isoformat()),
            )
            conn.execute(
                "INSERT INTO memberships (org_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)",
                (org.id, user_id, OrgRole.owner.value, now),
            )
        return org

    def get(self, org_id: str) -> Optional[Organization]:
        """Fetch organization by id, or None."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM organizations WHERE id = ?", (org_id,)).fetchone()
        return self._row_to_org(row) if row else None

    def list_for_user(self, user_id: str) -> List[Organization]:
        """List all orgs where user is a member."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT o.* FROM organizations o
                JOIN memberships m ON m.org_id = o.id
                WHERE m.user_id = ?
                ORDER BY o.created_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [self._row_to_org(r) for r in rows]

    def delete(self, org_id: str) -> bool:
        """Delete org and all memberships. Returns True if deleted."""
        with self._connect() as conn:
            conn.execute("DELETE FROM memberships WHERE org_id = ?", (org_id,))
            cur = conn.execute("DELETE FROM organizations WHERE id = ?", (org_id,))
            return cur.rowcount > 0

    # ---------- membership ----------

    def add_member(self, org_id: str, user_id: str, role: OrgRole | str) -> Membership:
        """Add a member. Raises ValueError if already exists, or org not found."""
        role_val = role.value if isinstance(role, OrgRole) else str(role)
        # validate role
        try:
            OrgRole(role_val)
        except ValueError:
            raise ValueError(f"Invalid role: {role_val}")
        now = datetime.utcnow().isoformat()
        membership = Membership(
            org_id=org_id,
            user_id=user_id,
            role=OrgRole(role_val),
            joined_at=datetime.fromisoformat(now),
        )
        with self._connect() as conn:
            # check org exists
            org = conn.execute("SELECT id FROM organizations WHERE id = ?", (org_id,)).fetchone()
            if not org:
                raise ValueError("Organization not found")
            try:
                conn.execute(
                    "INSERT INTO memberships (org_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)",
                    (org_id, user_id, role_val, now),
                )
            except sqlite3.IntegrityError:
                raise ValueError("User already a member")
        return membership

    def get_member_role(self, org_id: str, user_id: str) -> Optional[OrgRole]:
        """Return role if member, else None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT role FROM memberships WHERE org_id = ? AND user_id = ?",
                (org_id, user_id),
            ).fetchone()
        if not row:
            return None
        try:
            return OrgRole(row["role"])
        except ValueError:
            return None

    def is_member(self, org_id: str, user_id: str) -> bool:
        return self.get_member_role(org_id, user_id) is not None

    def list_members(self, org_id: str) -> List[Membership]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memberships WHERE org_id = ? ORDER BY joined_at ASC",
                (org_id,),
            ).fetchall()
        return [self._row_to_membership(r) for r in rows]

    def remove_member(self, org_id: str, user_id: str) -> bool:
        """Remove membership. Returns True if removed."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM memberships WHERE org_id = ? AND user_id = ?",
                (org_id, user_id),
            )
            return cur.rowcount > 0

    def update_member_role(self, org_id: str, user_id: str, role: OrgRole | str) -> Optional[Membership]:
        role_val = role.value if isinstance(role, OrgRole) else str(role)
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE memberships SET role = ? WHERE org_id = ? AND user_id = ?",
                (role_val, org_id, user_id),
            )
            if cur.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM memberships WHERE org_id = ? AND user_id = ?",
                (org_id, user_id),
            ).fetchone()
        return self._row_to_membership(row) if row else None

    # ---------- RBAC helpers ----------

    def require_role(self, org_id: str, user_id: str, min_role: OrgRole) -> OrgRole:
        """Ensure user has at least min_role; raise ValueError if not."""
        from ..domain.orgs import role_rank

        actual = self.get_member_role(org_id, user_id)
        if actual is None:
            raise ValueError("Not a member")
        if role_rank(actual) < role_rank(min_role):
            raise ValueError(f"Requires {min_role.value}, has {actual.value}")
        return actual
