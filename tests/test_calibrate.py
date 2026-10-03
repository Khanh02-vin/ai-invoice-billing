"""Calibrate LLM threshold: knee detection + phát hiện lỗi field theo GT."""
import importlib.util
from pathlib import Path

from src.domain.models import Invoice

_SPEC = importlib.util.spec_from_file_location(
    "calibrate", Path(__file__).parent.parent / "scripts" / "calibrate_llm_threshold.py")
cal = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cal)


def test_knee_picks_elbow_on_cost_benefit_curve():
    """Đường cong lợi ích giảm dần: điểm gãy là chỗ còn lợi nhiều so với chi phí."""
    pts = [(0.0, 0.0), (10.0, 80.0), (30.0, 90.0), (100.0, 100.0)]
    assert cal.knee(pts) == (10.0, 80.0)


def test_field_errors_detects_wrong_total():
    """So GT: total lệch → đánh dấu lỗi; khớp → không."""
    rec = {"entities": {"total": "31.00", "company": "ABC HO TRADING", "date": "02 APR 2018"}}
    bad = cal.field_errors(Invoice(total=31.0, vendor="ABC HO TRADING",
                                   issue_date="2018-04-02"), rec)
    assert bad == {"total": False, "vendor": False, "date": False}
    bad = cal.field_errors(Invoice(total=1.0, vendor="ABC HO TRADING",
                                   issue_date="2018-04-02"), rec)
    assert bad["total"] is True


def test_field_errors_normalizes_gt_date_variants():
    """GT date kiểu '02 APR 2018' / '11.02.18' không được tính sai (phantom fail)."""
    for gt in ("02 APR 2018", "11.02.18", "20180428"):
        rec = {"entities": {"date": gt}}
        want = "2018-04-02" if gt == "02 APR 2018" else (
            "2018-02-11" if gt == "11.02.18" else "2018-04-28")
        assert cal.field_errors(Invoice(issue_date=want), rec) == {"date": False}
