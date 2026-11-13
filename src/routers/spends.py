"""Ghi một lần chi tiêu tại địa điểm, xem lịch sử, xoá, và đọc số tiền từ QR."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..api.deps import current_user, place_repo, spend_draft_repo, spend_repo
from ..errors import AppError
from ..geo import normalize_name
from ..models import Spend, SpendDraft, User
from ..qr import parse_qr_text

router = APIRouter(prefix="/spends", tags=["spends"])

SPEND_LABEL = "mức chi ghi nhận"


class SpendCreate(BaseModel):
    place_id: str = Field(min_length=1, max_length=32)
    amount: int = Field(gt=0, le=100_000_000)
    party_size: int = Field(default=1, ge=1, le=50)
    occurred_at: datetime | None = None
    note: str = Field(default="", max_length=200)


class SpendDraftCreate(BaseModel):
    amount: int = Field(gt=0, le=100_000_000)
    party_size: int = Field(default=1, ge=1, le=50)
    merchant_name: str = Field(default="", max_length=120)
    bank_bin: str = Field(default="", max_length=6)
    account: str = Field(default="", max_length=32)
    place_id: str | None = None
    lat: float | None = None
    lon: float | None = None
    note: str = Field(default="", max_length=200)


class SpendDraftConfirm(BaseModel):
    category: str = Field(default="other", max_length=32)
    place_name: str | None = Field(default=None, max_length=120)
    address: str = Field(default="", max_length=200)
    note: str | None = Field(default=None, max_length=200)


class QrParseRequest(BaseModel):
    text: str = Field(default="", max_length=4000)


def _payload(spend: Spend, place) -> dict:
    return {
        "id": spend.id,
        "place_id": spend.place_id,
        "place_name": place.name if place else "",
        "category": place.category if place else "",
        "amount": spend.amount,
        "party_size": spend.party_size,
        "spend_per_person": spend.spend_per_person,
        "occurred_at": spend.occurred_at.isoformat(),
        "note": spend.note,
        "spend_label": SPEND_LABEL,
    }


@router.post("")
def create_spend(body: SpendCreate, user: User = Depends(current_user)) -> dict:
    place = place_repo().get(body.place_id)
    if place is None:
        raise AppError("PLACE_NOT_FOUND", "Chưa có địa điểm này.", status=404)

    spend = spend_repo().create(
        user_id=user.id,
        place_id=place.id,
        amount=body.amount,
        party_size=body.party_size,
        occurred_at=body.occurred_at,
        note=body.note.strip(),
    )
    return {"spend": _payload(spend, place)}


@router.get("")
def list_spends(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    user: User = Depends(current_user),
) -> dict:
    if start and end and start > end:
        raise AppError("INVALID_DATE_RANGE", "Khoảng ngày không hợp lệ.", status=400)

    places = {place.id: place for place in place_repo().search()}
    rows = [_payload(spend, places.get(spend.place_id)) for spend in spend_repo().list(user.id, start, end)]
    return {"spends": rows, "spend_label": SPEND_LABEL}


@router.delete("/{spend_id}")
def delete_spend(spend_id: str, user: User = Depends(current_user)) -> dict:
    repo = spend_repo()
    spend = repo.get(spend_id)
    if spend is None:
        raise AppError("SPEND_NOT_FOUND", "Không tìm thấy lần chi này.", status=404)
    if spend.user_id != user.id:
        raise AppError("FORBIDDEN", "Đây không phải lần chi của bạn.", status=403)
    repo.delete(user.id, spend_id)
    return {"deleted": True}


@router.post("/parse-qr")
def parse_qr(body: QrParseRequest) -> dict:
    """Đọc số tiền/số người từ QR. Không đọc được → ``amount: null`` (client tự nhập)."""
    return parse_qr_text(body.text)


def _draft_payload(draft: SpendDraft) -> dict:
    return {
        "id": draft.id,
        "amount": draft.amount,
        "party_size": draft.party_size,
        "merchant_name": draft.merchant_name,
        "bank_bin": draft.bank_bin,
        "account": draft.account,
        "place_id": draft.place_id,
        "lat": draft.lat,
        "lon": draft.lon,
        "note": draft.note,
        "created_at": draft.created_at.isoformat(),
    }


@router.post("/drafts")
def create_draft(body: SpendDraftCreate, user: User = Depends(current_user)) -> dict:
    draft = spend_draft_repo().create(
        user_id=user.id,
        amount=body.amount,
        party_size=body.party_size,
        merchant_name=body.merchant_name.strip(),
        bank_bin=body.bank_bin.strip(),
        account=body.account.strip(),
        place_id=body.place_id,
        lat=body.lat,
        lon=body.lon,
        note=body.note.strip(),
    )
    return {"draft": _draft_payload(draft)}


@router.get("/drafts")
def list_drafts(user: User = Depends(current_user)) -> dict:
    return {"drafts": [_draft_payload(d) for d in spend_draft_repo().list(user.id)]}


@router.delete("/drafts/{draft_id}")
def delete_draft(draft_id: str, user: User = Depends(current_user)) -> dict:
    if not spend_draft_repo().delete(user.id, draft_id):
        raise AppError("DRAFT_NOT_FOUND", "Không tìm thấy giao dịch tạm.", status=404)
    return {"deleted": True}


@router.post("/drafts/{draft_id}/confirm")
def confirm_draft(draft_id: str, body: SpendDraftConfirm, user: User = Depends(current_user)) -> dict:
    drepo = spend_draft_repo()
    draft = drepo.get(draft_id)
    if draft is None:
        raise AppError("DRAFT_NOT_FOUND", "Không tìm thấy giao dịch tạm.", status=404)
    if draft.user_id != user.id:
        raise AppError("FORBIDDEN", "Đây không phải giao dịch của bạn.", status=403)

    prepo = place_repo()
    place = prepo.get(draft.place_id) if draft.place_id else None
    if place is None:
        place_name = (body.place_name or draft.merchant_name or "Địa điểm chưa rõ").strip()
        normalized = normalize_name(place_name)
        place = prepo.find_match(normalized, draft.lat, draft.lon)
        if place is None:
            place = prepo.create(
                name=place_name,
                normalized_name=normalized,
                category=body.category or "other",
                address=body.address.strip(),
                lat=draft.lat,
                lon=draft.lon,
                created_by=user.id,
            )

    spend = spend_repo().create(
        user_id=user.id,
        place_id=place.id,
        amount=draft.amount,
        party_size=draft.party_size,
        note=(body.note if body.note is not None else draft.note).strip(),
    )
    drepo.delete(user.id, draft_id)
    return {"spend": _payload(spend, place)}
