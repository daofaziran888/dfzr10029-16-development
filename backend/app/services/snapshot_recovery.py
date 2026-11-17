"""数据快照可恢复清单与快照恢复依据包。

这里的业务对象分三层：

* ``snapshot_recovery_basis``：快照恢复依据包。每次校验（或检查点确认）都会落一个不可变版本，
  版本号按快照递增；处理输入原样保存，按版本回查时不会用当前快照重新计算。
* ``snapshot_recoverable``：可恢复快照清单。由「已执行快照 + 校验哈希」解析而来，
  一行绑定一份快照记录（记录指纹），快照记录变化后旧行不再参与可恢复清单。
* ``snapshot_checkpoint``：快照目录检查点。清理完成通知与恢复请求都会落到检查点上，
  但每个检查点只保留一个确认版本；后到的请求以已驳回版本留痕，不覆盖确认结果。

所有「读当前状态 -> 追加版本/确认检查点」的操作都在同一把可重入锁里完成，
同步路由跑在线程池里时并发提交也只会产生一个确认结果。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Mapping

from app.services.snapshot_manifest import (
    ALGORITHM,
    MANIFEST_FIELDS,
    checksums_match,
    compute_checksum,
    normalize_checksum,
    snapshot_fingerprint,
)
from app.store import store

SNAPSHOT_MODULE = "snapshot"
BASIS_MODULE = "snapshot_recovery_basis"
MANIFEST_MODULE = "snapshot_recoverable"
CHECKPOINT_MODULE = "snapshot_checkpoint"
CHECKPOINT_EVENT_MODULE = "snapshot_checkpoint_event"
RECOVERY_REQUEST_MODULE = "snapshot_recovery_request"

EXECUTED_STATUS = "已完成"
VERIFICATION_PASS = "校验通过"
VERIFICATION_FAIL = "校验失败"
VERIFICATION_NONE = "未校验"
RECOVERABLE = "可恢复"
NOT_RECOVERABLE = "不可恢复"
CONFIRMED = "已确认"
REJECTED = "已驳回"
CLEANED = "已清理"
NOT_CLEANED = "未清理"

EVENT_CLEANUP = "清理完成通知"
EVENT_RECOVERY = "恢复请求"
EVENT_ALIASES = {
    "清理完成": EVENT_CLEANUP,
    "清理完成通知": EVENT_CLEANUP,
    "cleanup_completed": EVENT_CLEANUP,
    "cleanup_complete": EVENT_CLEANUP,
    "cleanup": EVENT_CLEANUP,
    "恢复请求": EVENT_RECOVERY,
    "恢复完成": EVENT_RECOVERY,
    "recovery_request": EVENT_RECOVERY,
    "recovery_completed": EVENT_RECOVERY,
    "recovery": EVENT_RECOVERY,
    "recover": EVENT_RECOVERY,
}
SOURCE_ALIASES = {
    "校验接口": "校验接口",
    "verify": "校验接口",
    "校验": "校验接口",
    "执行完成": "执行完成",
    "execution": "执行完成",
    "snapshot_complete": "执行完成",
    "重新解析": "重新解析",
    "refresh": "重新解析",
    "reparse": "重新解析",
    "检查点": "检查点",
    "checkpoint": "检查点",
}
SOURCE_PREFIXES = {
    "检查点": "检查点·",
    "执行完成": "执行完成",
    "重新解析": "重新解析",
    "校验接口": "校验接口",
}


def _source_matches(candidate: Any, wanted: str) -> bool:
    """来源既支持精确匹配，也支持 `checkpoint` / `检查点` 这类分组写法。"""
    text = _text(candidate)
    key = SOURCE_ALIASES.get(wanted.strip().casefold(), wanted.strip())
    if text == key:
        return True
    prefix = SOURCE_PREFIXES.get(key)
    return bool(prefix) and text.startswith(prefix)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _row_count(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class SnapshotRecoveryService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized = False
        self._ensure_initialized()

    # ------------------------------------------------------------------ 基础
    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            if not store.rows(CHECKPOINT_MODULE):
                self._seed_checkpoints()
                # 示例检查点里已确认的版本要立刻反映到快照和依据包上，
                # 否则清理完成后的快照仍会被算成可恢复。
                for checkpoint in self._checkpoint_rows():
                    if checkpoint.get("确认版本") is None:
                        continue
                    self._apply_checkpoint_effect(checkpoint, _text(checkpoint.get("确认来源")))
            self.rebuild_manifest()
            self._initialized = True

    def _snapshot_rows(self) -> list[dict[str, Any]]:
        return store.rows(SNAPSHOT_MODULE)

    def _basis_rows(self) -> list[dict[str, Any]]:
        return store.rows(BASIS_MODULE)

    def _manifest_rows(self) -> list[dict[str, Any]]:
        return store.rows(MANIFEST_MODULE)

    def _checkpoint_rows(self) -> list[dict[str, Any]]:
        return store.rows(CHECKPOINT_MODULE)

    def _event_rows(self) -> list[dict[str, Any]]:
        return store.rows(CHECKPOINT_EVENT_MODULE)

    def find_snapshot(self, entry: int | str) -> dict[str, Any] | None:
        """按主键或快照编号定位快照记录。"""
        if isinstance(entry, int) or str(entry).isdigit():
            found = store.find(SNAPSHOT_MODULE, int(entry))
            if found is not None:
                return found
        wanted = str(entry).strip()
        for row in self._snapshot_rows():
            if _text(row.get("快照编号")) == wanted:
                return row
        return None

    def _find_checkpoint(self, entry: int | str) -> dict[str, Any] | None:
        if isinstance(entry, int) or str(entry).isdigit():
            found = store.find(CHECKPOINT_MODULE, int(entry))
            if found is not None:
                return found
        wanted = str(entry).strip()
        for row in self._checkpoint_rows():
            if _text(row.get("检查点编号")) == wanted:
                return row
        return None

    # -------------------------------------------------------------- 校验解析
    def verification_of(self, row: Mapping[str, Any]) -> dict[str, Any]:
        """解析快照校验哈希，返回校验结果、计算哈希与说明。"""
        stored = _text(row.get("校验哈希"))
        computed = compute_checksum(row)
        rows = _row_count(row.get("行数快照"))
        if not stored:
            result, reason = VERIFICATION_NONE, "快照没有登记校验哈希"
        elif rows <= 0:
            result, reason = VERIFICATION_FAIL, "快照行数为 0，无法作为恢复基线"
        elif checksums_match(stored, computed):
            result, reason = VERIFICATION_PASS, "校验哈希与快照清单一致"
        else:
            result, reason = VERIFICATION_FAIL, "校验哈希与快照清单不一致"
        return {
            "校验结果": result,
            "校验说明": reason,
            "存储校验哈希": stored,
            "计算校验哈希": computed,
            "规范化存储哈希": normalize_checksum(stored),
        }

    def _effective_state(self, row: Mapping[str, Any], verification: str) -> str:
        if _text(row.get("清理状态")) == CLEANED:
            return NOT_RECOVERABLE
        return RECOVERABLE if verification == VERIFICATION_PASS else NOT_RECOVERABLE

    def _latest_confirmed_basis(
        self, snapshot_id: int, source: str | None = None
    ) -> dict[str, Any] | None:
        candidates = [
            row
            for row in self._basis_rows()
            if _int(row.get("快照记录id")) == snapshot_id
            and row.get("状态") == CONFIRMED
            and (source is None or _source_matches(row.get("来源"), source))
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda row: (_int(row.get("版本")), _int(row.get("id"))))

    def _next_basis_version(self, snapshot_id: int) -> int:
        versions = [
            _int(row.get("版本"))
            for row in self._basis_rows()
            if _int(row.get("快照记录id")) == snapshot_id
        ]
        return max(versions, default=0) + 1

    def _persisted_input(self, row: Mapping[str, Any]) -> dict[str, Any]:
        payload = {field: row.get(field) for field in MANIFEST_FIELDS}
        payload["校验哈希"] = _text(row.get("校验哈希"))
        return payload

    def _diff_basis(self, previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> dict[str, Any]:
        if previous is None:
            return {"相对版本": None, "变化字段": [], "说明": "首次确认，无可比版本"}
        changes: list[dict[str, Any]] = []
        previous_input = previous.get("处理输入") or {}
        current_input = current.get("处理输入") or {}
        for field in MANIFEST_FIELDS:
            if previous_input.get(field) != current_input.get(field):
                changes.append({
                    "字段": field,
                    "原值": previous_input.get(field),
                    "新值": current_input.get(field),
                    "范围": "处理输入",
                })
        for field in ("存储校验哈希", "计算校验哈希", "校验结果", "可恢复状态", "确认校验哈希"):
            if previous.get(field) != current.get(field):
                changes.append({
                    "字段": field,
                    "原值": previous.get(field),
                    "新值": current.get(field),
                    "范围": "校验结果",
                })
        return {
            "相对版本": previous.get("版本"),
            "变化字段": changes,
            "说明": "与上一确认版本一致" if not changes else f"相对 v{previous.get('版本')} 有 {len(changes)} 项变化",
        }

    def _create_basis_locked(
        self,
        row: dict[str, Any],
        *,
        source: str,
        remark: str = "",
        recorded_state: str | None = None,
        checkpoint: Mapping[str, Any] | None = None,
        force_new: bool = False,
    ) -> dict[str, Any]:
        verification = self.verification_of(row)
        state = recorded_state or self._effective_state(row, verification["校验结果"])
        source = SOURCE_ALIASES.get(_text(source).casefold(), _text(source) or "校验接口")
        fingerprint = snapshot_fingerprint(row)
        latest = self._latest_confirmed_basis(_int(row.get("id")))

        # 同一份快照、同一来源、同样的校验结果重复提交时复用已有确认版本，
        # 并发校验因此只会落到一个版本上。
        if (
            not force_new
            and latest is not None
            and latest.get("记录指纹") == fingerprint
            and latest.get("来源") == source
            and latest.get("校验结果") == verification["校验结果"]
            and latest.get("可恢复状态") == state
        ):
            reused = dict(latest)
            reused["重复校验"] = True
            return reused

        version = self._next_basis_version(_int(row.get("id")))
        package: dict[str, Any] = {
            "id": max((_int(item.get("id")) for item in self._basis_rows()), default=0) + 1,
            "快照记录id": _int(row.get("id")),
            "快照编号": _text(row.get("快照编号")),
            "数据集名称": _text(row.get("数据集名称")),
            "版本": version,
            "来源": source,
            "状态": CONFIRMED,
            "算法": ALGORITHM,
            "存储校验哈希": verification["存储校验哈希"],
            "计算校验哈希": verification["计算校验哈希"],
            "校验结果": verification["校验结果"],
            "校验说明": verification["校验说明"],
            "可恢复状态": state,
            "确认校验哈希": verification["计算校验哈希"] if state == RECOVERABLE else "",
            "处理输入": self._persisted_input(row),
            "记录指纹": fingerprint,
            "创建时间": _now(),
            "备注": remark,
            "检查点id": checkpoint.get("id") if checkpoint else None,
            "检查点编号": checkpoint.get("检查点编号") if checkpoint else None,
            "检查点事件": checkpoint.get("确认来源") if checkpoint else None,
        }
        package["差异"] = self._diff_basis(latest, package)
        package["当前版本"] = True
        # 同一快照只允许一个「当前版本」标记，避免按旧版本回查时误显示为最新。
        for item in self._basis_rows():
            if _int(item.get("快照记录id")) == _int(row.get("id")):
                item["当前版本"] = False
        self._basis_rows().append(package)

        # 清单与校验结果绑定同一份快照记录：快照上的结果字段同步更新。
        row["校验结果"] = verification["校验结果"]
        row["可恢复状态"] = state
        row["依据包版本"] = version
        row["校验时间"] = package["创建时间"]
        row["确认校验哈希"] = package["确认校验哈希"]
        row["记录指纹"] = fingerprint
        if state == RECOVERABLE:
            row["异常说明"] = ""
        else:
            row["异常说明"] = verification["校验说明"]
        self._upsert_manifest_locked(row, package)
        return package

    # ------------------------------------------------------------ 可恢复清单
    def _manifest_item(self, row: Mapping[str, Any], basis: Mapping[str, Any] | None) -> dict[str, Any]:
        verification = self.verification_of(row)
        state = basis.get("可恢复状态") if basis else self._effective_state(row, verification["校验结果"])
        item: dict[str, Any] = {
            "id": _int(row.get("id")),
            "快照记录id": _int(row.get("id")),
            "快照编号": _text(row.get("快照编号")),
            "数据集名称": _text(row.get("数据集名称")),
            "快照时间": _text(row.get("快照时间")),
            "行数快照": _row_count(row.get("行数快照")),
            "存储位置": _text(row.get("存储位置")),
            "校验哈希": _text(row.get("校验哈希")),
            "存储校验哈希": verification["存储校验哈希"],
            "计算校验哈希": verification["计算校验哈希"],
            "校验结果": verification["校验结果"],
            "校验说明": verification["校验说明"],
            "可恢复状态": state,
            "依据包版本": basis.get("版本") if basis else None,
            "依据包来源": basis.get("来源") if basis else None,
            "确认校验哈希": basis.get("确认校验哈希", "") if basis else "",
            "清理状态": _text(row.get("清理状态")) or NOT_CLEANED,
            "记录指纹": snapshot_fingerprint(row),
            "stale": False,
            "异常": state != RECOVERABLE,
        }
        item.update({
            "dataset": item["数据集名称"],
            "snapshot_no": item["快照编号"],
            "snapshot_time": item["快照时间"],
            "checksum": item["校验哈希"],
            "verification": item["校验结果"],
            "recoverable": state == RECOVERABLE,
            "recoverable_state": state,
            "basis_version": item["依据包版本"],
            "confirmed_checksum": item["确认校验哈希"],
        })
        return item

    def _upsert_manifest_locked(self, row: Mapping[str, Any], basis: Mapping[str, Any] | None) -> dict[str, Any]:
        item = self._manifest_item(row, basis)
        existing = store.find(MANIFEST_MODULE, item["id"])
        if existing is not None:
            existing.clear()
            existing.update(item)
            return existing
        self._manifest_rows().append(item)
        return item

    def _remove_stale_locked(self, live_ids: set[int]) -> None:
        rows = self._manifest_rows()
        rows[:] = [row for row in rows if _int(row.get("快照记录id")) in live_ids]

    def rebuild_manifest(self, *, create_missing_basis: bool = True) -> dict[str, Any]:
        """重新解析已执行快照的校验哈希，刷新可恢复清单。"""
        with self._lock:
            live_ids: set[int] = set()
            created = 0
            for row in self._snapshot_rows():
                if _text(row.get("status")) != EXECUTED_STATUS:
                    continue
                live_ids.add(_int(row.get("id")))
                latest = self._latest_confirmed_basis(_int(row.get("id")))
                fingerprint = snapshot_fingerprint(row)
                if latest is None and create_missing_basis:
                    latest = self._create_basis_locked(row, source="初始化", remark="按已执行快照生成恢复依据包")
                    created += 1
                elif latest is not None and latest.get("记录指纹") != fingerprint:
                    latest = self._create_basis_locked(row, source="重新解析", remark="快照记录已变化，重新解析校验哈希")
                    created += 1
                self._upsert_manifest_locked(row, latest)
            self._remove_stale_locked(live_ids)
            return {
                "ok": True,
                "已执行快照": len(live_ids),
                "可恢复": sum(1 for row in self._manifest_rows() if row.get("可恢复状态") == RECOVERABLE),
                "不可恢复": sum(1 for row in self._manifest_rows() if row.get("可恢复状态") != RECOVERABLE),
                "新增依据包": created,
                "刷新时间": _now(),
            }

    def _current_manifest_rows(self, *, include_stale: bool = False) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in self._manifest_rows():
            snapshot = store.find(SNAPSHOT_MODULE, _int(row.get("快照记录id")))
            stale = (
                snapshot is None
                or _text(snapshot.get("status")) != EXECUTED_STATUS
                or snapshot_fingerprint(snapshot) != row.get("记录指纹")
            )
            item = dict(row)
            item["stale"] = stale
            if stale and not include_stale:
                continue
            items.append(item)
        return items

    def list_manifest(
        self,
        *,
        dataset: str | None = None,
        verification: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        only_recoverable: bool = False,
        include_stale: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            rows = self._current_manifest_rows(include_stale=include_stale)
        filtered = rows
        if dataset:
            filtered = [row for row in filtered if _text(row.get("数据集名称")) == dataset.strip()]
        if verification:
            filtered = [row for row in filtered if row.get("校验结果") == verification.strip()]
        if date_from:
            filtered = [row for row in filtered if _text(row.get("快照时间"))[:10] >= date_from.strip()]
        if date_to:
            filtered = [row for row in filtered if _text(row.get("快照时间"))[:10] <= date_to.strip()]
        if only_recoverable:
            filtered = [row for row in filtered if row.get("可恢复状态") == RECOVERABLE]
        filtered.sort(key=lambda row: (_text(row.get("快照时间")), _int(row.get("id"))), reverse=True)

        groups: dict[str, list[dict[str, Any]]] = {}
        for row in filtered:
            groups.setdefault(_text(row.get("数据集名称")), []).append(row)
        grouped = [
            {
                "数据集名称": name,
                "items": items,
                "总数": len(items),
                "可恢复": sum(1 for item in items if item.get("可恢复状态") == RECOVERABLE),
                "不可恢复": sum(1 for item in items if item.get("可恢复状态") != RECOVERABLE),
                "最近快照时间": max((_text(item.get("快照时间")) for item in items), default=""),
            }
            for name, items in groups.items()
        ]
        grouped.sort(key=lambda group: group["最近快照时间"], reverse=True)
        summary = {
            "总数": len(filtered),
            "可恢复": sum(1 for row in filtered if row.get("可恢复状态") == RECOVERABLE),
            "不可恢复": sum(1 for row in filtered if row.get("可恢复状态") != RECOVERABLE),
            "校验通过": sum(1 for row in filtered if row.get("校验结果") == VERIFICATION_PASS),
            "校验失败": sum(1 for row in filtered if row.get("校验结果") == VERIFICATION_FAIL),
            "未校验": sum(1 for row in filtered if row.get("校验结果") == VERIFICATION_NONE),
        }
        selection = {
            "数据集名称": dataset or "",
            "校验结果": verification or "",
            "开始日期": date_from or "",
            "结束日期": date_to or "",
            "仅看可恢复": only_recoverable,
        }
        return {
            "items": filtered,
            "total": len(filtered),
            "groups": grouped,
            "summary": summary,
            "datasets": self.datasets(),
            "selection": selection,
        }

    def datasets(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._current_manifest_rows()
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(_text(row.get("数据集名称")), []).append(row)
        result = []
        for name, items in groups.items():
            result.append({
                "数据集名称": name,
                "dataset": name,
                "总数": len(items),
                "可恢复": sum(1 for item in items if item.get("可恢复状态") == RECOVERABLE),
                "不可恢复": sum(1 for item in items if item.get("可恢复状态") != RECOVERABLE),
                "最近快照时间": max((_text(item.get("快照时间")) for item in items), default=""),
            })
        result.sort(key=lambda item: item["数据集名称"])
        return result

    def dataset_manifest(self, dataset_name: str) -> dict[str, Any]:
        return self.list_manifest(dataset=dataset_name)

    # ------------------------------------------------------------ 校验与回查
    def verify_snapshot(
        self, entry: int | str, *, source: str = "校验接口", remark: str = ""
    ) -> tuple[dict[str, Any] | None, str]:
        with self._lock:
            row = self.find_snapshot(entry)
            if row is None:
                return None, f"快照记录 {entry} 不存在或已归档"
            if _text(row.get("status")) != EXECUTED_STATUS:
                return None, f"快照记录 {entry} 尚未执行完成，不能解析校验哈希"
            package = self._create_basis_locked(row, source=source, remark=remark)
            message = f"快照校验完成：{package.get('校验结果')}，可恢复状态：{package.get('可恢复状态')}，依据包版本 v{package.get('版本')}"
            return package, message

    def get_basis(
        self, entry: int | str, *, version: int | None = None, source: str | None = None
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self.find_snapshot(entry)
            if row is None:
                return None
            snapshot_id = _int(row.get("id"))
            packages = [
                item for item in self._basis_rows() if _int(item.get("快照记录id")) == snapshot_id
            ]
            if not packages:
                return None
            if version is not None:
                selected = next((item for item in packages if _int(item.get("版本")) == version), None)
            else:
                confirmed = [
                    item
                    for item in packages
                    if item.get("状态") == CONFIRMED
                    and (source is None or _source_matches(item.get("来源"), source))
                ]
                selected = max(confirmed, key=lambda item: (_int(item.get("版本")), _int(item.get("id"))), default=None)
            if selected is None:
                return None
            result = dict(selected)
            confirmed_versions = [
                _int(item.get("版本")) for item in packages if item.get("状态") == CONFIRMED
            ]
            result["当前版本"] = _int(selected.get("版本")) == max(confirmed_versions, default=0)
            result["回查一致"] = selected.get("记录指纹") == snapshot_fingerprint(row)
            result["快照当前校验结果"] = self.verification_of(row)["校验结果"]
            result["快照当前可恢复状态"] = self._effective_state(row, self.verification_of(row)["校验结果"])
            result["来源列表"] = sorted({_text(item.get("来源")) for item in packages})
            result["版本列表"] = sorted({_int(item.get("版本")) for item in packages})
            result.update({
                "snapshot_id": snapshot_id,
                "snapshot_no": _text(row.get("快照编号")),
                "version": _int(selected.get("版本")),
                "source": selected.get("来源"),
                "status": selected.get("状态"),
                "verification": selected.get("校验结果"),
                "recoverable": selected.get("可恢复状态") == RECOVERABLE,
            })
            return result

    def basis_view(
        self, entry: int | str, *, version: int | None = None, source: str | None = None
    ) -> dict[str, Any] | None:
        """概览 + 明细：概览给当前版本与差异摘要，明细给处理输入和逐字段差异。"""
        package = self.get_basis(entry, version=version, source=source)
        if package is None:
            return None
        diff = package.get("差异") or {}
        changes = diff.get("变化字段") or []
        overview = {
            "快照编号": package.get("快照编号"),
            "数据集名称": package.get("数据集名称"),
            "当前版本": package.get("版本"),
            "版本状态": package.get("状态"),
            "来源": package.get("来源"),
            "校验结果": package.get("校验结果"),
            "可恢复状态": package.get("可恢复状态"),
            "确认校验哈希": package.get("确认校验哈希"),
            "校验时间": package.get("创建时间"),
            "相对版本": diff.get("相对版本"),
            "差异说明": diff.get("说明"),
            "差异数量": len(changes),
            "是最新版本": package.get("当前版本"),
            "回查一致": package.get("回查一致"),
            "版本列表": package.get("版本列表", []),
            "来源列表": package.get("来源列表", []),
            "检查点编号": package.get("检查点编号"),
        }
        detail = {
            "处理输入": package.get("处理输入") or {},
            "存储校验哈希": package.get("存储校验哈希"),
            "计算校验哈希": package.get("计算校验哈希"),
            "校验说明": package.get("校验说明"),
            "差异": diff,
            "检查点事件": package.get("检查点事件"),
            "备注": package.get("备注"),
        }
        return {
            "overview": overview,
            "detail": detail,
            "package": package,
        }

    def list_basis(
        self, *, snapshot_id: int | None = None, source: str | None = None
    ) -> dict[str, Any]:
        with self._lock:
            rows = [dict(item) for item in self._basis_rows()]
        if snapshot_id is not None:
            rows = [item for item in rows if _int(item.get("快照记录id")) == snapshot_id]
        if source:
            rows = [item for item in rows if _source_matches(item.get("来源"), source)]
        rows.sort(key=lambda item: (_int(item.get("快照记录id")), _int(item.get("版本"))), reverse=True)
        return {
            "items": rows,
            "total": len(rows),
            "sources": sorted({_text(item.get("来源")) for item in rows}),
        }

    def recover(
        self, entry: int | str, *, operator: str = "值班管理员", remark: str = ""
    ) -> tuple[dict[str, Any] | None, str]:
        """恢复入口只用已确认版本的校验哈希，拒绝过期或未确认的依据包。"""
        with self._lock:
            row = self.find_snapshot(entry)
            if row is None:
                return None, f"快照记录 {entry} 不存在或已归档"
            basis = self._latest_confirmed_basis(_int(row.get("id")))
            if basis is None:
                return None, "该快照还没有确认的恢复依据包，请先执行快照校验"
            if basis.get("记录指纹") != snapshot_fingerprint(row):
                return None, "恢复依据包与当前快照记录不一致，请重新校验后再恢复"
            if basis.get("可恢复状态") != RECOVERABLE:
                return None, "该快照的确认版本为不可恢复，不能发起恢复"
            checksum = _text(basis.get("确认校验哈希"))
            if not checksums_match(checksum, row.get("校验哈希")) or not checksums_match(checksum, compute_checksum(row)):
                return None, "确认校验哈希与当前快照不一致，不能发起恢复"
            requests = store.rows(RECOVERY_REQUEST_MODULE)
            record = {
                "id": max((_int(item.get("id")) for item in requests), default=0) + 1,
                "快照记录id": _int(row.get("id")),
                "快照编号": _text(row.get("快照编号")),
                "数据集名称": _text(row.get("数据集名称")),
                "依据包版本": basis.get("版本"),
                "使用校验哈希": checksum,
                "选择依据": f"依据包 v{basis.get('版本')}（{basis.get('来源')}）确认校验哈希",
                "操作人": operator,
                "恢复时间": _now(),
                "状态": "已受理",
                "备注": remark,
            }
            requests.append(record)
            return record, f"已按依据包 v{basis.get('版本')} 的确认校验哈希受理恢复"

    # --------------------------------------------------------------- 检查点
    def _seed_checkpoints(self) -> None:
        from app.seed import SEED_CHECKPOINT_EVENTS, SEED_CHECKPOINTS

        for checkpoint in SEED_CHECKPOINTS:
            self._checkpoint_rows().append(dict(checkpoint))
        for event in SEED_CHECKPOINT_EVENTS:
            self._event_rows().append(dict(event))

    def _apply_checkpoint_effect(
        self, checkpoint: dict[str, Any], event: str
    ) -> dict[str, Any] | None:
        row = self.find_snapshot(
            checkpoint.get("快照记录id") or _text(checkpoint.get("快照编号"))
        )
        if row is None:
            return None
        # 清理完成通知一旦确认，快照目录就不可再用于恢复；恢复请求只保证目录保留，
        # 不会把已经确认的清理结果重新翻回可恢复。
        if event == EVENT_CLEANUP:
            row["清理状态"] = CLEANED
        elif event == EVENT_RECOVERY and _text(row.get("清理状态")) != CLEANED:
            row["清理状态"] = NOT_CLEANED
        verification = self.verification_of(row)["校验结果"]
        state = self._effective_state(row, verification)
        return self._create_basis_locked(
            row,
            source=f"检查点·{event}",
            remark=f"检查点 {checkpoint.get('检查点编号')} 确认 {event}",
            recorded_state=state,
            checkpoint=checkpoint,
            force_new=True,
        )

    def list_checkpoints(self, *, status: str | None = None) -> dict[str, Any]:
        with self._lock:
            rows = [dict(item) for item in self._checkpoint_rows()]
        if status:
            rows = [row for row in rows if row.get("检查点状态") == status]
        rows.sort(key=lambda row: (row.get("检查点状态") != "待确认", _int(row.get("id"))))
        for row in rows:
            row["事件列表"] = [
                item for item in self._event_rows() if _int(item.get("检查点id")) == _int(row.get("id"))
            ]
        return {"items": rows, "total": len(rows)}

    def get_checkpoint(self, entry: int | str) -> dict[str, Any] | None:
        with self._lock:
            checkpoint = self._find_checkpoint(entry)
            if checkpoint is None:
                return None
            result = dict(checkpoint)
            result["事件列表"] = [
                item
                for item in self._event_rows()
                if _int(item.get("检查点id")) == _int(checkpoint.get("id"))
            ]
            confirmed_version = checkpoint.get("确认版本")
            if confirmed_version is not None:
                result["依据包"] = next(
                    (
                        item
                        for item in self._basis_rows()
                        if _int(item.get("检查点id")) == _int(checkpoint.get("id"))
                        and _int(item.get("版本")) == _int(confirmed_version)
                    ),
                    None,
                )
            else:
                result["依据包"] = None
            return result

    def create_checkpoint(self, values: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str]:
        with self._lock:
            snapshot = self.find_snapshot(
                values.get("快照记录id")
                or values.get("快照编号")
                or values.get("snapshot_id")
                or values.get("snapshot_no")
                or ""
            )
            if snapshot is None:
                return None, "检查点关联的快照记录不存在"
            rows = self._checkpoint_rows()
            checkpoint = {
                "id": max((_int(item.get("id")) for item in rows), default=0) + 1,
                "检查点编号": _text(values.get("检查点编号"))
                or f"CKPT-{datetime.now().strftime('%Y%m%d%H%M%S')}-{len(rows) + 1}",
                "快照记录id": _int(snapshot.get("id")),
                "快照编号": _text(snapshot.get("快照编号")),
                "数据集名称": _text(snapshot.get("数据集名称")),
                "快照目录": _text(values.get("快照目录")) or _text(snapshot.get("存储位置")),
                "检查点状态": "待确认",
                "确认版本": None,
                "当前版本": 0,
                "确认来源": "",
                "确认时间": "",
                "创建时间": _now(),
            }
            rows.append(checkpoint)
            return checkpoint, "检查点已登记"

    def submit_checkpoint_event(
        self, entry: int | str, event: str, *, operator: str = "值班管理员", remark: str = ""
    ) -> dict[str, Any]:
        """清理完成通知与恢复请求落到同一检查点时，只保留一个确认版本。"""
        event_name = EVENT_ALIASES.get(_text(event), _text(event))
        with self._lock:
            checkpoint = self._find_checkpoint(entry)
            if checkpoint is None:
                return {
                    "ok": False,
                    "conflict": False,
                    "message": f"检查点 {entry} 不存在",
                    "version": None,
                    "confirmed_version": None,
                    "entry": None,
                }
            if event_name not in (EVENT_CLEANUP, EVENT_RECOVERY):
                return {
                    "ok": False,
                    "conflict": False,
                    "message": f"事件「{event}」不属于检查点可接受范围",
                    "version": None,
                    "confirmed_version": checkpoint.get("确认版本"),
                    "entry": checkpoint,
                }
            events = [
                item
                for item in self._event_rows()
                if _int(item.get("检查点id")) == _int(checkpoint.get("id"))
            ]
            version = max((_int(item.get("版本")) for item in events), default=0) + 1
            confirmed_version = checkpoint.get("确认版本")
            if confirmed_version is not None:
                conflict = {
                    "id": max((_int(item.get("id")) for item in self._event_rows()), default=0) + 1,
                    "检查点id": _int(checkpoint.get("id")),
                    "检查点编号": checkpoint.get("检查点编号"),
                    "事件": event_name,
                    "版本": version,
                    "状态": REJECTED,
                    "确认时间": _now(),
                    "说明": f"检查点已有确认版本 v{confirmed_version}，本次 {event_name} 保留为已驳回版本",
                    "操作人": operator,
                    "备注": remark,
                }
                self._event_rows().append(conflict)
                checkpoint["当前版本"] = max(
                    _int(checkpoint.get("当前版本")), version
                )
                return {
                    "ok": False,
                    "conflict": True,
                    "message": f"检查点 {checkpoint.get('检查点编号')} 已有确认版本 v{confirmed_version}，本次 {event_name} 以 v{version} 留痕但不改变确认结果",
                    "version": version,
                    "confirmed_version": confirmed_version,
                    "entry": checkpoint,
                    "event": conflict,
                }

            checkpoint["检查点状态"] = "已确认"
            checkpoint["确认版本"] = version
            checkpoint["当前版本"] = version
            checkpoint["确认来源"] = event_name
            checkpoint["确认时间"] = _now()
            checkpoint["确认人"] = operator
            confirmed_event = {
                "id": max((_int(item.get("id")) for item in self._event_rows()), default=0) + 1,
                "检查点id": _int(checkpoint.get("id")),
                "检查点编号": checkpoint.get("检查点编号"),
                "事件": event_name,
                "版本": version,
                "状态": CONFIRMED,
                "确认时间": checkpoint["确认时间"],
                "说明": f"{event_name} 先落库，确认为检查点唯一有效版本",
                "操作人": operator,
                "备注": remark,
            }
            self._event_rows().append(confirmed_event)
            basis = self._apply_checkpoint_effect(checkpoint, event_name)
            snapshot = self.find_snapshot(checkpoint.get("快照记录id") or checkpoint.get("快照编号") or "")
            manifest = None
            if snapshot is not None:
                manifest = next(
                    (
                        dict(item)
                        for item in self._manifest_rows()
                        if _int(item.get("快照记录id")) == _int(snapshot.get("id"))
                    ),
                    None,
                )
            return {
                "ok": True,
                "conflict": False,
                "message": f"检查点 {checkpoint.get('检查点编号')} 已确认 {event_name}，版本 v{version}",
                "version": version,
                "confirmed_version": version,
                "entry": checkpoint,
                "event": confirmed_event,
                "basis": basis,
                "manifest": manifest,
            }


recovery_service = SnapshotRecoveryService()
