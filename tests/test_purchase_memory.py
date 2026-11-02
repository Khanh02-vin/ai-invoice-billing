import io
import os
import random

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from src import app as app_module
from src import purchase_memory
from src.models import Purchase, PurchaseLabelSample, User
from src.routers import payments
from src.store.sql_repository import Database, PurchaseRepository

# PNG giả dùng cho API validate (magic bytes đúng, Pillow không đọc được)
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 32

# Model xuất bằng scripts/export_vision_model.py; test vector thật bỏ qua nếu thiếu.
REAL_MODEL_PATH = purchase_memory._model_path()


def _noise_png(seed: int) -> bytes:
    """Ảnh PNG thật, deterministic theo seed — ảnh khác seed thì dHash khác xa."""
    from PIL import Image

    rng = random.Random(seed)
    img = Image.new("L", (64, 64))
    img.putdata([rng.randrange(256) for _ in range(64 * 64)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def memory_client(monkeypatch, tmp_path):
    db = Database(str(tmp_path / "memory.db"))
    db.create_tables()
    with db.get_session() as session:
        session.add(User(id="owner", username="owner", password_hash="x" * 60, verified=True))
        session.add(User(id="other", username="other", password_hash="y" * 60, verified=True))
        session.commit()
    monkeypatch.setattr(payments, "_get_db", lambda: db)
    # Tắt vector để test API chỉ đo đường hash/OCR, độc lập với model có sẵn hay không.
    monkeypatch.setattr(purchase_memory, "embed", lambda content: None)
    app_module.app.dependency_overrides[payments.current_user] = lambda: User(
        id="owner", username="owner", password_hash="x" * 60, verified=True
    )
    yield TestClient(app_module.app), db
    app_module.app.dependency_overrides.clear()


def _remember(db, *, user_id="owner", content=b"photo", item="Bánh mì"):
    """Tạo đủ dòng cha (user + purchase) vì sample có FK về cả hai."""
    with db.get_session() as session:
        if not session.get(User, user_id):
            session.add(User(id=user_id, username=user_id, password_hash="x" * 60, verified=True))
            session.commit()
    purchase = PurchaseRepository(db).create(Purchase(user_id=user_id, item=item, amount=25000))
    return purchase_memory.remember_purchase(
        db,
        user_id=user_id,
        purchase_id=purchase.id,
        content=content,
        mime_type="image/png",
        item=item,
        category="Ăn uống",
        merchant="Huỳnh Hoa",
    )


def test_dhash_stable_and_distinct():
    a = _noise_png(1)
    assert purchase_memory.dhash(a) == purchase_memory.dhash(a)
    assert purchase_memory.dhash(a) != purchase_memory.dhash(_noise_png(2))
    assert purchase_memory.dhash(b"khong phai anh") is None
    # SQLite INTEGER có dấu: hash phải luôn vừa 63 bit, bất kể ảnh nhiễu thế nào
    for seed in range(20):
        assert purchase_memory.dhash(_noise_png(seed)) < 2**63


def test_norm_strips_vietnamese_diacritics():
    assert purchase_memory._norm("Bánh mì Huỳnh Hoa 25K") == "banh mi huynh hoa 25k"


def test_remember_and_match_same_photo(monkeypatch, tmp_path):
    db = Database(str(tmp_path / "m1.db"))
    db.create_tables()
    monkeypatch.setattr(purchase_memory, "ocr_text", lambda content, mime: "")
    monkeypatch.setattr(purchase_memory, "embed", lambda content: None)
    photo = _noise_png(1)
    sample = _remember(db, content=photo)
    assert sample is not None and sample.image_hash is not None

    matched = purchase_memory.find_similar_purchase(
        db, user_id="owner", content=photo, mime_type="image/png"
    )
    assert matched is not None and matched.id == sample.id
    assert matched.item == "Bánh mì"


def test_match_by_ocr_label_for_new_photo(monkeypatch, tmp_path):
    db = Database(str(tmp_path / "m2.db"))
    db.create_tables()
    monkeypatch.setattr(purchase_memory, "ocr_text", lambda content, mime: "")
    monkeypatch.setattr(purchase_memory, "embed", lambda content: None)
    sample = _remember(db, content=_noise_png(1), item="Bánh mì")
    assert sample is not None

    # Ảnh chụp khác (hash khác xa) nhưng bao bì đọc được cùng nhãn → vẫn trúng.
    monkeypatch.setattr(
        purchase_memory, "ocr_text", lambda content, mime: "QUÁN BÁNH MÌ HUYNH HOA - 25K"
    )
    matched = purchase_memory.find_similar_purchase(
        db, user_id="owner", content=_noise_png(2), mime_type="image/png"
    )
    assert matched is not None and matched.id == sample.id


def test_no_match_for_unrelated_photo_without_text(monkeypatch, tmp_path):
    db = Database(str(tmp_path / "m3.db"))
    db.create_tables()
    monkeypatch.setattr(purchase_memory, "ocr_text", lambda content, mime: "")
    monkeypatch.setattr(purchase_memory, "embed", lambda content: None)
    _remember(db, content=_noise_png(1))
    matched = purchase_memory.find_similar_purchase(
        db, user_id="owner", content=_noise_png(2), mime_type="image/png"
    )
    assert matched is None


def test_memory_is_owner_scoped(monkeypatch, tmp_path):
    db = Database(str(tmp_path / "m4.db"))
    db.create_tables()
    monkeypatch.setattr(purchase_memory, "ocr_text", lambda content, mime: "")
    monkeypatch.setattr(purchase_memory, "embed", lambda content: None)
    _remember(db, user_id="other", content=_noise_png(1))
    matched = purchase_memory.find_similar_purchase(
        db, user_id="owner", content=_noise_png(1), mime_type="image/png"
    )
    assert matched is None


def test_image_draft_prefills_from_history_without_llm(memory_client, monkeypatch):
    client, db = memory_client
    monkeypatch.setattr(purchase_memory, "ocr_text", lambda content, mime: "banh mi huynh hoa")
    _remember(db, content=FAKE_PNG, item="Bánh mì")
    monkeypatch.setattr(payments, "get_llm_provider", lambda: None)

    response = client.post("/payments/image-draft", files={"file": ("dep.png", FAKE_PNG, "image/png")})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "review_required"
    assert data["item"] == "Bánh mì"
    assert data["category"] == "Ăn uống"
    assert data["merchant"] == "Huỳnh Hoa"
    assert data["amount"] is None
    assert "lịch sử" in data["message"]


def test_manual_save_records_sample_and_survives_memory_failure(memory_client, monkeypatch):
    client, db = memory_client
    photo = _noise_png(1)
    created = client.post(
        "/payments/purchases/manual",
        data={"item": "Bánh mì", "category": "Ăn uống", "merchant": "Huỳnh Hoa", "amount": 25000},
        files={"file": ("dep.png", photo, "image/png")},
    )
    assert created.status_code == 201
    with db.get_session() as session:
        samples = session.exec(select(PurchaseLabelSample)).all()
    assert len(samples) == 1
    assert samples[0].item == "Bánh mì"
    assert samples[0].purchase_id == created.json()["id"]

    monkeypatch.setattr(
        purchase_memory, "remember_purchase", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    retry = client.post(
        "/payments/purchases/manual",
        data={"item": "Cà phê", "amount": 15000},
        files={"file": ("dep2.png", _noise_png(3), "image/png")},
    )
    assert retry.status_code == 201


# ---------- Phase B: vector MobileNet ----------


def test_score_cosine_hit_and_miss():
    import numpy as np

    v_a = np.zeros(1024, dtype=np.float32)
    v_a[0] = 1.0
    v_b = np.zeros(1024, dtype=np.float32)
    v_b[1] = 1.0
    # Hướng lệch cosine 0.89 — dưới ngưỡng 0.90 thì không trúng.
    v_close = 0.89 * v_a + np.sqrt(1 - 0.89**2) * v_b
    sample = PurchaseLabelSample(user_id="owner", purchase_id="p1", item="Bánh mì", embedding=v_a.tobytes())

    assert purchase_memory._score(sample, None, "", v_a.tobytes()) == 0.9
    assert purchase_memory._score(sample, None, "", v_close.astype(np.float32).tobytes()) == 0.0
    assert purchase_memory._score(sample, None, "", v_b.tobytes()) == 0.0
    # Mẫu không có vector / ảnh mới không có vector → cosine không góp điểm.
    assert purchase_memory._score(PurchaseLabelSample(user_id="u", purchase_id="p"), None, "", v_a.tobytes()) == 0.0
    assert purchase_memory._score(sample, None, "", None) == 0.0


def test_embed_returns_none_without_model_file(monkeypatch, tmp_path):
    monkeypatch.setenv("VISION_MODEL_PATH", str(tmp_path / "missing.onnx"))
    assert purchase_memory.embed(b"khong phai anh") is None


@pytest.mark.skipif(not os.path.exists(REAL_MODEL_PATH), reason="Cần model ONNX (chạy scripts/export_vision_model.py)")
def test_real_embed_unit_vector_and_rejects_bad_content():
    import numpy as np

    emb = purchase_memory.embed(_noise_png(5))
    assert emb is not None and len(emb) == 4096  # 1024 float32
    assert float(np.linalg.norm(np.frombuffer(emb, dtype=np.float32))) == pytest.approx(1.0, abs=1e-5)
    assert purchase_memory.embed(b"khong phai anh") is None
    assert purchase_memory.embed(FAKE_PNG) is None  # PNG giả Pillow không đọc được


@pytest.mark.skipif(not os.path.exists(REAL_MODEL_PATH), reason="Cần model ONNX (chạy scripts/export_vision_model.py)")
def test_real_model_matches_rephoto_without_hash_or_text(monkeypatch, tmp_path):
    """Cùng ảnh nén lại JPEG — hash khác và không có chữ, chỉ vector trúng là đủ."""
    db = Database(str(tmp_path / "m5.db"))
    db.create_tables()
    monkeypatch.setattr(purchase_memory, "dhash", lambda content: None)
    monkeypatch.setattr(purchase_memory, "ocr_text", lambda content, mime: "")

    photo = _noise_png(1)
    sample = _remember(db, content=photo)
    assert sample is not None and sample.embedding is not None

    buf = io.BytesIO()
    from PIL import Image as PILImage

    PILImage.open(io.BytesIO(photo)).convert("RGB").save(buf, format="JPEG", quality=80)

    matched = purchase_memory.find_similar_purchase(
        db, user_id="owner", content=buf.getvalue(), mime_type="image/jpeg"
    )
    assert matched is not None and matched.id == sample.id
    assert matched.item == "Bánh mì"
