"""Organization / workspace router with RBAC.

RBAC rules:
  - viewer: read-only (can list/get org + members if member, cannot invite/remove others)
  - member: same as viewer (cannot invite)
  - admin: can invite member/viewer only, can remove member/viewer, cannot invite admin/owner, cannot remove admin/owner
  - owner: can do all (invite any role, remove any)
  - self-removal always allowed (leave org)
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from ..auth.security import decode_token
from ..domain.models import User
from ..domain.orgs import Membership, Organization, OrgCreate, OrgInvite, OrgRole, can_invite, can_remove
from ..errors import AppError
from ..store.orgs import OrganizationRepository
from ..store.users import UserRepository

router = APIRouter(prefix="/orgs", tags=["orgs"])

bearer = HTTPBearer(auto_error=False)

# Module-level repos (file DB default). For tests, repos are used directly :memory: so not patched.
org_repo = OrganizationRepository()
user_repo = UserRepository()


def current_user_local(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> User:
    """Local copy of src.app.current_user to avoid import cycle."""
    if not credentials:
        raise AppError("UNAUTHORIZED", "Cần đăng nhập.", status=401)
    user_id = decode_token(credentials.credentials)
    user = user_repo.get(user_id) if user_id else None
    if not user:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
    return user


# ---------- helpers ----------


def _get_org_or_404(org_id: str) -> Organization:
    org = org_repo.get(org_id)
    if not org:
        raise AppError("NOT_FOUND", "Organization not found.", status=404)
    return org


def _require_membership(org_id: str, user_id: str) -> OrgRole:
    role = org_repo.get_member_role(org_id, user_id)
    if role is None:
        raise AppError("FORBIDDEN", "Not a member of this organization.", status=403)
    return role


# ---------- routes ----------


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=Organization)
def create_org(payload: OrgCreate, user: User = Depends(current_user_local)):
    """Create org and become owner."""
    if not payload.name or not payload.name.strip():
        raise AppError("VALIDATION_ERROR", "Organization name is required.", status=400)
    org = org_repo.create_org(payload.name.strip(), user.id)
    return org


@router.get("/", response_model=List[Organization])
def list_my_orgs(user: User = Depends(current_user_local)):
    """List orgs where current user is member."""
    return org_repo.list_for_user(user.id)


@router.get("/{org_id}", response_model=Organization)
def get_org(org_id: str, user: User = Depends(current_user_local)):
    _get_org_or_404(org_id)
    _require_membership(org_id, user.id)
    # org already fetched but need return
    org = org_repo.get(org_id)
    # mypy: org guaranteed not None due to check above
    assert org is not None
    return org


@router.get("/{org_id}/members", response_model=List[Membership])
def list_members(org_id: str, user: User = Depends(current_user_local)):
    _get_org_or_404(org_id)
    _require_membership(org_id, user.id)
    return org_repo.list_members(org_id)


@router.post("/{org_id}/members", status_code=status.HTTP_201_CREATED, response_model=Membership)
def invite_member(org_id: str, payload: OrgInvite, user: User = Depends(current_user_local)):
    """Invite user by username with role. Only owner/admin allowed per RBAC."""
    org = _get_org_or_404(org_id)
    actor_role = _require_membership(org.id, user.id)

    # RBAC: only owner/admin can invite; check can_invite
    if not can_invite(actor_role, payload.role):
        raise AppError("FORBIDDEN", "Insufficient role to invite.", status=403)

    # lookup target user by username
    target = user_repo.get_by_username(payload.username)
    if not target:
        raise AppError("NOT_FOUND", "User not found.", status=404)

    # already member?
    existing = org_repo.get_member_role(org.id, target.id)
    if existing is not None:
        raise AppError("CONFLICT", "User already a member.", status=409)

    try:
        membership = org_repo.add_member(org.id, target.id, payload.role)
    except ValueError as e:
        msg = str(e)
        if "Organization not found" in msg:
            raise AppError("NOT_FOUND", "Organization not found.", status=404)
        if "already a member" in msg.lower():
            raise AppError("CONFLICT", "User already a member.", status=409)
        raise AppError("VALIDATION_ERROR", msg, status=400)
    return membership


@router.delete("/{org_id}/members/{target_user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(org_id: str, target_user_id: str, user: User = Depends(current_user_local)):
    """Remove member. Owner/admin or self."""
    org = _get_org_or_404(org_id)
    # check org membership for self case: actor may not be member? Self-removal requires being member
    # so fetch both roles; if actor not member and not self => forbidden
    is_self = user.id == target_user_id

    target_role = org_repo.get_member_role(org.id, target_user_id)
    if target_role is None:
        raise AppError("NOT_FOUND", "Membership not found.", status=404)

    if is_self:
        # self-removal always allowed if target exists
        org_repo.remove_member(org.id, target_user_id)
        return None

    # not self: need actor role
    actor_role = org_repo.get_member_role(org.id, user.id)
    if actor_role is None:
        raise AppError("FORBIDDEN", "Not a member of this organization.", status=403)

    if not can_remove(actor_role, target_role, is_self=False):
        raise AppError("FORBIDDEN", "Insufficient role to remove member.", status=403)

    removed = org_repo.remove_member(org.id, target_user_id)
    if not removed:
        raise AppError("NOT_FOUND", "Membership not found.", status=404)
    return None
