"""Bộ nhớ nhận diện mua hàng — học một lần từ nhãn người dùng đã xác nhận.

Mỗi purchase có ảnh được lưu một mẫu đặc trưng (dHash 64-bit + chữ OCR trên
bao bì + vector MobileNetV3 1024 chiều). `/payments/image-draft` tra cứu mẫu
của chính user đó TRƯỚC khi gọi LLM: trùng hash, trùng chữ hoặc trúng vector
là điền lại item/category/merchant ngay, không cần mạng hay API key.

Vector chạy qua onnxruntime trên file model xuất sẵn (models/mobilenetv3_embed.onnx,
xuất bằng scripts/export_vision_model.py). Thiếu Pillow/pytesseract/onnxruntime
hoặc file model thì đặc trưng tương ứng rỗng và tính năng tự tắt, không gây lỗi.
"""
from __future__ import annotations

import io
import os
import re
import unicodedata
from difflib import SequenceMatcher

from sqlmodel import select

from src.models import PurchaseLabelSample
from src.store.sql_repository import Database

# 2 ảnh chụp lại gần như y hệt nhau chênh nhau < 10/64 bit; ảnh khác thường > 25.
_HASH_MAX_DISTANCE = 10
# Chữ đọc được trùng ≥ 85% (hoặc nhãn đã lưu xuất hiện trong chữ mới) coi là cùng món.
_TEXT_RATIO = 0.85
# Bỏ nhãn ngắn hơn 4 ký tự sau chuẩn hoá ("bún", "ốp") để tránh trùng nhầm.
_MIN_LABEL_LEN = 4
_SCORE_THRESHOLD = 0.85
# Vector trúng ≥ 0.90. Thực đo: cùng ảnh nén/thu phóng lại ≥ 0.99; ảnh khác nhau
# median ~0.70, cặp khác nhau "giống nhất" ~0.88. Nhiễu thuần là ngoại lệ (0.94+)
# vì ngoài phân phối — ảnh chụp thật không rơi vào vùng đó.
_COSINE_THRESHOLD = 0.90


