"""Fine-tune embedding MobileNetV3 trên dataset ảnh người dùng đã gán nhãn (phase C).

Chạy nơi có torch + torchvision (Colab khuyến nghị GPU, CPU vẫn được):
    python scripts/finetune_vision_model.py --data training_data/ --out mobilenetv3_ft.onnx

Cách học: gắn tạm head phân loại theo món, train với augment mạnh (crop, màu,
lật, xoay) để vector bất biến với góc chụp/ánh sáng, rồi BỎ head — chỉ giữ
backbone xuất ONNX cùng signature model gốc (image Nx3x224x224 → embedding
Nx1024). App không đổi gì: vẫn onnxruntime + cosine. Dữ liệu ít (thậm chí 1
ảnh/món) vẫn chạy được vì mỗi epoch augment lại ra mẫu dương mới.

Cuối chạy in cosine nội-lớp vs ngoại-lớp trước/sau để đối chiếu có tiến bộ.
torch chỉ cần ở bước này — app chạy với onnxruntime như cũ.
"""

from __future__ import annotations

import argparse
import os
import sys

import torch
from torch import nn
from torch.utils.data import DataLoader
from torchvision import datasets, transforms

# Dùng lại kiến trúc + hàm xuất ONNX của script gốc để hai nơi không trôi signature.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from export_vision_model import build_embedder, export_to_onnx  # noqa: E402

IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]


def train_transform() -> transforms.Compose:
    """Augment mạnh — "chụp lại khác góc" được mô phỏng bằng crop/màu/lật/xoay."""
    return transforms.Compose(
        [
            transforms.RandomResizedCrop(224, scale=(0.6, 1.0)),
            transforms.ColorJitter(brightness=0.3, contrast=0.3, saturation=0.3),
            transforms.RandomHorizontalFlip(),
            transforms.RandomRotation(10),
            transforms.ToTensor(),
            transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
        ]
    )


def eval_transform() -> transforms.Compose:
    """Đúng chuẩn preprocessing lúc chạy app (resize 256 → crop 224 → normalize)."""
    return transforms.Compose(
        [
            transforms.Resize(256),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
        ]
    )


@torch.no_grad()
def _cosine_report(model: nn.Module, dataset: datasets.ImageFolder) -> tuple[float, float]:
    """(cosine nội-lớp trung bình, cosine ngoại-lớp trung bình) trên dataset."""
    device = next(model.parameters()).device
    loader = DataLoader(dataset, batch_size=32)
    vectors, labels = [], []
    for images, ys in loader:
        vectors.append(model(images.to(device)).flatten(1))
        labels.append(ys)
    vectors = torch.cat(vectors)
    vectors = vectors / vectors.norm(dim=1, keepdim=True)
    labels = torch.cat(labels)
    sims = vectors @ vectors.T
    same = labels[:, None] == labels[None, :]
    eye = torch.eye(len(labels), dtype=torch.bool, device=device)
    intra = sims[same & ~eye].mean().item() if (same & ~eye).any() else float("nan")
    inter = sims[~same].mean().item() if (~same).any() else float("nan")
    return intra, inter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, help="Thư mục dataset (mỗi món 1 thư mục con)")
    parser.add_argument("--out", default="mobilenetv3_ft.onnx")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=3e-4)
    args = parser.parse_args()

    train_set = datasets.ImageFolder(args.data, transform=train_transform())
    eval_set = datasets.ImageFolder(args.data, transform=eval_transform())
    classes = train_set.classes
    if len(classes) < 2:
        raise SystemExit(f"Cần ≥ 2 món để fine-tune, dataset chỉ có: {classes}")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    embedder = build_embedder().to(device)
    head = nn.Linear(1024, len(classes)).to(device)  # head chỉ để định hình không gian vector
    full = nn.Sequential(embedder, head)

    base_intra, base_inter = _cosine_report(embedder.eval().cpu(), eval_set)
    embedder.to(device)
    print(f"{len(train_set)} ảnh / {len(classes)} món trên {device}. "
          f"Trước train: cosine nội-lớp {base_intra:.3f} vs ngoại-lớp {base_inter:.3f}")

    loader = DataLoader(train_set, batch_size=args.batch_size, shuffle=True)
    optimizer = torch.optim.AdamW(full.parameters(), lr=args.lr)
    for epoch in range(args.epochs):
        full.train()
        total_loss = 0.0
        for images, labels in loader:
            optimizer.zero_grad()
            loss = nn.functional.cross_entropy(full(images.to(device)), labels.to(device))
            loss.backward()
            optimizer.step()
            total_loss += loss.item() * len(labels)
        if (epoch + 1) % 10 == 0 or epoch == 0:
            print(f"  epoch {epoch + 1}/{args.epochs}: loss {total_loss / len(train_set):.4f}")

    export_to_onnx(embedder.eval().cpu(), args.out)
    intra, inter = _cosine_report(embedder.eval(), eval_set)
    print(f"Đã xuất {args.out} ({os.path.getsize(args.out) / 1e6:.1f} MB)")
    print(f"Sau train: cosine nội-lớp {intra:.3f} (trước {base_intra:.3f}) "
          f"vs ngoại-lớp {inter:.3f} (trước {base_inter:.3f})")
    print("Thay model: chép đè models/mobilenetv3_embed.onnx (hoặc đặt VISION_MODEL_PATH) — app không đổi gì.")


if __name__ == "__main__":
    main()
