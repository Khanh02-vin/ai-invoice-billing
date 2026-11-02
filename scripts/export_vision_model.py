"""Xuất MobileNetV3-Small thành model embedding ONNX cho purchase memory.

Model xuất là phần thân MobileNetV3-Small (bỏ lớp phân loại 1000 lớp ImageNet):
avgpool → flatten → Linear 576→1024 → Hardswish, cho vector 1024 chiều.
Chạy 1 lần, file `models/mobilenetv3_embed.onnx` (~10 MB) được commit-exclude;
thiếu file thì bộ nhớ nhận diện tự dùng dHash + OCR như cũ.

Cần torchvision chỉ ở lần chạy này: pip install torchvision --index-url https://download.pytorch.org/whl/cpu
"""

from __future__ import annotations

import os
import warnings

import torch
from torch import nn

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.environ.get("VISION_MODEL_PATH", os.path.join(REPO_ROOT, "models", "mobilenetv3_embed.onnx"))


def build_embedder() -> nn.Module:
    from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

    full = mobilenet_v3_small(weights=MobileNet_V3_Small_Weights.IMAGENET1K_V1)
    # classifier = [Linear 576→1024, Hardswish, Dropout, Linear 1024→1000, ...]
    # Giữ 2 tầng đầu: vector 1024 chiều mang "hình dạng món" hơn là nhãn ImageNet.
    return nn.Sequential(full.features, full.avgpool, nn.Flatten(1), full.classifier[:2])


def export_to_onnx(model: nn.Module, out_path: str) -> None:
    """Xuất model embedding sang ONNX 1 file, signature: image (N,3,224,224) → embedding (N,1024).

    dynamo=False: exporter cũ, ghi 1 file duy nhất (exporter mới tách weight
    ra file .data ngoài, phiền khi copy server).
    """
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    dummy = torch.randn(1, 3, 224, 224)
    with torch.no_grad():
        vector = model.eval()(dummy)
    assert vector.shape == (1, 1024), f"Kỳ vọng vector (1, 1024), nhận {tuple(vector.shape)}"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        torch.onnx.export(
            model,
            (dummy,),
            out_path,
            input_names=["image"],
            output_names=["embedding"],
            dynamic_axes={"image": {0: "batch"}},
            opset_version=17,
            dynamo=False,
        )


def main() -> None:
    model = build_embedder().eval()
    export_to_onnx(model, OUT_PATH)
    size_mb = os.path.getsize(OUT_PATH) / 1e6
    print(f"Đã xuất {OUT_PATH} ({size_mb:.1f} MB), vector 1024 chiều")


if __name__ == "__main__":
    main()
