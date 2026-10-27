# Spec: Multi-tenant Organization (đa tổ chức)

> Sửa thiết kế gốc (bản 4 hướng mở rộng): giữ nguyên mục tiêu, sửa **semantics
> query** (lỗi AND user_id + org), bỏ `organization_id` trên
> `push_subscriptions`, và chọn header thay vì subdomain cho phase đầu.

## 1. Mục tiêu

Một "tổ chức" (công ty, nhóm) có nhiều nhân viên dùng chung kho hóa đơn và
giao dịch, trong khi mỗi user vẫn có kho cá nhân riêng. Phase đầu chỉ cần:
tạo org, mời/thêm thành viên, chọn ngữ cảnh org trong app, và mọi query
nghiệp vụ respect ngữ cảnh đó.

## 2. Schema

```sql
CREATE TABLE organizations (
    id            TEXT PRIMARY KEY,          -- uuid hex[:16] (pattern hiện có)
    name          TEXT NOT NULL,
    slug          TEXT NOT NULL UNIQUE,      -- để sau nếu cần URL /org/{slug}
    owner_user_id TEXT NOT NULL REFERENCES users(id),
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE organization_members (
    id              TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role            TEXT NOT NULL DEFAULT 'member',  -- owner|admin|member|viewer
    joined_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (organization_id, user_id)
);

ALTER TABLE invoices               ADD COLUMN organization_id TEXT REFERENCES organizations(id);
ALTER TABLE payment_transactions   ADD COLUMN organization_id TEXT REFERENCES organizations(id);
CREATE INDEX idx_invoices_org              ON invoices(organization_id);
CREATE INDEX idx_payment_transactions_org  ON payment_transactions(organization_id);
```

**Khác thiết kế gốc:** `push_subscriptions` KHÔNG thêm `organization_id`.
Push gắn với user/thiết bị, không gắn với org — khi có sự kiện org, gửi push
cho các thành viên (qua membership), không cần thiết bị biết org.

Dữ liệu cá nhân giữ `organization_id = NULL`. Cột nullable để migration
không bắt buộc backfill; bản ghi cũ tự động là "cá nhân".

## 3. Semantics query (phần sửa chính)

Thiết kế gốc lọc `user_id == user.id AND organization_id == org` — sai vì
thành viên sẽ **không bao giờ** thấy hóa đơn của đồng nghiệp, mâu thuẫn mục
tiêu "dùng chung". Nguyên tắc đúng: **ngữ cảnh quyết định bộ lọc, hai bộ lọc
không cộng nhau**:

| Ngữ cảnh request | Đọc (`GET /invoices`, transactions…) | Ghi (upload, tạo invoice/tx) |
|---|---|---|
| Không `X-Organization-Id` (cá nhân) | `user_id == user.id` (bất kể organization_id — gồm cả bản ghi user từng ghi trong org) | `organization_id = NULL` |
| Có `X-Organization-Id` (org) | `organization_id == org` (bất kể user nào ghi) | `organization_id = org` |

```python
# Repository pattern (đây là phần sửa so với thiết kế gốc — KHÔNG AND 2 điều kiện)
def list(self, user_id: str, organization_id: str | None = None, ...):
    query = select(Invoice)
    if organization_id:
        query = query.where(Invoice.organization_id == organization_id)
    else:
        query = query.where(Invoice.user_id == user_id)
```

Lưu ý phụ:

- Read trong org vẫn phải kiểm **membership** trước (middleware dưới) —
  filter org không bao giờ đứng một mình.
- Update/delete một bản ghi: cho phép nếu (a) `user_id == user.id` (bản ghi
  cá nhân của chính mình), hoặc (b) có org context + user là thành viên org
  của bản ghi, với role >= member. Viewer chỉ đọc.
- `match_invoice_saved` / auto-match chạy **trong cùng org context** của bản
  ghi (chỉ ghép invoice và transaction cùng `organization_id`; NULL chỉ ghép
  với NULL) — tránh ghép chéo tiền công ty với tiền cá nhân.

