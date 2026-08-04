"""FastAPI app cho Invoice & Billing System. Auth JWT + đa người dùng."""
import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles

from .domain.models import (
    Invoice, InvoiceCreate, InvoiceUpdate, MonthlyReport, InvoiceStatus,
    User, UserCreate, UserPublic, Token,
)
from .extract.extractor import extract_invoice
from .store.repository import InvoiceRepository
from .store.users import UserRepository
from .auth.security import hash_password, verify_password, create_token, decode_token

app = FastAPI(title="Invoice & Billing System", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

repo = InvoiceRepository()
users = UserRepository()
bearer = HTTPBearer(auto_error=False)


def current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> User:
    """Lấy user từ JWT. 401 nếu thiếu/sai token."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Cần đăng nhập")
    user_id = decode_token(credentials.credentials)
    user = users.get(user_id) if user_id else None
    if not user:
        raise HTTPException(status_code=401, detail="Token không hợp lệ")
    return user


# ---------- Auth ----------
# Đăng ký mặc định TẮT khi deploy (ai có link public đều gọi được API).
# Mở bằng env OPEN_REGISTRATION=1 khi cần tạo tài khoản mới.

@app.post("/auth/register", response_model=Token)
async def register(data: UserCreate):
    import os
    if not os.getenv("OPEN_REGISTRATION"):
        raise HTTPException(status_code=403, detail="Đăng ký đã tắt — liên hệ quản trị viên")
    """Đăng ký tài khoản mới."""
    if users.get_by_username(data.username):
        raise HTTPException(status_code=400, detail="Tên người dùng đã tồn tại")
    user = users.create(data.username, hash_password(data.password))
    return Token(access_token=create_token(user.id))


@app.post("/auth/login", response_model=Token)
async def login(data: UserCreate):
    """Đăng nhập, trả về JWT."""
    user = users.get_by_username(data.username)
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Sai tên đăng nhập hoặc mật khẩu")
    return Token(access_token=create_token(user.id))


@app.get("/auth/me", response_model=UserPublic)
async def me(user: User = Depends(current_user)):
    """Thông tin user hiện tại."""
    return UserPublic(id=user.id, username=user.username, created_at=user.created_at)


# ---------- Invoices (yêu cầu đăng nhập) ----------

@app.post("/invoices/upload", response_model=Invoice)
async def upload_invoice(file: UploadFile = File(...), user: User = Depends(current_user)):
    """Upload file hóa đơn (PDF/text) → trích xuất → lưu cho user."""
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        f.write(await file.read())
        temp_path = f.name
    try:
        invoice = extract_invoice(temp_path)
        invoice.user_id = user.id
        repo.upsert(invoice)
        return invoice
    finally:
        Path(temp_path).unlink()


@app.post("/invoices", response_model=Invoice)
async def create_invoice(data: InvoiceCreate, user: User = Depends(current_user)):
    """Tạo hóa đơn thủ công."""
    import hashlib
    invoice = Invoice(
        id=hashlib.md5(f"{data.invoice_number}|{user.id}".encode()).hexdigest()[:12],
        user_id=user.id,
        **data.model_dump(),
    )
    return repo.upsert(invoice)


@app.get("/invoices", response_model=List[Invoice])
async def list_invoices(
    status: Optional[InvoiceStatus] = None, limit: int = 100, user: User = Depends(current_user)
):
    """Liệt kê hóa đơn của user, lọc theo status tùy chọn."""
    return repo.list(user_id=user.id, status=status, limit=limit)


@app.get("/invoices/{invoice_id}", response_model=Invoice)
async def get_invoice(invoice_id: str, user: User = Depends(current_user)):
    """Lấy hóa đơn theo id (chỉ hóa đơn của user)."""
    invoice = repo.get(invoice_id, user.id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Không tìm thấy hóa đơn")
    return invoice


@app.patch("/invoices/{invoice_id}", response_model=Invoice)
async def update_invoice(invoice_id: str, changes: InvoiceUpdate, user: User = Depends(current_user)):
    """Cập nhật hóa đơn (vd: đánh dấu paid)."""
    invoice = repo.update(invoice_id, changes, user.id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Không tìm thấy hóa đơn")
    return invoice


@app.delete("/invoices/{invoice_id}")
async def delete_invoice(invoice_id: str, user: User = Depends(current_user)):
    """Xóa hóa đơn của user."""
    if not repo.delete(invoice_id, user.id):
        raise HTTPException(status_code=404, detail="Không tìm thấy hóa đơn")
    return {"deleted": invoice_id}


@app.get("/reports/monthly/{period}", response_model=MonthlyReport)
async def monthly_report(period: str, user: User = Depends(current_user)):
    """Báo cáo theo tháng của user. period = YYYY-MM."""
    report = repo.monthly_report(period, user.id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Không có hóa đơn tháng {period}")
    return report


@app.get("/health")
async def health():
    """Kiểm tra server."""
    return {"status": "healthy", "version": "2.0.0"}


# Mount UI sau cùng — StaticFiles chặn hết nếu đặt trước routes.
# Ưu tiên dist React (build), fallback static cũ.
_dist = Path(__file__).parent.parent / "frontend" / "dist"
_static_dir = Path(__file__).parent / "static"
ui_dir = _dist if _dist.exists() else (_static_dir if _static_dir.exists() else None)
if ui_dir:
    app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
