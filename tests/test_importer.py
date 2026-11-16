# -*- coding: utf-8 -*-
"""importer 文本导入的单元测试。"""
from __future__ import annotations

import os
import sys
import unittest
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from importer import ImportItem, apply_import, parse_line, parse_text
from models import STATUS_DONE, STATUS_PENDING
from storage import MemoStore


class TestParseLine(unittest.TestCase):
    """单行解析。"""

    def test_empty_lines_return_none(self):
        for text in ("", "   ", "\t", None):
            with self.subTest(text=text):
                self.assertIsNone(parse_line(text))

    def test_plain_pending_task(self):
        item = parse_line("普通任务")
        self.assertEqual(item.title, "普通任务")
        self.assertEqual(item.note, "")
        self.assertFalse(item.done)

    def test_check_mark_done(self):
        item = parse_line("绑定方案，协议--芯片实现初步学习    √")
        self.assertEqual(item.title, "绑定方案，协议")
        self.assertEqual(item.note, "芯片实现初步学习")
        self.assertTrue(item.done)

    def test_done_word_case_insensitive(self):
        for text in ("任务 done", "任务 DONE", "任务 Done"):
            with self.subTest(text=text):
                self.assertTrue(parse_line(text).done)

    def test_separator_with_note_no_done_mark(self):
        item = parse_line("绑定方案，实现                                    ---待调试")
        self.assertEqual(item.title, "绑定方案，实现")
        self.assertEqual(item.note, "待调试")
        self.assertFalse(item.done)

    def test_numbering_prefix_stripped(self):
        for prefix in ("1、", "12.", "3．", "5)", "6）", "7:", "8："):
            with self.subTest(prefix=prefix):
                item = parse_line("%s任务名" % prefix)
                self.assertEqual(item.title, "任务名")

    def test_multi_dash_separator(self):
        item = parse_line("标题 ----- 说明")
        self.assertEqual(item.title, "标题")
        self.assertEqual(item.note, "说明")

    def test_done_in_middle_of_title_not_marked(self):
        item = parse_line("helloworld done标题中含done?")
        self.assertFalse(item.done)
        self.assertIn("done", item.title)

    def test_line_starting_with_separator_keeps_title(self):
        item = parse_line("---只有说明")
        self.assertFalse(item.done)
        self.assertTrue(item.title)


class TestParseText(unittest.TestCase):
    """多行解析与用户示例。"""

    USER_SAMPLE = (
        "1、绑定方案，协议--芯片实现初步学习    √\n"
        "2、绑定方案，测试                                    done\n"
        "3、绑定方案，实现                                    ---待调试"
    )

    def test_user_sample(self):
        items = parse_text(self.USER_SAMPLE)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].title, "绑定方案，协议")
        self.assertEqual(items[0].note, "芯片实现初步学习")
        self.assertTrue(items[0].done)
        self.assertEqual(items[1].title, "绑定方案，测试")
        self.assertTrue(items[1].done)
        self.assertEqual(items[2].title, "绑定方案，实现")
        self.assertEqual(items[2].note, "待调试")
        self.assertFalse(items[2].done)

    def test_empty_text(self):
        self.assertEqual(parse_text(""), [])
        self.assertEqual(parse_text("\n\n   \n"), [])

    def test_blank_lines_skipped(self):
        items = parse_text("任务一\n\n任务二\n")
        self.assertEqual([it.title for it in items], ["任务一", "任务二"])

    def test_line_numbers_reported(self):
        items = parse_text("\n\n第三行任务")
        self.assertEqual(items[0].line_no, 3)


class TestApplyImport(unittest.TestCase):
    """入库同步行为。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = MemoStore(os.path.join(self.dir, "memos.json"))

    def _items(self, text):
        return parse_text(text)

    def test_creates_new_memos_with_status_and_progress(self):
        created, updated, _ = apply_import(self.store, self._items("A √\nB -- note\nC"))
        self.assertEqual((created, updated), (3, 0))
        by_title = {m.title: m for m in self.store.list_all()}
        self.assertEqual(by_title["A"].status, STATUS_DONE)
        self.assertEqual(by_title["A"].progress, 100)
        self.assertEqual(by_title["B"].status, STATUS_PENDING)
        self.assertEqual(by_title["B"].progress, 0)
        self.assertEqual(by_title["C"].status, STATUS_PENDING)

    def test_note_synced_as_progress_log(self):
        apply_import(self.store, self._items("任务X -- 芯片实现初步学习 √\n任务Y -- 待调试"))
        by_title = {m.title: m for m in self.store.list_all()}
        logs_x = [log.text for log in by_title["任务X"].logs]
        self.assertEqual(logs_x, ["芯片实现初步学习"])
        logs_y = [log.text for log in by_title["Y"].logs] if False else \
            [log.text for log in by_title["任务Y"].logs]
        self.assertEqual(logs_y, ["待调试"])
        # 进展记录带系统时间戳
        for log in by_title["任务Y"].logs:
            self.assertTrue(log.time)

    def test_same_title_updates_instead_of_duplicating(self):
        apply_import(self.store, self._items("任务Z -- 第一版进展"))
        created, updated, _ = apply_import(self.store, self._items("任务Z -- 第二版进展"))
        self.assertEqual((created, updated), (0, 1))
        self.assertEqual(len(self.store), 1)
        memo = self.store.list_all()[0]
        texts = [log.text for log in memo.logs]
        self.assertEqual(texts, ["第一版进展", "第二版进展"])

    def test_reimport_updates_status(self):
        apply_import(self.store, self._items("任务W"))
        memo = self.store.list_all()[0]
        self.assertEqual(memo.status, STATUS_PENDING)
        apply_import(self.store, self._items("任务W √"))
        memo = self.store.list_all()[0]
        self.assertEqual(memo.status, STATUS_DONE)
        self.assertEqual(memo.progress, 100)

    def test_empty_items_noop(self):
        created, updated, skipped = apply_import(self.store, self._items("   \n"))
        self.assertEqual((created, updated, skipped), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
