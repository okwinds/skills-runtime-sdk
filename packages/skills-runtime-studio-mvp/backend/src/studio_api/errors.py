from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException


def http_error(
    kind: str,
    message: str,
    *,
    status_code: int,
    details: Optional[Dict[str, Any]] = None,
) -> HTTPException:
    """
    构造统一的 HTTP 错误响应（MVP 最小结构）。

    参数：
    - kind：错误类型（用于前端区分场景；例如 not_found/validation_error/conflict）
    - message：人类可读的错误信息
    - status_code：HTTP 状态码
    - details：结构化详情（用于排障；不得包含 secrets）
    """

    return HTTPException(
        status_code=int(status_code),
        detail={
            "kind": str(kind),
            "message": str(message),
            "details": details or {},
        },
    )

