# -*- coding: utf-8 -*-
"""QA 独立回归验证：界面精简三项改动的边缘路径。

不复用工程师写的断言，重点验证：
* 排序下拉里「仅显示已完成」筛选项的正交性、状态栏计数与重复切换稳定性；
* always_on_top 的脏数据兼容、双开关往返与启动应用链；
* 「—」隐藏到托盘与设置窗口「退出程序」绑定仍然可达；
* 筛选态下无已完成任务、新建 / 删除任务、搜索组合等边缘情形。
所有数据均通过显式临时路径传入，不修改 MEMO_DATA_DIR 全局环境变量
（避免污染 test_gui_smoke 等模块的临时目录断言），绝不触碰项目 data/。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import tkinter as tk
from tkinter import ttk

from config import AppConfig, DEFAULT_ALWAYS_ON_TOP
from models import SORT_MODES
from storage import MemoStore
import ui_main
from ui_main import FILTER_DONE_ONLY_LABEL, MainWindow, SORT_VIEW_OPTIONS

STATUS_DONE = "已完成"


class QAGuiBase(unittest.TestCase):
    """独立搭建的 MainWindow 测试夹具，带退出 / 托盘回调探针。"""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:  # pragma: no cover - 无头兜底
            raise unittest.SkipTest("tkinter display unavailable: %s" % exc)
        self.addCleanup(self._destroy_root)
        self.store = MemoStore(os.path.join(self.folder.name, "memos.json")).load()
        self.config = AppConfig()
        self.config_path = os.path.join(self.folder.name, "config.json")
        self.exit_calls = []
        self.hide_calls = []
        with mock.patch.object(MainWindow, "_tick"):
            self.window = MainWindow(
                self.root, self.store, self.config, self.config_path,
                on_request_exit=lambda: self.exit_calls.append(1),
                on_hide_to_tray=lambda: self.hide_calls.append(1),
            )

    def _destroy_root(self):
        try:
            for child in list(self.root.winfo_children()):
                if isinstance(child, tk.Toplevel):
                    child.destroy()
            for item in self.root.tk.call("after", "info"):
                try:
                    self.root.after_cancel(item)
                except tk.TclError:
                    pass
            self.root.destroy()
        except tk.TclError:
            pass

    def _add(self, title, status="未完成"):
        memo = self.store.create_memo(title=title, status=status)
        self.store.add(memo)
        return memo

    def _select_view(self, label):
        self.window.cmb_sort.set(label)
        self.window._on_sort_changed()

    @staticmethod
    def _walk(widget, kind):
        found = []
        for child in widget.winfo_children():
            if isinstance(child, kind):
                found.append(child)
            found.extend(QAGuiBase._walk(child, kind))
        return found


class TestSortFilterDeepDive(QAGuiBase):
    def test_status_done_count_matches_store_and_is_global(self):
        """状态栏「已完成 N 条」必须按库内真实已完成任务数显示。"""
        for index in range(3):
            self._add("已完%d" % index, STATUS_DONE)
        for index in range(2):
            self._add("待办%d" % index)
        self.window.refresh()

        self._select_view(FILTER_DONE_ONLY_LABEL)
        self.assertEqual(len(self.window._rows), 3)
        status = self.window.var_status.get()
        self.assertIn("已完成 3 条", status)
        # 三个总数仍是全库统计，不随筛选收窄
        self.assertIn("共 5 条", status)
        self.assertIn("未完成 2 条", status)

        self._select_view("按创建时间")
        self.assertNotIn("已完成", self.window.var_status.get())

    def test_filter_never_writes_config_and_repeated_toggles_are_stable(self):
        """反复切换筛选/排序不得写脏 config，也不得污染排序模式。"""
        self.assertFalse(os.path.exists(self.config_path))

        def config_bytes():
            if not os.path.isfile(self.config_path):
                return None
            with open(self.config_path, "rb") as handle:
                return handle.read()

        for _ in range(5):
            mode_before = self.window._sort_mode
            bytes_before = config_bytes()
            self._select_view(FILTER_DONE_ONLY_LABEL)
            self.assertTrue(self.window.var_done_only.get())
            # 筛选项不改变排序模式（保留切换前的值），也不写盘
            self.assertEqual(self.window._sort_mode, mode_before)
            self.assertEqual(self.window.config.sort_mode, mode_before)
            self.assertEqual(config_bytes(), bytes_before)

            self._select_view("按预期完成时间")
            self.assertFalse(self.window.var_done_only.get())
            self.assertEqual(self.window._sort_mode, "due_at")

        self.assertTrue(os.path.isfile(self.config_path))
        with open(self.config_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["sort_mode"], "due_at")
        self.assertNotIn(FILTER_DONE_ONLY_LABEL, json.dumps(payload, ensure_ascii=False))
        self.assertEqual(tuple(self.window.cmb_sort["values"]), tuple(SORT_VIEW_OPTIONS))

    def test_filter_with_zero_done_tasks(self):
        """列表里没有已完成任务时启用筛选：空列表 + 计数 0，不抛异常。"""
        self._add("只有待办")
        self._select_view(FILTER_DONE_ONLY_LABEL)
        self.assertEqual(self.window._rows, [])
        self.assertIn("已完成 0 条", self.window.var_status.get())
        self.window.refresh()  # 空结果上再次刷新也应安全
        self.assertEqual(self.window._rows, [])

    def test_filter_survives_manual_refresh_after_create_and_delete(self):
        """筛选态下库增删任务后，refresh 应按最新数据重新收窄。"""
        first_done = self._add("旧已完成", STATUS_DONE)
        self._add("待办")
        self._select_view(FILTER_DONE_ONLY_LABEL)
        self.assertEqual([m.title for m in self.window._rows], ["旧已完成"])

        self._add("新待办")
        self.window.refresh()
        self.assertEqual([m.title for m in self.window._rows], ["旧已完成"])

        new_done = self._add("新已完成", STATUS_DONE)
        self.window.refresh()
        self.assertEqual({m.title for m in self.window._rows},
                         {"旧已完成", "新已完成"})

        self.assertTrue(self.store.delete(first_done.id))
        self.window.refresh()
        self.assertEqual([m.id for m in self.window._rows], [new_done.id])

    def test_search_and_filter_are_intersected(self):
        self._add("alpha 已完成", STATUS_DONE)
        self._add("alpha 待办")
        self._add("beta 已完成", STATUS_DONE)
        self.window.var_search.set("alpha")
        self._select_view(FILTER_DONE_ONLY_LABEL)
        self.assertEqual([m.title for m in self.window._rows], ["alpha 已完成"])
        # 状态栏显示全库已完成数（2），与搜索词无关
        self.assertIn("已完成 2 条", self.window.var_status.get())

    def test_settings_sort_dropdown_excludes_filter_label(self):
        """设置窗口「默认排序」下拉只能是 3 个排序项，不能混入筛选项。"""
        self.window.open_settings()
        self.root.update()
        tops = [c for c in self.root.winfo_children() if isinstance(c, tk.Toplevel)]
        self.assertEqual(len(tops), 1)
        sort_boxes = [box for box in self._walk(tops[0], ttk.Combobox)
                      if "按创建时间" in tuple(box["values"])]
        self.assertEqual(len(sort_boxes), 1)
        self.assertEqual(tuple(sort_boxes[0]["values"]),
                         tuple(label for _, label in SORT_MODES))
        self.assertNotIn(FILTER_DONE_ONLY_LABEL, sort_boxes[0]["values"])
        tops[0].destroy()


class TestTopmostAndExitPaths(QAGuiBase):
    def test_startup_applies_topmost_through_expected_chain(self):
        """构造窗口时必须以 config 值初始化 var 并调用应用函数。"""
        seen = []

        def spy(self_window):
            seen.append(self_window.var_topmost.get())

        root = tk.Tk()
        root.withdraw()
        try:
            with mock.patch.object(MainWindow, "_on_topmost_changed", spy), \
                    mock.patch.object(MainWindow, "_tick"):
                window = MainWindow(root, self.store,
                                    AppConfig(always_on_top=True), self.config_path)
            self.assertEqual(seen, [True])
            self.assertTrue(window.var_topmost.get())
        finally:
            root.destroy()

    def test_settings_exit_button_is_bound_to_request_exit(self):
        """设置窗口「退出程序」必须触发 on_request_exit，而不是仅关闭窗口。"""
        self.window.open_settings()
        self.root.update()
        tops = [c for c in self.root.winfo_children() if isinstance(c, tk.Toplevel)]
        self.assertEqual(len(tops), 1)
        exit_buttons = [b for b in self._walk(tops[0], tk.Button)
                        if str(b.cget("text")) == "退出程序"]
        self.assertEqual(len(exit_buttons), 1)
        exit_buttons[0].invoke()
        self.assertEqual(self.exit_calls, [1])
        self.assertEqual(self.root.winfo_exists(), 1)

    def test_header_minimize_button_still_wires_hide_to_tray(self):
        """「—」按钮仍在且绑定隐藏到托盘，不销毁窗口、不触发退出。"""
        header = [c for c in self.window.container.winfo_children()
                  if isinstance(c, tk.Frame)][0]
        buttons = [b for b in header.winfo_children() if isinstance(b, tk.Button)]
        texts = [str(b.cget("text")) for b in buttons]
        self.assertIn("—", texts)
        self.assertNotIn("×", texts)
        minimize = [b for b in buttons if str(b.cget("text")) == "—"][0]
        minimize.invoke()
        self.assertEqual(self.hide_calls, [1])
        self.assertEqual(self.exit_calls, [])
        self.assertEqual(self.root.winfo_exists(), 1)


class TestConfigEdgeCases(unittest.TestCase):
    def test_both_switches_round_trip_together(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "config.json")
            original = AppConfig(auto_start=True, always_on_top=True)
            original.save(path)
            loaded = AppConfig.load(path)
            self.assertIs(loaded.auto_start, True)
            self.assertIs(loaded.always_on_top, True)
            self.assertIs(loaded.as_dict()["auto_start"], True)
            self.assertIs(loaded.as_dict()["always_on_top"], True)

    def test_extra_dirty_values_coerce_safely(self):
        cfg = AppConfig()
        for raw, expected in (("1", True), ("TRUE ", True), ("0", False),
                              (None, DEFAULT_ALWAYS_ON_TOP), (2, DEFAULT_ALWAYS_ON_TOP),
                              ("maybe", DEFAULT_ALWAYS_ON_TOP),
                              ({}, DEFAULT_ALWAYS_ON_TOP)):
            with self.subTest(raw=raw):
                cfg.apply_dict({"always_on_top": raw})
                self.assertIs(cfg.always_on_top, expected)


if __name__ == "__main__":
    unittest.main()
