# -*- coding: utf-8 -*-
"""文本导入：把多行文本解析为事务并同步进事务库。

导入规则（与用户约定一致）：
1. 每个非空行 = 一条事务；行首的序号（如 ``1、`` ``2.`` ``3)``）自动去掉。
2. 行内出现 ``√``（或 ``done``，不分大小写）表示**已完成**：状态置为已完成、
   进度 100。
3. 连续两个及以上的 ``-``（如 ``--`` ``---``）是分隔符，把行拆成
   「标题 -- 进展说明」；若分隔符后的内容（以及整行）没有 √/done，则该事务
   **未完成**，分隔符后的说明文字作为一条进展记录（带本机系统时间戳）同步
   写进该任务的进展历史。
4. 标题与已有事务完全相同时不重复创建，而是更新那条事务（同步状态/进度/进展）。

本模块只依赖标准库，可在无 GUI 环境独立测试。
"""
from __future__ import annotations

import re
from typing import Any, List, NamedTuple, Optional

from models import STATUS_DONE, STATUS_PENDING, Memo, now_str

# 行首序号：数字 + 中文顿号 / 点 / 顿号变体 / 括号 / 冒号，后跟空白
_NUMBERING_RE = re.compile(r"^\s*\d+\s*[、\.．)）:：]\s*")
# 分隔符：连续两个及以上 '-'（允许中间夹杂空格，如 "- -"，宽松处理）
_SEPARATOR_RE = re.compile(r"\s*-{2,}\s*")
# 完成标记：√ / done（不区分大小写；done 需独立单词，避免误伤标题）
_DONE_MARK_RE = re.compile(r"√|☑|✓|\bdone\b", re.IGNORECASE)
# 从文本中剔除完成标记用于清理标题/说明
_DONE_STRIP_RE = re.compile(r"√|☑|✓|\bdone\b", re.IGNORECASE)


class ImportItem(NamedTuple):
    """一行导入文本解析后的结构化结果。"""

    title: str
    note: str
    done: bool
    line_no: int


def parse_line(line: str, line_no: int = 0) -> Optional[ImportItem]:
    """解析单行导入文本；空白行返回 None。

    解析顺序：
    1. 去行首序号；
    2. 判断完成标记（√ / done）；
    3. 按 ``-{2,}`` 分隔符拆成标题与进展说明；
    4. 清理标题与说明里的完成标记与多余空白。
    """
    text = (line or "").strip()
    if not text:
        return None
    text = _NUMBERING_RE.sub("", text, count=1)
    done = bool(_DONE_MARK_RE.search(text))

    segments = [seg for seg in _SEPARATOR_RE.split(text) if seg and seg.strip()]
    if len(segments) >= 2:
        title_raw = segments[0]
        note_raw = " ".join(seg.strip() for seg in segments[1:] if seg.strip())
    else:
        title_raw = text
        note_raw = ""

    title = _DONE_STRIP_RE.sub("", title_raw).strip()
    note = _DONE_STRIP_RE.sub("", note_raw).strip()
    if not title:
        return None
    return ImportItem(title=title, note=note, done=done, line_no=line_no)


def parse_text(text: str) -> List[ImportItem]:
    """把整段导入文本按行解析成结构化条目列表。"""
    items = []  # type: List[ImportItem]
    for index, line in enumerate((text or "").splitlines(), start=1):
        item = parse_line(line, line_no=index)
        if item is not None:
            items.append(item)
    return items


def apply_import(store: Any, items: List[ImportItem]) -> Any:
    """把解析结果同步进事务库，返回 (created, updated, skipped) 计数。

    同步策略：
    * 标题与已有事务**完全相同** → 更新该事务：
      - 状态按 done 置为已完成/未完成，进度相应置 100 / 保持；
      - note 非空时追加一条带系统时间戳的进展记录。
    * 否则新建事务（created_at 自动写系统时间），同样带上进展记录。
    """
    created = 0
    updated = 0
    for item in items:
        existing = None  # type: Optional[Memo]
        for memo in store.list_all():
            if memo.title == item.title:
                existing = memo
                break

        if existing is not None:
            existing.status = STATUS_DONE if item.done else STATUS_PENDING
            if item.done:
                existing.progress = 100
            if item.note:
                existing.add_progress(item.note)
            store.update(existing)
            updated += 1
        else:
            memo = store.create_memo(
                title=item.title,
                status=STATUS_DONE if item.done else STATUS_PENDING,
                progress=100 if item.done else 0,
            )
            if item.note:
                memo.add_progress(item.note)
            store.add(memo)
            created += 1
    return created, updated, len(items) - created - updated
