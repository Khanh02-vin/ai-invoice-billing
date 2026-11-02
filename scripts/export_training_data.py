"""Xuất dataset ảnh đã gán nhãn từ DB để fine-tune model vision (phase C).

Đọc purchase_label_samples JOIN purchases (chỉ ĐỌC, sqlite mode=ro — không đụng
gì vào DB), chép ảnh từ storage ra `training_data/<item-slug>/<stt>.<ext>`.
Mỗi thư mục con là 1 lớp theo tên món ("Bánh mì" và "bánh mì " gộp chung).
Ảnh hỏng hoặc thiếu file bị bỏ qua và được đếm in ra.

Chạy trên máy có DB, rồi nén thư mục upload lên Colab/máy train:
    python scripts/export_training_data.py                # dùng DATABASE_PATH/STORAGE_DIR
    zip -r training_data.zip training_data/
Ở nơi train: python scripts/finetune_vision_model.py --data training_data/ --out mobilenetv3_ft.onnx

Ảnh người dùng — KHÔNG commit training_data/ (đã có trong .gitignore).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
from collections import Counter
from pathlib import Path

from slugify import slugify

_EXT_BY_MIME = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _image_ok(content: bytes) -> bool:
    """Pillow đọc được thật sự (chặn ảnh hỏng làm sập ImageFolder lúc train)."""
    try:
        import io

        from PIL import Image

        Image.open(io.BytesIO(content)).verify()
        return True
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.getenv("DATABASE_PATH", "invoices.db"))
    parser.add_argument("--storage", default=os.getenv("STORAGE_DIR", "data/storage"))
    parser.add_argument("--out", default="training_data")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        raise SystemExit(f"Không thấy DB: {args.db}")

    # mode=ro: driver từ chối mọi ghi — script không thể lỡ tay sửa DB.
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows = conn.execute(
        """
        SELECT s.item, p.source_file, p.mime_type
        FROM purchase_label_samples s JOIN purchases p ON p.id = s.purchase_id
        WHERE s.item != '' AND p.source_file != ''
        """
    ).fetchall()
    conn.close()

    storage = Path(args.storage)
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    per_class: Counter[str] = Counter()
    skipped = 0
    for item, source_file, mime_type in rows:
        ext = _EXT_BY_MIME.get(mime_type)
        if ext is None:
            skipped += 1
            continue
        path = storage / source_file
        if not path.exists():
            skipped += 1
            continue
        content = path.read_bytes()
        if not _image_ok(content):
            skipped += 1
            continue
        class_dir = out / slugify(item)
        class_dir.mkdir(exist_ok=True)
        (class_dir / f"{per_class[class_dir.name] + 1}{ext}").write_bytes(content)
        per_class[class_dir.name] += 1

    print(f"Đã xuất {sum(per_class.values())} ảnh / {len(per_class)} lớp vào {out}/")
    for name, count in per_class.most_common():
        print(f"  {name}: {count} ảnh")
    if skipped:
        print(f"Bỏ qua {skipped} mẫu (ảnh hỏng/thiếu/không đúng định dạng ảnh)")
    if len(per_class) < 2:
        print("⚠️  Cần ít nhất 2 món khác nhau để fine-tune có nghĩa.")


if __name__ == "__main__":
    main()
