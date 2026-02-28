"""core/utils.py 共享工具函数测试。"""
from skills_runtime.core.utils import now_rfc3339
import datetime


def test_now_rfc3339_returns_z_suffix():
    result = now_rfc3339()
    assert result.endswith("Z")
    assert "+" not in result


def test_now_rfc3339_is_iso_format():
    result = now_rfc3339()
    dt = datetime.datetime.fromisoformat(result.replace("Z", "+00:00"))
    assert dt.tzinfo is not None
