"""快照校验哈希的清单口径。

快照的校验哈希不是随便一段文本，而是由快照清单（编号、数据集、时间、行数、存储位置）
按固定顺序拼出来的摘要。校验接口、恢复依据包和示例数据都复用这里的同一套口径，
避免「登记哈希」和「重新计算哈希」各算各的。
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

ALGORITHM = "sha256"
MANIFEST_FIELDS = ("快照编号", "数据集名称", "快照时间", "行数快照", "存储位置")


def _row_count(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def manifest_text(row: Mapping[str, Any]) -> str:
    """返回参与摘要计算的规范化清单文本。"""
    return "|".join(
        [
            str(row.get("快照编号") or "").strip(),
            str(row.get("数据集名称") or "").strip(),
            str(row.get("快照时间") or "").strip(),
            str(_row_count(row.get("行数快照"))),
            str(row.get("存储位置") or "").strip(),
        ]
    )


def compute_checksum(row: Mapping[str, Any]) -> str:
    """按清单文本计算 sha256，统一带算法前缀，便于人工核对。"""
    digest = hashlib.sha256(manifest_text(row).encode("utf-8")).hexdigest()
    return f"{ALGORITHM}:{digest}"


def normalize_checksum(value: Any) -> str:
    """把 `sha256:`、`sha256=` 等写法归一成可比较的十六进制摘要。"""
    text = str(value or "").strip().casefold()
    for prefix in ("sha256:", "sha256=", "sha-256:", "sha-256="):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text


def checksums_match(left: Any, right: Any) -> bool:
    normalized_left = normalize_checksum(left)
    normalized_right = normalize_checksum(right)
    return bool(normalized_left) and normalized_left == normalized_right


def snapshot_fingerprint(row: Mapping[str, Any]) -> str:
    """记录快照身份指纹，用于判断依据包是否还对得上当前快照记录。"""
    payload = "|".join([manifest_text(row), normalize_checksum(row.get("校验哈希"))])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