def dhash(content: bytes) -> int | None:
    """dHash 64-bit: so 8 cặp pixel ngang trên ảnh xám 9x8. Ảnh lỗi → None."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(content)).convert("L").resize((9, 8), Image.LANCZOS)
    except Exception:
        return None
    pixels = img.tobytes()  # chế độ "L" → 72 byte, mỗi byte một mức xám
    bits = 0
    for row in range(8):
        base = row * 9
        for col in range(8):
            bits = (bits << 1) | (1 if pixels[base + col] > pixels[base + col + 1] else 0)
    # SQLite INTEGER là 64-bit có dấu: bỏ bit cao để hash luôn lưu được
    return bits & ((1 << 63) - 1)


def ocr_text(content: bytes, mime_type: str) -> str:
    """Chữ đọc được trên ảnh — dùng lại OCR sẵn có của extract (paddle → tesseract)."""
    try:
        from .extract.extractor import _extract_text

        return _extract_text(content, mime_type)
    except Exception:
        return ""


# ---------- Vector MobileNetV3 qua onnxruntime ----------

_SESSION = None


def _model_path() -> str:
    custom = os.getenv("VISION_MODEL_PATH")
    if custom:
        return custom
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "models", "mobilenetv3_embed.onnx")


def _session():
    """InferenceSession dùng chung; None nếu thiếu file model hoặc onnxruntime."""
    global _SESSION
    path = _model_path()
    if not os.path.exists(path):
        return None
    if _SESSION is None:
        try:
            import onnxruntime

            _SESSION = onnxruntime.InferenceSession(path, providers=["CPUExecutionProvider"])
        except Exception:
            return None
    return _SESSION


def _preprocess(content: bytes):
    """Ảnh bytes → tensor (1, 3, 224, 224) đúng chuẩn ImageNet của torchvision."""
    import numpy as np
    from PIL import Image

    img = Image.open(io.BytesIO(content)).convert("RGB")
    img.thumbnail((1024, 1024))  # chặn ảnh khổng lồ tốn RAM
    w, h = img.size
    scale = 256.0 / min(w, h)
    img = img.resize((round(w * scale), round(h * scale)), Image.BICUBIC)
    w, h = img.size
    left, top = (w - 224) // 2, (h - 224) // 2
    img = img.crop((left, top, left + 224, top + 224))
    arr = np.asarray(img, dtype=np.float32) / 255.0
    arr = (arr - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array(
        [0.229, 0.224, 0.225], dtype=np.float32
    )
    return arr.transpose(2, 0, 1)[None].astype(np.float32)


def embed(content: bytes) -> bytes | None:
    """Vector đặc trưng đã chuẩn hoá đơn vị (1024 float32 ≈ 4 KB); None nếu không khả dụng."""
    try:
        session = _session()
        if session is None:
            return None
        vector = session.run(None, {"image": _preprocess(content)})[0][0]

        import numpy as np

        vector = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if norm <= 0:
            return None
        return (vector / norm).tobytes()
    except Exception:
        return None


def _cosine(a: bytes, b: bytes) -> float:
    """Cosine của 2 vector đã chuẩn hoá — chỉ là dot product."""
    import numpy as np

    return float(np.dot(np.frombuffer(a, dtype=np.float32), np.frombuffer(b, dtype=np.float32)))


def _norm(text: str) -> str:
    """Thường + bỏ dấu tiếng Việt + chỉ giữ chữ/số — "Bánh mì" → "banh mi"."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", stripped).strip()


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _score(
    sample: PurchaseLabelSample, image_hash: int | None, text: str, embedding: bytes | None = None
) -> float:
    """Điểm giống nhau giữa ảnh mới và một mẫu đã lưu; 0.0 = không giống."""
    score = 0.0
    if image_hash is not None and sample.image_hash is not None:
        distance = _hamming(image_hash, sample.image_hash)
        if distance <= _HASH_MAX_DISTANCE:
            score = max(score, 0.9 + (_HASH_MAX_DISTANCE - distance) / 100)
    if text:
        new_norm = _norm(text)
        for label, weight in ((sample.item, 0.95), (sample.merchant, 0.85), (sample.ocr_text, 0.85)):
            label_norm = _norm(label)
            if len(label_norm) < _MIN_LABEL_LEN:
                continue
            if label_norm in new_norm or SequenceMatcher(None, new_norm, label_norm).ratio() >= _TEXT_RATIO:
                score = max(score, weight)
    if embedding is not None and sample.embedding:
        try:
            if _cosine(embedding, sample.embedding) >= _COSINE_THRESHOLD:
                score = max(score, 0.9)
        except Exception:
            pass
    return score


def remember_purchase(
    db: Database,
    *,
    user_id: str,
    purchase_id: str,
    content: bytes,
    mime_type: str,
    item: str,
    category: str,
    merchant: str,
) -> PurchaseLabelSample | None:
    """Lưu mẫu đặc trưng cho purchase vừa được xác nhận. Không trích được gì → không lưu."""
    image_hash = dhash(content)
    text = ocr_text(content, mime_type)
    vector = embed(content)
    if image_hash is None and not text and vector is None:
        return None
    sample = PurchaseLabelSample(
        user_id=user_id,
        purchase_id=purchase_id,
        item=item,
        category=category,
        merchant=merchant,
        ocr_text=text,
        image_hash=image_hash,
        embedding=vector,
    )
    with db.get_session() as session:
        session.add(sample)
        session.commit()
        session.refresh(sample)
        return sample


def find_similar_purchase(
    db: Database, *, user_id: str, content: bytes, mime_type: str
) -> PurchaseLabelSample | None:
    """Mẫu của user trùng hash, chữ hoặc vector với ảnh mới; None nếu không trúng ngưỡng."""
    with db.get_session() as session:
        samples = session.exec(
            select(PurchaseLabelSample)
            .where(PurchaseLabelSample.user_id == user_id)
            .order_by(PurchaseLabelSample.created_at.desc())
            .limit(500)
        ).all()
    if not samples:
        return None
    image_hash = dhash(content)
    text = ocr_text(content, mime_type)
    embedding = embed(content)
    if image_hash is None and not text and embedding is None:
        return None
    best: PurchaseLabelSample | None = None
    best_score = _SCORE_THRESHOLD
    for sample in samples:
        s = _score(sample, image_hash, text, embedding)
        if s > best_score:
            best, best_score = sample, s
    return best
