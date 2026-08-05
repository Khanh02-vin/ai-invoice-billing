"""Regression: 8 receipt THẬT từ SROIE (ICDAR 2019) — các trường đã gia cố phải giữ nguyên.

Fixture lấy từ data/sroie/train.jsonl (đã tái dựng dòng từ bboxes).
Mỗi lần sửa regex/heuristic → chạy lại: pytest tests/test_sroie_regression.py
Các trường expected=None = chưa xử lý được trên receipt đó (không assert).
"""
import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extract.extractor import extract_from_text

FIXTURES = Path(__file__).parent / "data" / "sroie_regression"
CASES = sorted(p.stem for p in FIXTURES.glob("*.json"))


@pytest.mark.parametrize("key", CASES)
def test_sroie_regression(key):
    fx = json.loads((FIXTURES / f"{key}.json").read_text(encoding="utf-8"))
    inv = extract_from_text(fx["text"], llm=None)
    exp = fx["expected"]
    if exp["total"] is not None:
        assert abs(inv.total - exp["total"]) < 0.01, f"{key}: total {inv.total} != {exp['total']}"
    if exp["vendor"] is not None:
        assert (inv.vendor or "").strip().upper() == exp["vendor"].strip().upper(), \
            f"{key}: vendor {inv.vendor!r} != {exp['vendor']!r}"
    if exp["date"] is not None:
        assert inv.issue_date == exp["date"], f"{key}: date {inv.issue_date} != {exp['date']}"
