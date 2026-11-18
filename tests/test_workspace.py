# -*- coding: utf-8 -*-
"""工作空间绑定 / 快速打开的回归测试。

覆盖：Memo.workspace 持久化、表单 show/collect 贯通、绑定取消与路径规范化、
快速打开空值 / 失效目录的温和降级，以及标题行按钮的尺寸与字体。
所有 filedialog / os.startfile / subprocess.Popen 均被 mock 隔离，
不会弹真实窗口，也不会真的打开文件资源管理器。
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
import tkinter.font as tkfont

import theme
import ui_detail
from models import Memo
from storage import MemoStore
from ui_detail import DetailPanel


class TestWorkspaceModel(unittest.TestCase):
    """数据层：字段必须能随 JSON 无损往返，且兼容旧数据。"""

    def test_workspace_round_trip(self):
        memo = Memo(title="带目录", workspace="D:\\work\\demo")
        self.assertEqual(memo.workspace, "D:\\work\\demo")
        data = memo.to_dict()
        self.assertEqual(data["workspace"], "D:\\work\\demo")
        self.assertEqual(Memo.from_dict(data).workspace, "D:\\work\\demo")

    def test_legacy_data_without_workspace_defaults_to_empty(self):
        memo = Memo.from_dict({"title": "老数据", "progress": 30})
        self.assertEqual(memo.workspace, "")
        self.assertEqual(Memo(workspace=None).workspace, "")

    def test_non_string_workspace_degrades_to_empty(self):
        for bad in (123, [], {}, True):
            self.assertEqual(Memo.from_dict({"workspace": bad}).workspace, "")
            self.assertEqual(Memo(workspace=bad).workspace, "")

    def test_storage_file_round_trip_and_legacy_file(self):
        folder = tempfile.TemporaryDirectory(prefix="memo_ws_model_")
        self.addCleanup(folder.cleanup)
        path = os.path.join(folder.name, "memos.json")
        store = MemoStore(path).load()
        memo = store.create_memo(title="持久化", workspace=folder.name)
        store.add(memo)
        self.assertTrue(store.save())
        self.assertEqual(MemoStore(path).load().get(memo.id).workspace,
                         folder.name)

        # 旧版 JSON（没有 workspace 键）载入后必须为空串
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "memos": [
                {"id": "legacy-1", "title": "旧数据"}]}, handle)
        loaded = MemoStore(path).load().get("legacy-1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.workspace, "")


class _DetailCase(unittest.TestCase):
    """直接构造详情面板，避免依赖主窗口。"""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="memo_ws_gui_")
        self.addCleanup(self.folder.cleanup)
        self.root = tk.Tk()
        self.root.geometry("1200x800+0+0")
        self.root.deiconify()
        self.root.update()
        self.addCleanup(self._destroy)
        self.saved = []  # type: list
        self.panel = DetailPanel(self.root, on_save=self.saved.append)
        self.panel.pack(side="left", fill="y", padx=14)
        self.root.update()

    def _destroy(self):
        try:
            self.panel._cancel_save()
        except (tk.TclError, AttributeError):
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass


class TestWorkspaceForm(_DetailCase):
    """表单变量与自动保存链路。"""

    def test_show_and_collect_preserve_workspace(self):
        memo = Memo(title="任务", workspace=self.folder.name)
        self.panel.show(memo)
        self.root.update()
        self.assertEqual(self.panel.var_workspace.get(), self.folder.name)
        collected = self.panel.collect()
        self.assertIsNotNone(collected)
        self.assertEqual(collected.workspace, self.folder.name)
        # show() 载入字段时不能触发自动保存
        self.assertEqual(self.saved, [])

    def test_collect_strips_surrounding_spaces(self):
        memo = Memo(title="任务", workspace=self.folder.name)
        self.panel.show(memo)
        self.panel.var_workspace.set("  %s  " % self.folder.name)
        self.assertEqual(self.panel.collect().workspace, self.folder.name)

    def test_workspace_change_triggers_autosave(self):
        memo = Memo(title="任务")
        self.panel.show(memo)
        self.panel.var_workspace.set(self.folder.name)
        self.root.update()
        self.assertTrue(self.panel._save_now())
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(self.saved[-1].workspace, self.folder.name)

    def test_show_tolerates_memo_without_workspace_attribute(self):
        class OldMemo(object):
            id = "old"
            title = "旧对象"
            content = ""
            created_at = "2026-01-01 00:00:00"
            due_at = ""
            urgency = "中"
            status = "未完成"
            progress = 0
            logs = []

        self.panel.show(OldMemo())  # type: ignore[arg-type]
        self.assertEqual(self.panel.var_workspace.get(), "")


class TestBindWorkspace(_DetailCase):
    """绑定按钮：选择规范化，取消不改值。"""

    def _picked(self):
        # 含 .. 的路径，验证 os.path.normpath 生效
        return os.path.join(self.folder.name, "sub", "..")

    def test_pick_normalizes_path_and_autosaves(self):
        self.panel.show(Memo(title="绑定"))
        self.panel.var_workspace.set("")
        with mock.patch("ui_detail.filedialog.askdirectory",
                        return_value=self._picked()) as ask:
            self.panel._click_bind_workspace()
        ask.assert_called_once()
        self.assertIn("parent", ask.call_args.kwargs)
        self.assertEqual(self.panel.var_workspace.get(),
                         os.path.normpath(self._picked()))
        self.assertTrue(self.panel._save_now())
        self.assertEqual(self.saved[-1].workspace, self.folder.name)

    def test_cancel_keeps_previous_value(self):
        self.panel.var_workspace.set("原来的目录")
        with mock.patch("ui_detail.filedialog.askdirectory",
                        return_value="") as ask:
            self.panel._click_bind_workspace()
        ask.assert_called_once()
        self.assertEqual(self.panel.var_workspace.get(), "原来的目录")
        self.assertEqual(self.saved, [])

    def test_dialog_tcl_error_is_ignored(self):
        self.panel.var_workspace.set("原来的目录")
        with mock.patch("ui_detail.filedialog.askdirectory",
                        side_effect=tk.TclError("closed")):
            self.panel._click_bind_workspace()
        self.assertEqual(self.panel.var_workspace.get(), "原来的目录")


class TestQuickOpen(_DetailCase):
    """快速打开按钮：空值 / 失效目录提示，有效目录跨平台打开。"""

    def test_empty_workspace_shows_info_and_does_not_open(self):
        self.panel.var_workspace.set("")
        with mock.patch("ui_detail.messagebox.showinfo") as info, \
                mock.patch.object(ui_detail.os, "startfile") as start, \
                mock.patch("ui_detail.subprocess.Popen") as popen:
            self.panel._click_quick_open()
        info.assert_called_once()
        self.assertIn("尚未绑定", info.call_args.args[1])
        start.assert_not_called()
        popen.assert_not_called()

    def test_missing_directory_shows_warning_and_does_not_open(self):
        missing = os.path.join(self.folder.name, "not-exists")
        self.panel.var_workspace.set(missing)
        with mock.patch("ui_detail.messagebox.showwarning") as warn, \
                mock.patch.object(ui_detail.os, "startfile") as start, \
                mock.patch("ui_detail.subprocess.Popen") as popen:
            self.panel._click_quick_open()
        warn.assert_called_once()
        self.assertIn("不存在", warn.call_args.args[1])
        start.assert_not_called()
        popen.assert_not_called()

    def test_existing_directory_is_opened(self):
        self.panel.var_workspace.set(self.folder.name)
        with mock.patch.object(ui_detail.os, "startfile") as start, \
                mock.patch("ui_detail.subprocess.Popen") as popen:
            self.panel._click_quick_open()
        if sys.platform.startswith("win"):
            start.assert_called_once_with(self.folder.name)
            popen.assert_not_called()
        else:
            popen.assert_called_once_with(["xdg-open", self.folder.name])

    @unittest.skipUnless(hasattr(os, "startfile"), "需要 Windows startfile")
    def test_falls_back_to_explorer_when_startfile_fails(self):
        self.panel.var_workspace.set(self.folder.name)
        with mock.patch.object(ui_detail.os, "startfile",
                               side_effect=OSError("blocked")), \
                mock.patch("ui_detail.subprocess.Popen") as popen, \
                mock.patch("ui_detail.messagebox.showwarning") as warn:
            self.panel._click_quick_open()
        popen.assert_called_once_with(["explorer", self.folder.name])
        warn.assert_not_called()

    def test_open_failure_shows_warning_without_exception(self):
        self.panel.var_workspace.set(self.folder.name)
        with mock.patch.object(ui_detail.os, "startfile",
                               side_effect=OSError("blocked")), \
                mock.patch("ui_detail.subprocess.Popen",
                           side_effect=OSError("no opener")), \
                mock.patch("ui_detail.messagebox.showwarning") as warn:
            self.panel._click_quick_open()
        warn.assert_called_once()
        self.assertIn("无法打开", warn.call_args.args[1])


class TestWorkspaceButtons(_DetailCase):
    """标题行按钮：存在、字体一致、宽度不溢出。"""

    def test_buttons_exist_with_tiny_font(self):
        expected = tkfont.Font(root=self.root,
                               font=theme.FONT_TINY_PLAIN).actual()
        for attr, text in (("btn_quick_open", "快速打开"),
                           ("btn_bind_workspace", "绑定工作空间")):
            button = getattr(self.panel, attr)
            self.assertIsInstance(button, tk.Button)
            self.assertIs(button.master, self.panel.head)
            self.assertEqual(str(button.cget("text")), text)
            # tkinter 会把字体元组转成字符串，用 actual() 归一化后比较
            self.assertEqual(
                tkfont.Font(root=self.root,
                            font=button.cget("font")).actual(), expected)
            self.assertEqual(str(button.cget("font")),
                             str(self.panel.cmb_urgency.cget("font")))
            self.assertEqual(button.cget("relief"), "flat")
            self.assertEqual(button.cget("cursor"), "hand2")

    def test_buttons_fit_before_property_bar(self):
        head = self.panel.head
        children = head.winfo_children()
        self.assertLess(children.index(self.panel.btn_quick_open),
                        children.index(self.panel.side))
        self.assertLess(children.index(self.panel.btn_bind_workspace),
                        children.index(self.panel.side))
        # head 可用宽度 = DETAIL_PANEL_W - 左右 14px padding
        self.assertLessEqual(head.winfo_reqwidth(),
                             theme.DETAIL_PANEL_W - 28)
        self.assertLessEqual(head.winfo_width(),
                             theme.DETAIL_PANEL_W - 28)
        self.assertGreater(head.winfo_width(), 0)


if __name__ == "__main__":
    unittest.main()