## 4. Middleware / dependency

Phase đầu dùng **header `X-Organization-Id`** (đơn giản, test được bằng curl).
Subdomain và JWT claim `org_id` để phase sau khi có nhiều domain.

```python
def current_organization(request: Request, user: User = Depends(current_user)) -> str | None:
    """Org context của request; None = ngữ cảnh cá nhân. Raise 403 nếu không
    phải thành viên / 404 nếu org không tồn tại."""
    org_id = request.headers.get("X-Organization-Id")
    if not org_id:
        return None
    membership = org_members_repo.get(org_id, user.id)
    if not membership:
        raise AppError("FORBIDDEN", "Bạn không phải thành viên tổ chức này.", status=403)
    return org_id
```

Route nghiệp vụ thêm `org: str | None = Depends(current_organization)` và
truyền xuống repository như bảng ở mục 3. Không dùng middleware toàn cục —
dependency tường minh per-route đúng kiểu code hiện có (`current_user`).

## 5. Roles & quyền

| Hành động | owner | admin | member | viewer |
|---|---|---|---|---|
| Đọc invoices/transactions/báo cáo org | ✓ | ✓ | ✓ | ✓ |
| Upload/sửa hóa đơn, link giao dịch | ✓ | ✓ | ✓ | ✗ |
| Thêm/xóa thành viên, đổi role | ✓ | ✓ | ✗ | ✗ |
| Xóa org, chuyển ownership | ✓ | ✗ | ✗ | ✗ |

`owner` là member duy nhất không thể bị xóa khỏi org. Chuyển ownership:
owner hiện tại transfer cho 1 admin (phase sau nếu cần).

## 6. API surface

```
POST   /organizations                      {name}            → tạo org (user thành owner)
GET    /organizations                                        → các org user thuộc về
DELETE /organizations/{org_id}                               → chỉ owner
GET    /organizations/{org_id}/members                      → admin+
POST   /organizations/{org_id}/members     {username, role}  → admin+ (mời user đã tồn tại)
PATCH  /organizations/{org_id}/members/{user_id}  {role}    → admin+
DELETE /organizations/{org_id}/members/{user_id}           → admin+ (không xóa owner)
```

Quản lý org luôn qua JWT (không nhận X-API-Key — cùng pattern `api_keys`).
Frontend: dropdown chọn ngữ cảnh ở Sidebar, lưu `org_id` vào state + header
mọi request qua `api()`; trang quản lý thành viên.

## 7. Tác động lan

- **Worker extraction** (`_do_extract`): nhận thêm `organization_id` khi
  enqueue (celery task signature thêm 1 kwarg, backward-compatible).
- **API keys**: phase sau có thể thêm scope `org:read/write` + tạo key
  "thuộc org" (cột `organization_id` trên `api_keys`). Hiện tại key thao tác
  dữ liệu cá nhân của owner key — không thấy dữ liệu org của người khác.
- **Quota theo org** (số hóa đơn/tháng): để phase sau, đếm từ
  `invoices WHERE organization_id = ? AND created_at >= tháng này`.

## 8. Migration & triển khai

1. Thêm models `Organization`, `OrganizationMember` vào `src/models.py`;
   `organization_id` vào `Invoice`/`PaymentTransaction` (nullable).
2. `SQLModel.metadata.create_all` tự tạo bảng mới; SQLite
   `ALTER TABLE ... ADD COLUMN` cho bảng cũ (script migration trong
   `scripts/`, chạy idempotent — check pragma table_info trước khi add).
3. Repository: thêm tham số `organization_id` vào `list/get/update/delete`
   theo bảng semantics ở mục 3; route thêm `Depends(current_organization)`.
4. Test: ngữ cảnh org thấy hóa đơn của thành viên khác (case mà thiết kế
   gốc làm sai); viewer không ghi được; non-member 403; auto-match không
   ghép chéo org/cá nhân.
