# -*- coding: utf-8 -*-
"""备忘录数据模型层。

定义事务(Memo)、进度记录(ProgressLog)、紧急程度/状态常量、排序模式，
以及本机系统时间的格式化与解析工具。

本模块只依赖 Python 标准库，不 import 任何 UI / Win32 模块，
因此可在非 Windows 环境被独立 import 用于单元测试。
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class Urgency(object):
    """紧急程度枚举式常量。"""

    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"
    ALL = (HIGH, MEDIUM, LOW)


class Status(object):
    """事务状态枚举式常量。"""

    PENDING = "未完成"
    DONE = "已完成"
    ALL = (PENDING, DONE)


URGENCY_HIGH = Urgency.HIGH
URGENCY_MEDIUM = Urgency.MEDIUM
URGENCY_LOW = Urgency.LOW
URGENCY_LEVELS = Urgency.ALL

STATUS_PENDING = Status.PENDING
STATUS_DONE = Status.DONE
STATUS_LEVELS = Status.ALL

URGENCY_ORDER = {Urgency.HIGH: 0, Urgency.MEDIUM: 1, Urgency.LOW: 2}

SORT_CREATED = "created_at"
SORT_DUE = "due_at"
SORT_URGENCY = "urgency"
SORT_MODES = (
    (SORT_CREATED, "按创建时间"),
    (SORT_DUE, "按预期完成时间"),
    (SORT_URGENCY, "按紧急程度"),
)
SORT_MODE_LABELS = {key: label for key, label in SORT_MODES}

_SUPPORTED_TIME_FORMATS = (
    TIME_FORMAT,
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
)


# ---------------------------------------------------------------------------
# 时间工具（一律使用本机系统时间 datetime.now()，不使用 UTC）
# ---------------------------------------------------------------------------
def now_str() -> str:
    """返回当前本机系统时间的标准字符串。"""
    return datetime.now().strftime(TIME_FORMAT)


def now_dt() -> datetime:
    """返回当前本机系统时间的 datetime 对象。"""
    return datetime.now()


def parse_time(text: str) -> Optional[datetime]:
    """解析用户输入的时间文本，失败返回 None。

    支持完整时间戳、仅日期、斜杠分隔以及省略秒数等常见写法。
    """
    value = (text or "").strip()
    if not value:
        return None
    for fmt in _SUPPORTED_TIME_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def is_valid_time_text(text: str) -> bool:
    """空字符串合法（表示未设置预期完成时间），其它必须可解析。"""
    if not (text or "").strip():
        return True
    return parse_time(text) is not None


def normalize_progress(value: Any) -> int:
    """把任意输入规整为 0-100 的整数。

    非有限浮点数（NaN / ±inf，例如 JSON 中的 1e999 被解析为 inf）无法转成
    整数，统一回落到默认进度 0；int() 可能抛出的 OverflowError 也一并兜住，
    保证任何异常字段都不会向上冒泡到持久化层。
    """
    if isinstance(value, float) and not math.isfinite(value):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    if number < 0:
        return 0
    if number > 100:
        return 100
    return number


# ---------------------------------------------------------------------------
# 进度记录
# ---------------------------------------------------------------------------
class ProgressLog(object):
    """一条带系统时间戳的进度记录。"""

    def __init__(self, text: str = "", time: str = ""):
        self.text = text or ""
        self.time = time or now_str()

    def to_dict(self) -> Dict[str, str]:
        """序列化为可写入 JSON 的字典。"""
        return {"time": self.time, "text": self.text}

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ProgressLog":
        """从字典还原，缺失时间戳时补当前系统时间。"""
        item = data or {}
        return cls(
            text=str(item.get("text", "") or ""),
            time=str(item.get("time", "") or now_str()),
        )

    def __repr__(self) -> str:
        return "ProgressLog(time=%r, text=%r)" % (self.time, self.text)


# ---------------------------------------------------------------------------
# 事务
# ---------------------------------------------------------------------------
class Memo(object):
    """一条备忘录事务。

    字段约定（全工程统一，JSON 键名与此一致）：
        id          事务唯一标识
        title       标题
        content     内容/备注
        created_at  创建时间（新增时自动写入系统时间，不可手填）
        due_at      预期完成时间（可选，空字符串表示未设置）
        urgency     紧急程度：高 / 中 / 低
        status      状态：未完成 / 已完成
        progress    进度 0-100
        logs        进度记录列表，元素为 {"time": ..., "text": ...}
    """

    def __init__(
        self,
        id: str = "",
        title: str = "",
        content: str = "",
        created_at: str = "",
        due_at: str = "",
        urgency: str = URGENCY_MEDIUM,
        status: str = STATUS_PENDING,
        progress: Any = 0,
        logs: Optional[List[Any]] = None,
    ):
        self.id = id or uuid.uuid4().hex
        self.title = title or ""
        self.content = content or ""
        self.created_at = created_at or now_str()
        self.due_at = due_at or ""
        self.urgency = urgency if urgency in URGENCY_LEVELS else URGENCY_MEDIUM
        self.status = status if status in STATUS_LEVELS else STATUS_PENDING
        self.progress = normalize_progress(progress)
        self.logs = []  # type: List[ProgressLog]
        if logs:
            for item in logs:
                if isinstance(item, ProgressLog):
                    self.logs.append(item)
                else:
                    self.logs.append(ProgressLog.from_dict(item))

    def to_dict(self) -> Dict[str, Any]:
        """序列化为可写入 JSON 的字典。"""
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "created_at": self.created_at,
            "due_at": self.due_at,
            "urgency": self.urgency,
            "status": self.status,
            "progress": self.progress,
            "logs": [log.to_dict() for log in self.logs],
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "Memo":
        """从字典还原；对缺失或非法字段一律给出安全默认值。"""
        item = data or {}
        return cls(
            id=str(item.get("id", "") or ""),
            title=str(item.get("title", "") or ""),
            content=str(item.get("content", "") or ""),
            created_at=str(item.get("created_at", "") or now_str()),
            due_at=str(item.get("due_at", "") or ""),
            urgency=str(item.get("urgency", "") or URGENCY_MEDIUM),
            status=str(item.get("status", "") or STATUS_PENDING),
            progress=item.get("progress", 0),
            logs=item.get("logs", []) or [],
        )

    def add_progress(self, text: str, progress: Any = None, time: str = "") -> ProgressLog:
        """追加一条带系统时间戳的进度记录；可同时更新进度值。"""
        log = ProgressLog(text=text, time=time or now_str())
        self.logs.append(log)
        if progress is not None:
            self.progress = normalize_progress(progress)
        return log

    def is_done(self) -> bool:
        """是否已完成。"""
        return self.status == STATUS_DONE

    def is_pending(self) -> bool:
        """是否未完成。"""
        return not self.is_done()

    def due_datetime(self) -> Optional[datetime]:
        """预期完成时间的 datetime，未设置或非法返回 None。"""
        return parse_time(self.due_at)

    def is_overdue(self, now: Optional[datetime] = None) -> bool:
        """未完成且已超过预期完成时间。"""
        due = self.due_datetime()
        if due is None or self.is_done():
            return False
        return due < (now or datetime.now())

    def is_due_soon(self, hours: int = 48, now: Optional[datetime] = None) -> bool:
        """未完成、未逾期，且在 hours 小时内即将到期。"""
        due = self.due_datetime()
        if due is None or self.is_done():
            return False
        current = now or datetime.now()
        if due <= current:
            return False
        return (due - current).total_seconds() <= float(hours) * 3600.0

    def matches(self, keyword: str) -> bool:
        """标题或内容是否命中搜索关键字（大小写不敏感）。"""
        kw = (keyword or "").strip().lower()
        if not kw:
            return True
        return kw in self.title.lower() or kw in self.content.lower()

    def __repr__(self) -> str:
        return "Memo(id=%r, title=%r, status=%r, urgency=%r)" % (
            self.id,
            self.title,
            self.status,
            self.urgency,
        )
