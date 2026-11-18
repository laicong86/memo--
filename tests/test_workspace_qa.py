# -*- coding: utf-8 -*-
"""QA 独立边界验证（严过关 / Edward）。

与 test_workspace.py 互为补充，专攻工程师未覆盖的角度：
* 非 Windows opener 选择（darwin -> open / linux -> xdg-open）；
* Windows 下 startfile 成功与失败回退 explorer 的调用顺序；
* messagebox.showwarning 自身抛 TclError 时的兜底；
* 绑定等价路径（含 ..）不应重复 set / 触发自动保存；
* show() 载入无 workspace 属性的旧对象后 collect 的降级；
* UI collect -> MemoStore 落盘 -> 重新载入的端到端持久化；
* head 区两按钮插入后各控件的实际几何不溢出 / 不重叠。

所有 filedialog / os.startfile / subprocess.Popen / messagebox 均被 mock 隔离，
不弹真实窗口，也不会真的打开文件资源管理器。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import tkinter as tk

import theme
import ui_detail
from models import Memo
from storage import MemoStore
from ui_detail import DetailPanel


class _Platform(object):
    """最小的 sys.platform 替身，避免真去 patch 全局 sys 模块。"""

    def __init__(self, name):
        self.platform = name


class _QADetailCase(unittest.TestCase):
    """自带 Tk 根窗口与详情面板，独立于工程师测试文件。"""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="memo_ws_qa_")
        self.addCleanup(self.folder.cleanup)
        self.root = tk.Tk()
        self.root.geometry("1200x800+0+0")
        self.root.deiconify()
        self.root.update()
        self.addCleanup(self._destroy)
        self.saved = []
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


class TestPlatformOpener(unittest.TestCase):
    """_reveal_workspace 的跨平台分支：纯逻辑，无需 Tk 根窗口。"""

    def test_darwin_uses_open_opener(self):
        fake = _Platform("darwin")
        with mock.patch("ui_detail.sys", fake), \
                mock.patch("ui_detail.subprocess.Popen") as popen, \
                mock.patch.object(ui_detail.os, "startfile", create=True) as start:
            DetailPanel._reveal_workspace("/tmp/demo")
        start.assert_not_called()
        popen.assert_called_once_with(["open", "/tmp/demo"])

    def test_linux_uses_xdg_open_opener(self):
        fake = _Platform("linux")
        with mock.patch("ui_detail.sys", fake), \
                mock.patch("ui_detail.subprocess.Popen") as popen, \
                mock.patch.object(ui_detail.os, "startfile", create=True) as start:
            DetailPanel._reveal_workspace("/tmp/demo")
        start.assert_not_called()
        popen.assert_called_once_with(["xdg-open", "/tmp/demo"])

    @unittest.skipUnless(hasattr(os, "startfile"), "需要 Windows startfile")
    def test_win_startfile_success_skips_popen(self):
        fake = _Platform("win32")
        with mock.patch("ui_detail.sys", fake), \
                mock.patch.object(ui_detail.os, "startfile") as start, \
                mock.patch("ui_detail.subprocess.Popen") as popen:
            DetailPanel._reveal_workspace("D:\\demo")
        start.assert_called_once_with("D:\\demo")
        popen.assert_not_called()

    @unittest.skipUnless(hasattr(os, "startfile"), "需要 Windows startfile")
    def test_win_explorer_fallback_order(self):
        """startfile 抛异常后必须回退 explorer，且顺序为先 startfile 后 Popen。"""
        fake = _Platform("win32")
        manager = mock.Mock()
        with mock.patch("ui_detail.sys", fake), \
                mock.patch.object(ui_detail.os, "startfile",
                                  side_effect=OSError("blocked")) as start, \
                mock.patch("ui_detail.subprocess.Popen") as popen, \
                mock.patch("ui_detail.messagebox.showwarning") as warn:
            manager.attach_mock(start, "start")
            manager.attach_mock(popen, "popen")
            DetailPanel._reveal_workspace("D:\\demo")
        self.assertEqual(
            manager.mock_calls,
            [mock.call.start("D:\\demo"),
             mock.call.popen(["explorer", "D:\\demo"])],
        )
        warn.assert_not_called()

    def test_warning_tcl_error_is_swallowed(self):
        """Popen 失败且 showwarning 自身抛 TclError 时仍不能冒泡。"""
        fake = _Platform("linux")
        with mock.patch("ui_detail.sys", fake), \
                mock.patch("ui_detail.subprocess.Popen",
                           side_effect=OSError("no opener")), \
                mock.patch("ui_detail.messagebox.showwarning",
                           side_effect=tk.TclError("dead")):
            DetailPanel._reveal_workspace("/tmp/demo")


class TestQABindEdge(_QADetailCase):
    """绑定按钮的等价路径与旧数据边界。"""

    def test_bind_equivalent_path_does_not_reset(self):
        self.panel.show(Memo(title="绑定"))
        self.panel.var_workspace.set(self.folder.name)
        equivalent = os.path.join(self.folder.name, "sub", "..")
        with mock.patch("ui_detail.filedialog.askdirectory",
                        return_value=equivalent), \
                mock.patch.object(self.panel.var_workspace, "set") as setter:
            self.panel._click_bind_workspace()
        setter.assert_not_called()
        self.assertEqual(self.panel.var_workspace.get(), self.folder.name)

    def test_collect_after_legacy_show_is_empty(self):
        class OldMemo(object):
            id = "legacy-qa"
            title = "旧对象"
            content = ""
            created_at = "2026-01-01 00:00:00"
            due_at = ""
            urgency = "中"
            status = "未完成"
            progress = 0
            logs = []

        self.panel.show(OldMemo())  # type: ignore[arg-type]
        collected = self.panel.collect()
        self.assertIsNotNone(collected)
        self.assertEqual(collected.workspace, "")
        self.assertEqual(self.saved, [])


class TestQAPersistence(_QADetailCase):
    """UI 变更 -> collect -> 落盘 -> 重新载入的端到端链路。"""

    def test_ui_collect_persists_via_store(self):
        path = os.path.join(self.folder.name, "memos.json")
        memo = Memo(title="持久化", workspace=self.folder.name)
        self.panel.show(memo)
        # 模拟用户在表单里改动绑定目录（trace 只安排延时保存，这里手动冲刷）
        new_dir = os.path.join(self.folder.name, "nested")
        os.makedirs(new_dir, exist_ok=True)
        self.panel.var_workspace.set(new_dir)
        self.assertTrue(self.panel._save_now())

        collected = self.panel.collect()
        store = MemoStore(path)
        store.add(collected)
        store.save()
        reloaded = MemoStore(path).load().get(collected.id)
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.workspace, new_dir)


class TestQALayout(_QADetailCase):
    """DETAIL_PANEL_W=600 下 head 区几何不溢出 / 不重叠。"""

    def test_head_widgets_fit_within_width(self):
        self.panel.show(Memo(title="布局"))
        self.root.update()
        head = self.panel.head
        head_w = head.winfo_width()
        self.assertGreater(head_w, 0)
        self.assertLessEqual(head_w, theme.DETAIL_PANEL_W - 28)

        children = head.winfo_children()
        title = next(w for w in children
                     if isinstance(w, tk.Label)
                     and str(w.cget("text")) == "任务详情")
        close_btn = next(w for w in children
                         if isinstance(w, tk.Button)
                         and str(w.cget("text")) == "×")

        # 每个直接子控件都落在 head 的可见宽度内
        for widget in children:
            self.assertGreaterEqual(widget.winfo_x(), 0)
            self.assertLessEqual(widget.winfo_x() + widget.winfo_width(),
                                 head_w + 1)

        # 左侧标题/按钮/属性条都不得与最右关闭按钮重叠
        for widget in (title, self.panel.btn_quick_open,
                       self.panel.btn_bind_workspace, self.panel.side):
            self.assertLessEqual(widget.winfo_x() + widget.winfo_width(),
                                 close_btn.winfo_x() + 1)


if __name__ == "__main__":
    unittest.main()
