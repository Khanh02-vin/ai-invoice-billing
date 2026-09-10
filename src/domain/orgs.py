"""Domain models for organizations / workspaces + RBAC.

Phase 2 multi-tenant: organizations group users; Membership binds user to org with a role.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class OrgRole(str, Enum):
    owner = "owner"
    admin = "admin"
    member = "member"
    viewer = "viewer"


# numeric hierarchy for RBAC comparisons (higher = more privilege)
_ROLE_RANK: dict[str, int] = {
    OrgRole.viewer.value: 0,
    OrgRole.member.value: 1,
    OrgRole.admin.value: 2,
    OrgRole.owner.value: 3,
}


def role_rank(role: str | OrgRole) -> int:
    v = role.value if isinstance(role, OrgRole) else str(role)
    return _ROLE_RANK.get(v, -1)


def can_invite(actor_role: OrgRole, target_role: OrgRole) -> bool:
    """RBAC check for invite: owner can invite any, admin only member/viewer, others cannot."""
    if actor_role == OrgRole.owner:
        return True
    if actor_role == OrgRole.admin:
        return target_role in (OrgRole.member, OrgRole.viewer)
    return False


def can_remove(actor_role: OrgRole | None, target_role: OrgRole | None, is_self: bool = False) -> bool:
    """RBAC check for removal.

    - Self-removal (leaving) always allowed.
    - Owner can remove anyone.
    - Admin can remove member/viewer only.
    - member/viewer cannot remove others.
    """
    if is_self:
        return True
    if actor_role is None or target_role is None:
        return False
    if actor_role == OrgRole.owner:
        return True
    if actor_role == OrgRole.admin:
        return target_role in (OrgRole.member, OrgRole.viewer)
    return False


class Organization(BaseModel):
    """Workspace / Organization."""

    id: str
    name: str
    created_by: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Membership(BaseModel):
    """User membership in an organization."""

    org_id: str
    user_id: str
    role: OrgRole
    joined_at: datetime = Field(default_factory=datetime.utcnow)


class OrgCreate(BaseModel):
    """Request body for creating an organization."""

    name: str = Field(min_length=1, max_length=100, description="Organization name")


class OrgInvite(BaseModel):
    """Request body for inviting a user to an organization."""

    username: str = Field(min_length=1, max_length=64)
    role: OrgRole = Field(default=OrgRole.member)
