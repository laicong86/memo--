# -*- coding: utf-8 -*-
"""事务持久化层：基于标准库 json 的本地文件存储。

数据文件结构：
    {"version": 1, "memos": [ {事务字典}, ... ]}

提供增删改查、搜索、排序、未完成计数。文件不存在或损坏时安全降级为空库。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, List, Optional

from models import (
    Memo,
    SORT_CREATED,
    SORT_DUE,
    SORT_URGENCY,
    STATUS_DONE,
    URGENCY_ORDER,
)


class MemoStore(object):
    """内存中的事务集合 + JSON 文件读写。"""

    VERSION = 1

    def __init__(self, path: str = ""):
        self.path = path or "memos.json"
        self._memos = []  # type: List[Memo]

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------
    def load(self) -> "MemoStore":
        """从磁盘载入；文件异常时保持空库，不抛异常。

        单条损坏记录只跳过该条并尽量保留其余可用数据；文件级异常
        （打不开、JSON 损坏、字段类型异常、非有限数值等）一律降级为空库，
        绝不把解析异常冒泡给调用方（例如启动链路）。
        """
        self._memos = []
        if not os.path.isfile(self.path):
            return self
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            items = data.get("memos", []) if isinstance(data, dict) else []
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    try:
                        self._memos.append(Memo.from_dict(item))
                    except Exception:
                        # 单条记录字段损坏：跳过它，不影响其它事务
                        continue
        except (OSError, ValueError, TypeError, OverflowError):
            self._memos = []
        except Exception:
            # 兜底：任何未预期的解析异常都降级为空库，保证启动不被中断
            self._memos = []
        return self

    def save(self) -> bool:
        """把当前全部事务写回磁盘（自动创建父目录）。"""
        folder = os.path.dirname(os.path.abspath(self.path))
        if folder:
            os.makedirs(folder, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "memos": [memo.to_dict() for memo in self._memos],
        }
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return True

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def list_all(self) -> List[Memo]:
        """返回全部事务的副本列表。"""
        return list(self._memos)

    def __len__(self) -> int:
        return len(self._memos)

    def count_all(self) -> int:
        """事务总数。"""
        return len(self._memos)

    def count_pending(self) -> int:
        """未完成事务数量（用于解锁提醒）。"""
        return sum(1 for memo in self._memos if memo.status != STATUS_DONE)

    def get(self, memo_id: str) -> Optional[Memo]:
        """按 id 查找，未找到返回 None。"""
        for memo in self._memos:
            if memo.id == memo_id:
                return memo
        return None

    def search(self, keyword: str) -> List[Memo]:
        """按标题 / 内容过滤。"""
        return [memo for memo in self._memos if memo.matches(keyword)]

    def sorted_by(self, mode: str = SORT_CREATED, keyword: str = "") -> List[Memo]:
        """按指定模式排序，并叠加搜索过滤。

        三种模式：
            created_at  创建时间倒序（最新在前）
            due_at      预期完成时间升序（越早到期越靠前，未设置的排最后）
            urgency     紧急程度高→中→低，同级按创建时间
        所有模式下「未完成」都排在「已完成」之前（稳定排序，保留上述相对次序）。
        """
        rows = self.search(keyword)
        if mode == SORT_DUE:
            # 按解析后的真实时间排序，与 config 配色判定（parse_time）保持
            # 同一时间语义；未设置或解析失败统一用 datetime.max 放到末尾。
            rows.sort(key=lambda m: (
                m.due_datetime() or datetime.max,
                m.created_at,
            ))
        elif mode == SORT_URGENCY:
            rows.sort(key=lambda m: (URGENCY_ORDER.get(m.urgency, 9), m.created_at))
        else:
            rows.sort(key=lambda m: m.created_at, reverse=True)
        # 稳定排序：未完成(0) 优先于 已完成(1)
        rows.sort(key=lambda m: 0 if m.status != STATUS_DONE else 1)
        return rows

    # ------------------------------------------------------------------
    # 增删改
    # ------------------------------------------------------------------
    def add(self, memo: Memo) -> Memo:
        """新增事务；若 id 已存在则改为更新，保证不会重复。"""
        if self.get(memo.id) is not None:
            self.update(memo)
            return memo
        self._memos.append(memo)
        return memo

    def update(self, memo: Memo) -> bool:
        """按 id 替换事务，成功返回 True。"""
        for index, current in enumerate(self._memos):
            if current.id == memo.id:
                self._memos[index] = memo
                return True
        return False

    def delete(self, memo_id: str) -> bool:
        """按 id 删除事务，成功返回 True。"""
        for index, memo in enumerate(self._memos):
            if memo.id == memo_id:
                self._memos.pop(index)
                return True
        return False

    def create_memo(self, title: str = "", **kwargs: Any) -> Memo:
        """工厂方法：创建一条新事务，created_at 自动写入系统时间。

        注意：只创建内存对象，不自动入库；由 UI 保存时调用 add/update。
        """
        return Memo(title=title, **kwargs)
