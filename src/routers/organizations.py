"""Quản lý Organization (multi-tenant) — chỉ JWT, không nhận X-API-Key.

Ngữ cảnh org khi gọi nghiệp vụ: header `X-Organization-Id` (xem
docs/multi-tenant-spec.md). Routes ở đây chỉ quản lý org + thành viên.
"""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..auth.security import decode_token
from ..config import get_settings
from ..errors import AppError
from ..models import (
    MemberAdd,
    MemberPublic,
    MemberRoleUpdate,
    ORG_ROLES,
    OrganizationCreate,
    OrganizationPublic,
    User,
)
from ..store.sql_repository import (
    Database,
    OrganizationRepository,
    UserRepository,
    get_database,
)

router = APIRouter(prefix="/organizations", tags=["organizations"])
bearer = HTTPBearer(auto_error=False)


def _get_org_repo() -> OrganizationRepository:
    try:
        from .. import app as app_module

        repo = getattr(app_module, "orgs_repo", None)
        if repo is not None:
            return repo
    except Exception:
        pass
    return OrganizationRepository(get_database(get_settings().database_path))


def _get_users_repo() -> UserRepository:
    try:
        from .. import app as app_module

        repo = getattr(app_module, "users", None)
        if repo is not None:
            return repo
    except Exception:
        pass
    return UserRepository(get_database(get_settings().database_path))


def current_user_jwt(
    request: Request, credentials: HTTPAuthorizationCredentials = Depends(bearer)
) -> User:
    """Quản lý org chỉ cho JWT bearer — API key không quản lý org."""
    if (request.headers.get("X-API-Key") or "").strip():
        raise AppError("FORBIDDEN", "API key không được quản lý tổ chức.", status=403)
    if not credentials:
        raise AppError("UNAUTHORIZED", "Cần đăng nhập.", status=401)
    user_id = decode_token(credentials.credentials)
    user = _get_users_repo().get(user_id) if user_id else None
    if not user:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
    if not user.verified:
        raise AppError("EMAIL_NOT_VERIFIED", "Vui lòng xác minh email trước.", status=403)
    return user


def _require_membership(org_id: str, user: User, admin_only: bool = False):
    repo = _get_org_repo()
    if not repo.get(org_id):
        raise AppError("NOT_FOUND", "Không tìm thấy tổ chức.", status=404)
    membership = repo.get_membership(org_id, user.id)
    if not membership:
        raise AppError("FORBIDDEN", "Bạn không phải thành viên tổ chức này.", status=403)
    if admin_only and membership.role not in ("owner", "admin"):
        raise AppError("FORBIDDEN", "Chỉ owner/admin mới được thao tác này.", status=403)
    return membership


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "org"
    return f"{slug}-{uuid.uuid4().hex[:6]}"


def _org_public(org, role: str) -> OrganizationPublic:
    return OrganizationPublic(
        id=org.id, name=org.name, slug=org.slug, role=role, created_at=org.created_at
    )


@router.post("", response_model=OrganizationPublic)
def create_organization(body: OrganizationCreate, user: User = Depends(current_user_jwt)):
    org, _member = _get_org_repo().create(body.name, _slugify(body.name), user.id)
    return _org_public(org, "owner")


@router.get("", response_model=list[OrganizationPublic])
def list_my_organizations(user: User = Depends(current_user_jwt)):
    return [_org_public(org, role) for org, role in _get_org_repo().list_for_user(user.id)]


@router.delete("/{org_id}")
def delete_organization(org_id: str, user: User = Depends(current_user_jwt)):
    membership = _require_membership(org_id, user)
    if membership.role != "owner":
        raise AppError("FORBIDDEN", "Chỉ owner mới được xóa tổ chức.", status=403)
    if not _get_org_repo().delete(org_id):
        raise AppError("NOT_FOUND", "Không tìm thấy tổ chức.", status=404)
    return {"deleted": True}


@router.get("/{org_id}/members", response_model=list[MemberPublic])
def list_members(org_id: str, user: User = Depends(current_user_jwt)):
    _require_membership(org_id, user)
    return [MemberPublic(**m) for m in _get_org_repo().list_members(org_id)]


@router.post("/{org_id}/members", response_model=MemberPublic)
def add_member(org_id: str, body: MemberAdd, user: User = Depends(current_user_jwt)):
    _require_membership(org_id, user, admin_only=True)
    if body.role not in ("admin", "member", "viewer"):
        raise AppError(
            "INVALID_ROLE", "Role hợp lệ khi thêm thành viên: admin, member, viewer.", status=400
        )
    target = _get_users_repo().get_by_username(body.username)
    if not target:
        raise AppError("NOT_FOUND", "Không tìm thấy người dùng này.", status=404)
    repo = _get_org_repo()
    if repo.get_membership(org_id, target.id):
        raise AppError("MEMBER_EXISTS", "Người này đã là thành viên.", status=409)
    member = repo.add_member(org_id, target.id, body.role)
    return MemberPublic(
        user_id=target.id, username=target.username, role=member.role, joined_at=member.joined_at
    )


@router.patch("/{org_id}/members/{user_id}", response_model=MemberPublic)
def update_member_role(
    org_id: str, user_id: str, body: MemberRoleUpdate, user: User = Depends(current_user_jwt)
):
    _require_membership(org_id, user, admin_only=True)
    if body.role not in ORG_ROLES:
        raise AppError("INVALID_ROLE", f"Role hợp lệ: {', '.join(ORG_ROLES)}.", status=400)
    repo = _get_org_repo()
    target = repo.get_membership(org_id, user_id)
    if not target:
        raise AppError("NOT_FOUND", "Không tìm thấy thành viên.", status=404)
    if target.role == "owner":
        raise AppError("FORBIDDEN", "Không đổi được role của owner.", status=403)
    if body.role == "owner":
        raise AppError("FORBIDDEN", "Chuyển ownership chưa hỗ trợ.", status=400)
    repo.update_role(org_id, user_id, body.role)
    members = {m["user_id"]: m for m in repo.list_members(org_id)}
    m = members[user_id]
    return MemberPublic(**m)


@router.delete("/{org_id}/members/{user_id}")
def remove_member(org_id: str, user_id: str, user: User = Depends(current_user_jwt)):
    _require_membership(org_id, user, admin_only=True)
    repo = _get_org_repo()
    target = repo.get_membership(org_id, user_id)
    if not target:
        raise AppError("NOT_FOUND", "Không tìm thấy thành viên.", status=404)
    if target.role == "owner":
        raise AppError("FORBIDDEN", "Không xóa được owner.", status=403)
    repo.remove_member(org_id, user_id)
    return {"removed": True}
