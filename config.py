# -*- coding: utf-8 -*-
"""配色规则与用户可配置的颜色映射。

判定维度：**紧急程度 + 预期完成时间 + 状态**，三者共同决定颜色类别(key)：

    done          已完成                         → 灰
    overdue_high  已逾期且高紧急                 → 红
    overdue       已逾期                         → 橙
    urgent_high   未逾期但高紧急                 → 橙黄
    due_soon      临期（due_soon_hours 小时内）  → 黄
    normal        普通                           → 绿

每个类别对应的填充色写在 config.json 中，用户可在「配色设置」界面里
通过下拉框选择预设颜色或直接填写 #RRGGBB，颜色关系完全可配置。
临期阈值(due_soon_hours)与默认排序模式同样可配置。

本模块只依赖标准库与 models，可在非 Windows 环境独立 import 用于测试。
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from models import (
    SORT_CREATED,
    SORT_MODE_LABELS,
    STATUS_DONE,
    URGENCY_HIGH,
    parse_time,
)
from hotkey import (
    DEFAULT_HOTKEY,
    DEFAULT_NEW_HOTKEY,
    is_valid_hotkey_text,
    normalize_hotkey_text as normalize_text,
)

# ---------------------------------------------------------------------------
# 颜色类别定义
# ---------------------------------------------------------------------------
COLOR_KEY_LABELS = {
    "done": "已完成",
    "overdue_high": "逾期 · 高紧急",
    "overdue": "已逾期",
    "urgent_high": "高紧急",
    "due_soon": "临期",
    "normal": "普通",
}
COLOR_KEYS = tuple(COLOR_KEY_LABELS.keys())

DEFAULT_COLOR_MAP = {
    "done": "#9E9E9E",
    "overdue_high": "#D32F2F",
    "overdue": "#F57C00",
    "urgent_high": "#F9A825",
    "due_soon": "#FDD835",
    "normal": "#43A047",
}

# 设置界面下拉可选的颜色预设：(十六进制值, 中文名)
COLOR_PRESETS = (
    ("#D32F2F", "红"),
    ("#E53935", "亮红"),
    ("#F57C00", "橙"),
    ("#F9A825", "橙黄"),
    ("#FDD835", "黄"),
    ("#43A047", "绿"),
    ("#1E88E5", "蓝"),
    ("#7E57C2", "紫"),
    ("#26A69A", "青"),
    ("#9E9E9E", "灰"),
)

DEFAULT_DUE_SOON_HOURS = 48
DEFAULT_UNLOCK_DUE_HOURS = 24  # 解锁提醒：列出未来 24 小时内将到期的事务
DEFAULT_SORT_MODE = SORT_CREATED
DEFAULT_HOTKEY = "F2"  # 全局快捷键：弹出 / 最小化主窗口
DEFAULT_NEW_HOTKEY = "Ctrl+Alt+N"  # 全局快捷键：快速新建事务
CONFIG_VERSION = 1

_MAX_HOURS = 24 * 365


def is_valid_hex(value: Any) -> bool:
    """判断是否为合法的 #RRGGBB 颜色值（严格 6 位十六进制）。

    使用 fullmatch 而非 int(text, 16)，避免 0x 前缀、内嵌空白、正号等
    被 int() 宽松接受后写入配置，最终导致 Tk 无法解析该颜色。
    """
    text = str(value or "").strip()
    return re.fullmatch(r"#[0-9A-Fa-f]{6}", text) is not None


def _coerce_hours(value: Any, default: int) -> int:
    """把任意输入规整为 1-8760 之间的有限整数；失败返回 default。

    非有限浮点数（NaN / ±inf，例如 JSON 中的 1e999 被解析为 inf）无法转成
    整数，统一视为非法；int() 可能抛出的 OverflowError 也一并兜住。
    这是 config 分支上与 models.normalize_progress 同构的根因防护。
    """
    if isinstance(value, float) and not math.isfinite(value):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if number < 1 or number > _MAX_HOURS:
        return default
    return number


def _safe_hours(value: Any, fallback: int = DEFAULT_DUE_SOON_HOURS) -> int:
    """把任意输入规整为 1-8760 之间的临期小时数。

    value 与 fallback 都经过 _coerce_hours 规整，保证返回值永远是安全的
    有限整数，绝不把 OverflowError / ValueError 冒泡到 AppConfig.load、
    classify_color_key 或 MemoApp 启动链路。
    """
    safe_fallback = _coerce_hours(fallback, DEFAULT_DUE_SOON_HOURS)
    return _coerce_hours(value, safe_fallback)


# ---------------------------------------------------------------------------
# 配置对象
# ---------------------------------------------------------------------------
class AppConfig(object):
    """用户配置：颜色映射、临期阈值、默认排序模式。"""

    def __init__(
        self,
        color_map: Optional[Dict[str, str]] = None,
        due_soon_hours: int = DEFAULT_DUE_SOON_HOURS,
        unlock_due_hours: int = DEFAULT_UNLOCK_DUE_HOURS,
        sort_mode: str = DEFAULT_SORT_MODE,
        hotkey: str = DEFAULT_HOTKEY,
        new_hotkey: str = DEFAULT_NEW_HOTKEY,
        path: str = "",
    ):
        self.path = path or ""
        self.color_map = dict(DEFAULT_COLOR_MAP)
        self.due_soon_hours = _safe_hours(due_soon_hours)
        self.unlock_due_hours = _safe_hours(unlock_due_hours,
                                            DEFAULT_UNLOCK_DUE_HOURS)
        # 非字符串（含不可哈希类型）一律回退，避免 dict 成员判断抛 TypeError
        self.sort_mode = (
            sort_mode
            if isinstance(sort_mode, str) and sort_mode in SORT_MODE_LABELS
            else DEFAULT_SORT_MODE
        )
        self.hotkey = (
            hotkey if isinstance(hotkey, str) and is_valid_hotkey_text(hotkey)
            else DEFAULT_HOTKEY
        )
        self.new_hotkey = (
            new_hotkey
            if isinstance(new_hotkey, str) and is_valid_hotkey_text(new_hotkey)
            else DEFAULT_NEW_HOTKEY
        )
        if color_map:
            self.apply_colors(color_map)

    def apply_colors(self, color_map: Dict[str, str]) -> None:
        """合并合法的颜色项，非法项自动忽略。"""
        if not isinstance(color_map, dict):
            return
        for key in COLOR_KEYS:
            value = color_map.get(key)
            if is_valid_hex(value):
                self.color_map[key] = str(value).strip()

    @classmethod
    def load(cls, path: str = "") -> "AppConfig":
        """从 JSON 文件载入配置；缺失或损坏时返回默认配置。"""
        cfg = cls(path=path)
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                cfg.apply_dict(data)
            except (OSError, ValueError, TypeError, OverflowError):
                # 文件级异常：回退到完整默认配置，不保留部分应用的结果
                cfg = cls(path=path)
            except Exception:
                # 兜底：任何未预期的解析异常都返回默认配置，绝不冒泡到启动链路
                cfg = cls(path=path)
        return cfg

    def apply_dict(self, data: Any) -> None:
        """应用配置字典（用于载入与测试）。"""
        if not isinstance(data, dict):
            return
        color_map = data.get("color_map")
        if isinstance(color_map, dict):
            self.apply_colors(color_map)
        if "due_soon_hours" in data:
            self.due_soon_hours = _safe_hours(data.get("due_soon_hours"), self.due_soon_hours)
        if "unlock_due_hours" in data:
            self.unlock_due_hours = _safe_hours(
                data.get("unlock_due_hours"), self.unlock_due_hours)
        mode = data.get("sort_mode")
        # 先做类型判定，避免 list/dict 等不可哈希值参与 dict 成员判断
        if isinstance(mode, str) and mode in SORT_MODE_LABELS:
            self.sort_mode = mode
        # 快捷键：非法值一律回退默认值，绝不冒泡到启动链路
        hotkey_value = data.get("hotkey")
        if isinstance(hotkey_value, str) and is_valid_hotkey_text(hotkey_value):
            self.hotkey = normalize_text(hotkey_value)
        else:
            self.hotkey = DEFAULT_HOTKEY
        new_hotkey_value = data.get("new_hotkey")
        if isinstance(new_hotkey_value, str) and is_valid_hotkey_text(new_hotkey_value):
            self.new_hotkey = normalize_text(new_hotkey_value)
        else:
            self.new_hotkey = DEFAULT_NEW_HOTKEY

    def as_dict(self) -> Dict[str, Any]:
        """序列化为可写 JSON 的字典。"""
        return {
            "version": CONFIG_VERSION,
            "color_map": dict(self.color_map),
            "due_soon_hours": self.due_soon_hours,
            "unlock_due_hours": self.unlock_due_hours,
            "sort_mode": self.sort_mode,
            "hotkey": self.hotkey,
            "new_hotkey": self.new_hotkey,
        }

    def save(self, path: str = "") -> bool:
        """写入 JSON 文件（自动创建父目录）。"""
        target = path or self.path
        if not target:
            return False
        folder = os.path.dirname(os.path.abspath(target))
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(self.as_dict(), handle, ensure_ascii=False, indent=2)
        return True

    def get_color(self, key: str) -> str:
        """取某个颜色类别的填充色，未知 key 回退到默认普通色。"""
        if not isinstance(key, str):
            return DEFAULT_COLOR_MAP["normal"]
        return self.color_map.get(key, DEFAULT_COLOR_MAP.get(key, DEFAULT_COLOR_MAP["normal"]))

    def set_color(self, key: str, value: str) -> bool:
        """设置某个颜色类别，返回是否成功。"""
        if isinstance(key, str) and key in COLOR_KEYS and is_valid_hex(value):
            self.color_map[key] = str(value).strip()
            return True
        return False

    def reset_colors(self) -> None:
        """恢复默认颜色映射。"""
        self.color_map = dict(DEFAULT_COLOR_MAP)


# ---------------------------------------------------------------------------
# 配色计算
# ---------------------------------------------------------------------------
def classify_color_key(
    status: str,
    due_at: str,
    urgency: str,
    now: Optional[datetime] = None,
    due_soon_hours: int = DEFAULT_DUE_SOON_HOURS,
) -> str:
    """按「状态 + 紧急程度 + 预期完成时间」计算颜色类别 key。

    判定优先级（先命中先返回）：
        1. 已完成            → done
        2. 已逾期 + 高紧急   → overdue_high
        3. 已逾期            → overdue
        4. 高紧急            → urgent_high
        5. 临期              → due_soon
        6. 其它              → normal
    """
    current = now or datetime.now()
    if status == STATUS_DONE:
        return "done"
    # 非字符串 due_at 一律按「未设置」处理，避免 parse_time 内部 strip 抛异常
    due = parse_time(due_at if isinstance(due_at, str) else "")
    if due is not None and due <= current:
        return "overdue_high" if urgency == URGENCY_HIGH else "overdue"
    if urgency == URGENCY_HIGH:
        return "urgent_high"
    if due is not None:
        # 非有限 due_soon_hours 在此被 _safe_hours 规整为安全整数
        hours = _safe_hours(due_soon_hours)
        if 0 < (due - current).total_seconds() <= float(hours) * 3600.0:
            return "due_soon"
    return "normal"


def resolve_color(
    status: str,
    due_at: str,
    urgency: str,
    config: Optional[AppConfig] = None,
    now: Optional[datetime] = None,
) -> str:
    """直接返回该事务应使用的十六进制填充色。"""
    cfg = config or AppConfig()
    key = classify_color_key(
        status,
        due_at,
        urgency,
        now=now,
        due_soon_hours=cfg.due_soon_hours,
    )
    return cfg.get_color(key)


def color_key_for_memo(
    memo: Any,
    config: Optional[AppConfig] = None,
    now: Optional[datetime] = None,
) -> str:
    """返回事务的颜色类别 key（鸭子类型读取 status/due_at/urgency）。"""
    cfg = config or AppConfig()
    return classify_color_key(
        memo.status,
        memo.due_at,
        memo.urgency,
        now=now,
        due_soon_hours=cfg.due_soon_hours,
    )


def color_for_memo(
    memo: Any,
    config: Optional[AppConfig] = None,
    now: Optional[datetime] = None,
) -> str:
    """返回事务标签应填充的十六进制颜色。"""
    cfg = config or AppConfig()
    key = color_key_for_memo(memo, cfg, now)
    return cfg.get_color(key)
