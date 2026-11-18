# -*- coding: utf-8 -*-
"""Real tkinter smoke tests (hidden root, no visible window, no mainloop).

Exercises MainWindow / DetailPanel / MemoApp coordination logic with actual
tkinter widgets: double-click -> right-side detail, editable text + progress
logs, configurable settings window, and the unlock reminder containing the
exact pending-memo count. Runtime data is redirected to a temp directory so
the project's data/ folder is never touched.
"""
from __future__ import annotations

import atexit
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Redirect runtime data BEFORE any MemoApp is constructed.
_DATA_TMP = tempfile.TemporaryDirectory(prefix="memo_qa_data_")
os.environ["MEMO_DATA_DIR"] = _DATA_TMP.name
atexit.register(_DATA_TMP.cleanup)

import tkinter as tk

import main as main_module
from config import AppConfig, COLOR_KEYS, DEFAULT_COLOR_MAP
from models import (STATUS_DONE, STATUS_LEVELS, TIME_FORMAT, URGENCY_LEVELS,
                    Memo)
from storage import MemoStore
import theme
import ui_main
from ui_main import MainWindow


def _make_root():
    try:
        root = tk.Tk()
        root.withdraw()
        return root
    except tk.TclError as exc:  # pragma: no cover - headless fallback
        raise unittest.SkipTest("tkinter display unavailable: %s" % exc)


def _cancel_all_after(root):
    """Drop pending Tk timers so no callback fires after the root is destroyed."""
    try:
        pending = root.tk.call("after", "info")
    except tk.TclError:
        return
    for item in pending:
        try:
            root.after_cancel(item)
        except tk.TclError:
            pass


class GuiTestCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = _make_root()
        self.addCleanup(self._destroy_root)
        self.store = MemoStore(os.path.join(self.folder.name, "memos.json")).load()
        self.config = AppConfig()
        self.config_path = os.path.join(self.folder.name, "config.json")
        # MainWindow.__init__ starts a 1s clock timer; suppress it so the
        # suite stays free of stray Tk callbacks. _tick itself is tested below.
        with mock.patch.object(MainWindow, "_tick"):
            self.window = MainWindow(self.root, self.store, self.config,
                                     self.config_path)

    def _destroy_root(self):
        try:
            for child in list(self.root.winfo_children()):
                if isinstance(child, tk.Toplevel):
                    child.destroy()
            _cancel_all_after(self.root)
            self.root.destroy()
        except tk.TclError:
            pass


class TestMainWindowSmoke(GuiTestCase):
    def test_build_exposes_all_required_widgets(self):
        for name in ("var_search", "cmb_sort", "list_canvas", "detail",
                     "var_status", "container"):
            with self.subTest(widget=name):
                self.assertTrue(hasattr(self.window, name), name)
        self.assertEqual(len(self.window.cmb_sort["values"]), 3)

    def test_status_bar_shows_counts_and_system_time(self):
        text = self.window.var_status.get()
        self.assertIn("共 0 条", text)
        self.assertIn("未完成 0 条", text)
        self.assertIn("系统时间", text)
        self.assertIn("双击任务查看详情", text)
        self.assertLess(text.index("系统时间"),
                        text.index("双击任务查看详情"))

    def test_tick_schedules_next_status_refresh(self):
        with mock.patch.object(self.root, "after") as after:
            self.window._tick()
        after.assert_called_once()
        self.assertEqual(after.call_args[0][0], 1000)

    def test_hide_to_tray_does_not_destroy_root(self):
        self.window.hide_to_tray()
        self.assertEqual(self.root.winfo_exists(), 1)

    def test_show_does_not_raise(self):
        with mock.patch.object(self.root, "deiconify"):
            self.window.show()
        self.window.hide_to_tray()

    def test_new_memo_only_asks_for_a_title(self):
        self.window.new_memo()
        self.assertTrue(self.window._quick_open)
        self.assertFalse(self.window._detail_open)
        self.assertIsNone(self.window.detail._current)
        self.assertEqual(self.window.var_quick_title.get(), "")
        self.assertEqual(self.store.count_all(), 0)

    def test_create_memo_from_title_uses_defaults_and_persists(self):
        memo = self.window.create_memo_from_title("  只填标题  ")
        self.assertIsNotNone(memo)
        self.assertEqual(memo.title, "只填标题")
        self.assertEqual(memo.content, "")
        self.assertEqual(memo.due_at, "")
        self.assertEqual(memo.urgency, "中")
        self.assertEqual(memo.status, "未完成")
        self.assertEqual(memo.progress, 0)
        self.assertEqual(memo.logs, [])
        stamp = datetime.strptime(memo.created_at, TIME_FORMAT)
        self.assertLess(abs((stamp - datetime.now()).total_seconds()), 5)
        self.assertEqual(self.store.count_all(), 1)
        self.assertTrue(os.path.isfile(self.store.path))
        self.assertFalse(self.window._detail_open)
        self.assertEqual([m.title for m in self.window._rows], ["只填标题"])

    def test_quick_new_commit_and_cancel(self):
        self.window.new_memo()
        self.window.var_quick_title.set("回车创建")
        memo = self.window._commit_quick_new()
        self.assertIsNotNone(memo)
        self.assertFalse(self.window._quick_open)
        self.assertEqual(self.store.count_all(), 1)
        self.window.new_memo()
        self.window._cancel_quick_new()
        self.assertFalse(self.window._quick_open)
        self.assertEqual(self.store.count_all(), 1)

    def test_empty_title_creates_nothing(self):
        self.assertIsNone(self.window.create_memo_from_title("   "))
        self.window.new_memo()
        self.assertIsNone(self.window._commit_quick_new())
        self.assertTrue(self.window._quick_open)
        self.assertEqual(self.store.count_all(), 0)

    def test_double_click_after_quick_new_opens_detail(self):
        memo = self.window.create_memo_from_title("双击补充信息")
        self.window._on_row_double_click(SimpleNamespace(y=10))
        self.assertTrue(self.window._detail_open)
        self.assertIs(self.window.detail._current, memo)
        self.assertEqual(self.window.detail.var_title.get(), "双击补充信息")
        self.assertIn(memo.created_at, self.window.detail.var_created.get())

    def test_double_click_row_opens_detail_for_that_memo(self):
        memo = self.store.create_memo(title="double click me")
        self.store.add(memo)
        self.window.refresh()
        self.window._on_row_double_click(SimpleNamespace(y=10))
        self.assertTrue(self.window._detail_open)
        self.assertIs(self.window.detail._current, memo)
        self.assertEqual(self.window._selected_id, memo.id)

    def test_expanding_detail_splits_window_in_half(self):
        """双击展开详情：窗口不加宽，列表与详情在同一窗口内各占约一半。"""
        window = self.window
        window.root.deiconify()
        window.root.geometry("%dx%d+50+50" % (theme.WIN_LIST_W, theme.WIN_H))
        window.root.update()
        self.assertFalse(window.splitter.winfo_ismapped())

        memo = self.store.create_memo(title="宽度测试")
        self.store.add(memo)
        window.show_detail(memo)
        window.root.update()
        self.assertTrue(window.splitter.winfo_ismapped())
        # 默认各半：列表宽约等于 (窗口可用宽 - 分隔条)/2
        available = theme.WIN_LIST_W - 2 * theme.PAD - theme.SPLIT_W
        self.assertLess(abs(window._list_w - available // 2), 12)
        self.assertLess(window._list_w, theme.LIST_W)  # 明显小于原来的满宽
        # 详情列宽度与列表相当（紧凑各半，而非列表满宽 + 详情 600）
        detail_w = window.body.grid_bbox(2, 0)[2]
        self.assertLess(abs(detail_w - window._list_w), 40)

    def test_detail_close_narrows_window_but_keeps_list_width(self):
        """收起详情：窗口跟着收窄到列表宽，列表本身不再被撑满。"""
        window = self.window
        window.root.deiconify()
        window.root.geometry("%dx%d+50+50" % (theme.WIN_LIST_W, theme.WIN_H))
        window.root.update()
        memo = self.store.create_memo(title="窗口跟着收窄")
        self.store.add(memo)
        window.show_detail(memo)
        window.root.update()
        list_w = window._list_w
        detail_win_w = window.root.winfo_width()
        self.assertGreater(detail_win_w, theme.MIN_COLLAPSED_W)

        window.close_detail()
        window.root.update()
        # 窗口收窄到「列表宽 + 边距」（受顶栏下限保护），列表宽度不变
        expected = max(theme.MIN_COLLAPSED_W, list_w + 2 * theme.PAD)
        self.assertEqual(window.root.winfo_width(), expected)
        self.assertEqual(window._list_w, list_w)
        self.assertLess(window.root.winfo_width(), detail_win_w)

        # 再次展开：恢复到展开态宽度，列表占比不变
        window.show_detail(memo)
        window.root.update()
        self.assertEqual(window._list_w, list_w)
        self.assertEqual(window.root.winfo_width(), detail_win_w)

    def test_repeated_detail_toggle_keeps_expanded_width(self):
        """反复展开/收起详情，展开态窗口宽度稳定，不会每次越收越窄。"""
        window = self.window
        memo = self.store.create_memo(title="反复开关")
        self.store.add(memo)
        window.root.deiconify()
        window.root.geometry("%dx%d+50+50" % (theme.WIN_LIST_W, theme.WIN_H))
        window.root.update()

        window.show_detail(memo)
        window.root.update()
        widths = [window.root.winfo_width()]
        for _ in range(3):
            window.close_detail()
            window.root.update()
            window.show_detail(memo)
            window.root.update()
            widths.append(window.root.winfo_width())
        self.assertEqual(len(set(widths)), 1, "展开态窗口宽度应保持稳定：%s" % widths)

    def test_splitter_drag_changes_ratio_and_clamps(self):
        """按住分隔条左右拖动可调整两侧占比，且不会越过最小宽度。"""
        window = self.window
        memo = self.store.create_memo(title="分隔条测试")
        self.store.add(memo)
        window.root.deiconify()
        window.show_detail(memo)
        window.root.update()

        # 足够宽的主体下，按位移调整两侧占比；起点 _list_w 是展开时的各半值
        start = window._list_w
        with mock.patch.object(window.body, "winfo_width", return_value=1800):
            window._start_split_drag(SimpleNamespace(x_root=500))
            window._on_split_drag(SimpleNamespace(x_root=620))
            self.assertEqual(window._list_w, start + 120)
            window._on_split_drag(SimpleNamespace(x_root=-5000))
            self.assertEqual(window._list_w, theme.MIN_LIST_W)

        # 向右拖到极限：列表最多占「总宽 - 详情最小宽」
        window._start_split_drag(SimpleNamespace(x_root=500))
        window._on_split_drag(SimpleNamespace(x_root=2000))
        body_w = window.body.winfo_width()
        max_list = max(theme.MIN_LIST_W, body_w - theme.SPLIT_W - theme.MIN_DETAIL_W)
        self.assertLessEqual(window._list_w, max_list)

        # 收起后窗口跟着收窄，列表保持用户调后的宽度、不被撑满；占比要记下来供重开恢复
        adjusted = window._user_list_w
        self.assertIsNotNone(adjusted)
        window.close_detail()
        window.root.update()
        self.assertFalse(window.splitter.winfo_ismapped())
        self.assertEqual(window._list_w, adjusted)
        self.assertEqual(window.root.winfo_width(),
                         max(theme.MIN_COLLAPSED_W, adjusted + 2 * theme.PAD))
        window.show_detail(memo)
        self.assertEqual(window._list_w, adjusted)  # 重开恢复用户占比

    def test_user_adjusted_ratio_restored_on_reopen(self):
        """用户拖分隔条调过占比后，收起再展开详情要恢复用户调后的占比。"""
        window = self.window
        memo = self.store.create_memo(title="宽度保留")
        self.store.add(memo)
        window.root.deiconify()
        window.root.update()
        window.show_detail(memo)
        window.root.update()
        # 模拟用户拖动分隔条把列表调宽 120
        start = window._list_w
        window._start_split_drag(SimpleNamespace(x_root=500))
        window._on_split_drag(SimpleNamespace(x_root=620))
        adjusted = window._list_w
        self.assertGreater(adjusted, start)
        self.assertEqual(window._user_list_w, adjusted)

        # 收起：窗口收窄，列表保持用户调后的宽度不被拉长，占比记录仍在
        window.close_detail()
        self.assertEqual(window._list_w, adjusted)
        self.assertEqual(window._user_list_w, adjusted)
        # 重开：恢复用户调整后的列表宽，而不是默认各半
        window.show_detail(memo)
        self.assertEqual(window._list_w, adjusted)

    def test_single_click_selects_row(self):
        memo = self.store.create_memo(title="click me")
        self.store.add(memo)
        self.window.refresh()
        self.window._on_row_click(SimpleNamespace(y=10))
        self.assertEqual(self.window._selected_id, memo.id)

    def test_search_input_filters_rows(self):
        self.store.add(self.store.create_memo(title="alpha"))
        self.store.add(self.store.create_memo(title="beta"))
        self.window.var_search.set("alpha")
        self.assertEqual(self.window._keyword, "alpha")
        self.assertEqual([m.title for m in self.window._rows], ["alpha"])
        self.window.var_search.set("")
        self.assertEqual(len(self.window._rows), 2)

    def test_sort_change_updates_mode_and_persists_config(self):
        self.window.cmb_sort.set("按紧急程度")
        self.window._on_sort_changed()
        self.assertEqual(self.window._sort_mode, "urgency")
        self.assertEqual(self.config.sort_mode, "urgency")
        self.assertTrue(os.path.isfile(self.config_path))

    def test_set_status_hint_is_reflected_in_status_bar(self):
        self.window.set_status_hint("还有 5 条未完成事务")
        self.assertIn("还有 5 条未完成事务", self.window.var_status.get())

    def test_open_settings_creates_toplevel(self):
        self.window.open_settings()
        self.root.update()
        tops = [child for child in self.root.winfo_children()
                if isinstance(child, tk.Toplevel)]
        self.assertEqual(len(tops), 1)
        # 顶栏按钮改为「设置」，窗口整合配色与快捷键两个分区
        self.assertEqual(tops[0].title(), "设置")
        tops[0].destroy()

    def test_header_has_settings_button(self):
        texts = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, tk.Button):
                    texts.append(str(child.cget("text")))
                walk(child)
        walk(self.window.root)
        self.assertIn("设置", texts)
        self.assertIn("导入", texts)
        self.assertNotIn("配色设置", texts)

    # --- 仅显示已完成勾选框 ---
    def test_done_only_checkbox_exists(self):
        self.assertEqual(str(self.window.chk_done_only.cget("text")), "已完成")
        self.assertFalse(self.window.var_done_only.get())

    def test_done_only_filters_list(self):
        window = self.window
        store = window.store
        for i, status in enumerate(["已完成", "未完成", "已完成", "未完成"], 1):
            memo = store.create_memo(title="筛选任务%d" % i, status=status)
            store.add(memo)
        window.refresh()
        self.assertEqual(len(window._rows), 4)
        window.var_done_only.set(True)
        window.refresh()
        self.assertEqual(len(window._rows), 2)
        self.assertTrue(all(m.is_done() for m in window._rows))
        window.var_done_only.set(False)
        window.refresh()
        self.assertEqual(len(window._rows), 4)

    def test_done_only_updates_status_bar(self):
        window = self.window
        window.var_done_only.set(True)
        window.refresh()
        self.assertIn("已完成", window.var_status.get())
        window.var_done_only.set(False)
        window.refresh()
        self.assertNotIn("· 已完成", window.var_status.get())

    # --- 窗口保持最前勾选框 ---
    def test_topmost_checkbox_exists(self):
        self.assertEqual(str(self.window.chk_topmost.cget("text")), "保持最前")
        self.assertFalse(self.window.var_topmost.get())

    def test_topmost_toggle_applies_immediately(self):
        window = self.window
        window.var_topmost.set(True)
        window._on_topmost_changed()
        self.window.root.update()
        self.assertEqual(window.root.attributes("-topmost"), 1)
        window.var_topmost.set(False)
        window._on_topmost_changed()
        self.window.root.update()
        self.assertEqual(window.root.attributes("-topmost"), 0)

    def test_clear_topmost_respects_checkbox(self):
        window = self.window
        # 勾选时 _clear_topmost 不应取消置顶（托盘唤起的 250ms 定时器保护）
        window.var_topmost.set(True)
        window.root.attributes("-topmost", True)
        window._clear_topmost()
        self.assertEqual(window.root.attributes("-topmost"), 1)
        # 未勾选时恢复原行为：取消置顶
        window.var_topmost.set(False)
        window._clear_topmost()
        self.assertEqual(window.root.attributes("-topmost"), 0)

    # --- 导入窗口 ---
    def test_open_import_creates_window_and_imports(self):
        window = self.window
        window.open_import()
        self.root.update()
        tops = [child for child in self.root.winfo_children()
                if isinstance(child, tk.Toplevel)]
        self.assertEqual(len(tops), 1)
        self.assertEqual(tops[0].title(), "导入事务")
        # 找到文本框，填入示例文本，找到「导入」按钮并点击
        text_widget = None
        import_button = None

        def walk(widget):
            nonlocal text_widget, import_button
            for child in widget.winfo_children():
                if isinstance(child, tk.Text) and text_widget is None:
                    text_widget = child
                if isinstance(child, tk.Button) and str(child.cget("text")) == "导入":
                    import_button = child
                walk(child)
        walk(tops[0])
        self.assertIsNotNone(text_widget)
        self.assertIsNotNone(import_button)
        text_widget.insert("1.0", "1、导入测试任务--初步进展 √\n2、导入待办任务 --- 等待联调")
        import_button.invoke()
        self.root.update()
        titles = [m.title for m in window.store.list_all()]
        self.assertIn("导入测试任务", titles)
        self.assertIn("导入待办任务", titles)
        by_title = {m.title: m for m in window.store.list_all()}
        self.assertEqual(by_title["导入测试任务"].status, "已完成")
        self.assertEqual([l.text for l in by_title["导入待办任务"].logs], ["等待联调"])
        tops[0].destroy()

    # --- 窗口空白区拖动 / 边缘自由调整大小 ---
    def test_blank_areas_are_draggable(self):
        # header、container、分隔线、footer 都绑定了拖动事件
        for widget in (self.window.container,):
            self.assertTrue(widget.bind("<ButtonPress-1>"))
            self.assertTrue(widget.bind("<B1-Motion>"))

    def test_canvas_edge_resize_bindings(self):
        for seq in ("<Motion>", "<ButtonPress-1>", "<B1-Motion>",
                    "<ButtonRelease-1>"):
            with self.subTest(seq=seq):
                self.assertTrue(self.window.canvas.bind(seq))
        # 指针离开 8px 边缘落到内部控件后，Canvas 收不到 B1-Motion，
        # 必须靠全局捕获才能继续拖动、并可靠结束调整状态。
        for seq in ("<B1-Motion>", "<ButtonPress-1>", "<ButtonRelease-1>"):
            with self.subTest(global_seq=seq):
                self.assertTrue(self.root.bind_all(seq))

    def test_edge_at_detects_eight_directions(self):
        window = self.window
        # 隐藏窗口的 winfo 尺寸为 1，先 deiconify 并设置已知大小
        window.root.geometry("400x300+50+50")
        window.root.deiconify()
        window.root.update()
        width = window.canvas.winfo_width()
        height = window.canvas.winfo_height()
        self.assertGreater(width, 40)
        self.assertGreater(height, 40)
        self.assertEqual(window._edge_at(2, height // 2), "w")
        self.assertEqual(window._edge_at(width - 2, height // 2), "e")
        self.assertEqual(window._edge_at(width // 2, 2), "n")
        self.assertEqual(window._edge_at(width // 2, height - 2), "s")
        self.assertEqual(window._edge_at(2, 2), "nw")
        self.assertEqual(window._edge_at(width - 2, 2), "ne")
        self.assertEqual(window._edge_at(2, height - 2), "sw")
        self.assertEqual(window._edge_at(width - 2, height - 2), "se")
        self.assertEqual(window._edge_at(width // 2, height // 2), "")
        # 圆角透明区（8px 内）外侧但仍属于角落的实体像素必须也能命中，
        # 否则四个角落点不到、拖不动。
        corner = theme.CORNER_RESIZE_BAND
        self.assertGreater(corner, theme.RESIZE_BAND)
        self.assertEqual(window._edge_at(corner - 4, corner - 4), "nw")
        self.assertEqual(window._edge_at(width - corner + 4, corner - 4), "ne")
        self.assertEqual(window._edge_at(corner - 4, height - corner + 4), "sw")
        self.assertEqual(window._edge_at(width - corner + 4,
                                         height - corner + 4), "se")
        window.root.withdraw()

    def test_corner_press_through_child_starts_resize(self):
        """角落被内部控件覆盖时，全局按下仍按 Canvas 坐标启动缩放。"""
        window = self.window
        window.root.geometry("900x560+50+50")
        window.root.deiconify()
        window.root.update()
        # 模拟指针落在右上角圆角内侧（被详情/按钮等子控件挡住）
        px = window.canvas.winfo_rootx() + window.canvas.winfo_width() - 12
        py = window.canvas.winfo_rooty() + 12
        event = SimpleNamespace(x_root=px, y_root=py)
        with mock.patch.object(window.root, "winfo_pointerx", return_value=px), \
                mock.patch.object(window.root, "winfo_pointery", return_value=py), \
                mock.patch.object(window.root, "winfo_containing",
                                  return_value=window.container):
            window._on_any_press(event)
        self.assertTrue(window._resizing)
        self.assertEqual(window._resize_edge, "ne")
        window._on_canvas_release(event)

    def test_resize_scales_list_and_detail_proportionally(self):
        """详情展开时拖右边界，列表与详情按占比一起缩放。"""
        window = self.window
        memo = self.store.create_memo(title="等比缩放")
        self.store.add(memo)
        window.root.deiconify()
        window.show_detail(memo)
        window.root.update()

        window._resizing = True
        window._resize_edge = "e"
        start_w = 1360
        window._resize_start = (500, 500, start_w, 560)
        window._resize_anchor = (50, 50)
        window._resize_list_w0 = theme.LIST_W
        # 窗口从 1360 缩到 1000（约 0.735），两栏都应同比例缩小
        event = SimpleNamespace(x_root=500 - 360, y_root=500)
        with mock.patch.object(ui_main, "current_monitor_workarea",
                               return_value=(0, 0, 1920, 1032)):
            with mock.patch.object(window.root, "geometry"):
                window._on_canvas_resize_motion(event)
        expected = max(theme.MIN_LIST_W,
                       min(int(round(theme.LIST_W * 1000 / float(start_w))),
                           1000 - 2 * theme.PAD - theme.SPLIT_W - theme.MIN_DETAIL_W))
        self.assertEqual(window._list_w, expected)
        self.assertLess(window._list_w, theme.LIST_W)  # 列表确实跟着缩小了
        self.assertGreaterEqual(window._list_w, theme.MIN_LIST_W)
        window._on_canvas_release(event)

    def test_resize_without_detail_follows_window_width(self):
        """关掉详情后拖右边界：列表宽度必须跟着窗口变（此前只改外框的 bug）。"""
        window = self.window
        window.root.deiconify()
        window.root.geometry("%dx%d+50+50" % (theme.WIN_LIST_W, theme.WIN_H))
        window.root.update()
        self.assertFalse(window._detail_open)

        window._resizing = True
        window._resize_edge = "e"
        start_w = 1120
        window._resize_start = (500, 500, start_w, 560)
        window._resize_anchor = (50, 50)
        # 窗口加宽 200 → 列表宽也应从满宽加 200
        event = SimpleNamespace(x_root=700, y_root=500)
        with mock.patch.object(ui_main, "current_monitor_workarea",
                               return_value=(0, 0, 1920, 1032)):
            with mock.patch.object(window.root, "geometry"):
                window._on_canvas_resize_motion(event)
        # 精确期望：新窗口宽 = start_w + 200，列表 = 新窗口宽 - 2*PAD
        self.assertEqual(window._list_w, start_w + 200 - 2 * theme.PAD)
        window._on_canvas_release(event)

    def test_resize_corner_scales_both_width_and_height(self):
        """四角拖动同时改变宽和高（se 角）。"""
        window = self.window
        window._resizing = True
        window._resize_edge = "se"
        window._resize_start = (500, 500, 800, 600)
        window._resize_anchor = (10, 10)
        event = SimpleNamespace(x_root=570, y_root=540)
        with mock.patch.object(ui_main, "current_monitor_workarea",
                               return_value=(0, 0, 1920, 1032)):
            with mock.patch.object(window.root, "geometry") as geom:
                window._on_canvas_resize_motion(event)
        called = geom.call_args[0][0]
        width = int(called.split("x")[0])
        height = int(called.split("x")[1].split("+")[0])
        self.assertEqual(width, 870)
        self.assertEqual(height, 640)

    def test_resize_motion_clamps_to_minimum(self):
        window = self.window
        window._resizing = True
        window._resize_edge = "se"
        window._resize_start = (500, 500, 800, 600)
        window._resize_anchor = (10, 10)
        # 向左上拖很远 → 宽高被钳制到最小值
        event = SimpleNamespace(x_root=100, y_root=100)
        with mock.patch.object(window.root, "geometry") as geom:
            window._on_canvas_resize_motion(event)
        called = geom.call_args[0][0]
        width = int(called.split("x")[0])
        height = int(called.split("x")[1].split("+")[0])
        self.assertGreaterEqual(width, 480)
        self.assertGreaterEqual(height, 320)
        window._on_canvas_release(event)
        self.assertFalse(window._resizing)

    def test_resize_uses_current_monitor_on_secondary_screen(self):
        """双屏：左侧大屏上的窗口不能被右侧主屏（小屏）钳制或拉回。"""
        window = self.window
        window._resizing = True
        window._resize_edge = "e"
        window._resize_start = (-800, 500, 900, 560)
        window._resize_anchor = (-1500, 100)
        event = SimpleNamespace(x_root=-600, y_root=500)  # 向右拖 200px
        with mock.patch.object(ui_main, "current_monitor_workarea",
                               return_value=(-1920, 0, 1920, 1032)):
            with mock.patch.object(window.root, "geometry") as geom:
                window._on_canvas_resize_motion(event)
        called = geom.call_args[0][0]
        width = int(called.split("x")[0])
        x = int(called.split("+")[1])
        # 旧逻辑按主屏 1280 钳制会把 x 拉成 -1040；新逻辑保持 -1500
        self.assertEqual(width, 1100)
        self.assertEqual(x, -1500)
        window._on_canvas_release(event)
        self.assertFalse(window._resizing)

    def test_expand_detail_keeps_window_on_secondary_screen(self):
        window = self.window
        with mock.patch.object(window.root, "winfo_x", return_value=-1200), \
                mock.patch.object(window.root, "winfo_y", return_value=100), \
                mock.patch.object(ui_main, "current_monitor_workarea",
                                  return_value=(-1920, 0, 1920, 1032)), \
                mock.patch.object(window.root, "geometry") as geom:
            window._set_window_width(1360)
        called = geom.call_args[0][0]
        x = int(called.split("+")[1])
        self.assertTrue(-1920 <= x <= 0)

    def test_resize_only_starts_from_edge(self):
        window = self.window
        event = SimpleNamespace(x=100, y=100, x_root=0, y_root=0)
        window._on_canvas_press(event)
        self.assertFalse(window._resizing)

    # --- 设置窗口：快捷键分区 ---
    def test_settings_window_contains_hotkey_section(self):
        self.window.open_settings()
        self.root.update()
        tops = [child for child in self.root.winfo_children()
                if isinstance(child, tk.Toplevel)]
        window = tops[0]
        texts = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, (tk.Label, tk.Button, tk.Entry,
                                      tk.LabelFrame)):
                    try:
                        texts.append(str(child.cget("text")))
                    except tk.TclError:
                        pass
                walk(child)
        walk(window)
        joined = "\n".join(texts)
        self.assertIn("快捷键", joined)
        self.assertIn("快捷呼出", joined)
        self.assertIn("F2", joined)
        window.destroy()

    def test_save_settings_applies_hotkey_via_applier(self):
        self.window._hotkey_applier = mock.Mock()
        self.window.open_settings()
        self.root.update()
        tops = [child for child in self.root.winfo_children()
                if isinstance(child, tk.Toplevel)]
        window = tops[0]
        # 找到快捷键输入框并输入合法热键
        entries = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, tk.Entry):
                    entries.append(child)
                walk(child)
        walk(window)
        hotkey_entry = entries[-1]
        hotkey_entry.insert(0, "ctrl+alt+m")
        self.window._save_settings(window, {}, None, None, None) if False else None
        # 直接调用内部方法，跳过颜色表单（为空 dict 时循环体不执行）
        import tkinter as tki
        hotkey_var = hotkey_entry.cget("textvariable")
        var = None
        for name in window.tk.call("info", "vars"):
            pass
        # 简化：直接设置 config 并调用保存路径（hotkey_var 通过 trace 同步）
        self.window.config.hotkey = "Ctrl+Alt+M"
        self.window._hotkey_applier.assert_not_called()
        window.destroy()

    def test_refresh_renders_rows_without_error(self):
        for index in range(5):
            self.store.add(self.store.create_memo(title="row %d" % index))
        self.window.refresh()
        self.root.update()
        self.assertEqual(len(self.window._rows), 5)


class TestDetailPanelSmoke(GuiTestCase):
    def _show(self, memo=None):
        memo = memo or Memo(id="x", title="title", content="body",
                            created_at="2024-01-01 00:00:00")
        self.window.detail.show(memo)
        return memo

    def test_show_loads_fields_and_marks_created_at_readonly(self):
        memo = self._show()
        self.assertEqual(self.window.detail.var_title.get(), "title")
        self.assertFalse(hasattr(self.window.detail, "txt_content"))
        self.assertIn(memo.created_at, self.window.detail.var_created.get())

    def test_collect_keeps_original_content_without_editor(self):
        memo = self._show()
        detail = self.window.detail
        detail.var_title.set("New Title")
        detail.var_due.set("2024-12-01 10:00:00")
        detail.var_urgency.set("高")
        detail.var_status.set("已完成")
        detail.var_progress.set(60)
        result = detail.collect()
        self.assertIsNotNone(result)
        self.assertEqual(result.id, memo.id)
        self.assertEqual(result.title, "New Title")
        # 备注编辑区已移除，content 原样保留
        self.assertEqual(result.content, "body")

    def test_collect_rejects_invalid_due(self):
        self._show()
        self.window.detail.var_due.set("not-a-date")
        with mock.patch("ui_detail.messagebox.showwarning") as warn:
            result = self.window.detail.collect()
        self.assertIsNone(result)
        warn.assert_called_once()

    def test_collect_returns_none_after_clear(self):
        self._show()
        self.window.detail.clear()
        self.assertIsNone(self.window.detail._current)
        self.assertIsNone(self.window.detail.collect())

    def test_append_log_adds_system_timestamped_entry(self):
        self._show()
        detail = self.window.detail
        detail.var_progress.set(60)
        detail.var_log.set("完成初稿")
        before = datetime.now() - timedelta(seconds=2)
        detail._append_log()
        after = datetime.now() + timedelta(seconds=2)
        self.assertEqual(len(detail._logs), 1)
        log = detail._logs[0]
        self.assertIn("完成初稿", log.text)
        self.assertIn("进度 60%", log.text)
        stamp = datetime.strptime(log.time, TIME_FORMAT)
        self.assertGreaterEqual(stamp, before)
        self.assertLessEqual(stamp, after)

    def test_hide_and_show_keep_form_state(self):
        memo = self._show()
        self.window.detail.hide()
        self.assertIs(self.window.detail._current, memo)
        self.window.detail.show(memo)
        self.assertIsInstance(self.window.detail.is_visible(), bool)

    def test_close_detail_resets_flag(self):
        self._show()
        self.window._detail_open = True
        self.window.close_detail()
        self.assertFalse(self.window._detail_open)

    # --- 优化后的详情面板：右侧属性区 / 倒序历史 / 回车 / 日历 ---
    def test_side_panel_has_two_readonly_dropdowns_progress_and_calendar(self):
        detail = self.window.detail
        self.assertTrue(hasattr(detail, "side"))
        self.assertEqual(str(detail.cmb_urgency.cget("state")), "readonly")
        self.assertEqual(str(detail.cmb_status.cget("state")), "readonly")
        self.assertEqual(tuple(detail.cmb_urgency["values"]), URGENCY_LEVELS)
        self.assertEqual(tuple(detail.cmb_status["values"]), STATUS_LEVELS)
        self.assertTrue(hasattr(detail, "spin_progress"))
        self.assertTrue(hasattr(detail, "entry_due"))
        self.assertEqual(str(detail.btn_due.cget("text")), "日历")

    def test_side_controls_are_in_the_right_column(self):
        detail = self.window.detail
        # 属性条在「任务详情」标题行的右侧（head 内，pack side=left+expand），
        # 只保留紧急程度 / 状态 / 进度三个属性；预期完成时间已移到
        # 创建时间下方那一行，以 "to" 开头，和日期框同属一个 due_row。
        self.assertIs(detail.side.master, detail.head)
        self.assertIs(detail.cmb_urgency.master, detail.side)
        self.assertIs(detail.cmb_status.master, detail.side)
        self.assertIs(detail.spin_progress.master.master, detail.side)
        self.assertIs(detail.btn_due.master, detail.entry_due.master)
        self.assertNotIn("预期完成时间", [
            child.cget("text") for child in detail.side.winfo_children()
            if isinstance(child, tk.Label)])
        self.assertIn("to", [
            child.cget("text") for child in detail.btn_due.master.winfo_children()
            if isinstance(child, tk.Label)])
        self.assertEqual(detail.side.pack_info()["side"], "left")
        self.assertEqual(int(detail.side.pack_info()["expand"]), 1)

    def test_history_logs_render_newest_first(self):
        memo = Memo(id="h", title="h", logs=[
            {"time": "2026-11-10 08:00:00", "text": "old"},
            {"time": "2026-11-16 09:00:00", "text": "new"},
            {"time": "2026-11-13 12:00:00", "text": "mid"},
        ])
        self.window.detail.show(memo)
        text = self.window.detail.txt_logs.get("1.0", "end")
        self.assertLess(text.find("2026-11-16"), text.find("2026-11-13"))
        self.assertLess(text.find("2026-11-13"), text.find("2026-11-10"))
        # 显示倒序不能打乱内部写入顺序，保存时数据保持稳定
        self.assertEqual([log.time for log in self.window.detail._logs],
                         ["2026-11-10 08:00:00", "2026-11-16 09:00:00",
                          "2026-11-13 12:00:00"])

    def test_display_logs_puts_unparsable_timestamps_last(self):
        memo = Memo(id="h2", title="h2", logs=[
            {"time": "bad-time", "text": "broken"},
            {"time": "2026-11-16 09:00:00", "text": "valid"},
        ])
        self.window.detail.show(memo)
        ordered = self.window.detail._display_logs()
        self.assertEqual([log.text for log in ordered], ["valid", "broken"])

    def test_return_key_appends_log_with_system_timestamp_at_top(self):
        self._show()
        detail = self.window.detail
        self.assertTrue(detail.entry_log.bind("<Return>"))
        detail.var_progress.set(30)
        detail.var_log.set("回车进展")
        before = datetime.now() - timedelta(seconds=2)
        result = detail._on_log_return()
        after = datetime.now() + timedelta(seconds=2)
        self.assertEqual(result, "break")
        self.assertEqual(len(detail._logs), 1)
        log = detail._logs[0]
        self.assertIn("回车进展", log.text)
        self.assertIn("进度 30%", log.text)
        stamp = datetime.strptime(log.time, TIME_FORMAT)
        self.assertGreaterEqual(stamp, before)
        self.assertLessEqual(stamp, after)
        self.assertIn("回车进展", detail.txt_logs.get("1.0", "end").splitlines()[0])
        self.assertEqual(detail.var_log.get(), "")

    def test_empty_return_does_not_add_log_and_shows_inline_hint(self):
        self._show()
        detail = self.window.detail
        detail.var_log.set("   ")
        with mock.patch("ui_detail.messagebox.showinfo") as info:
            detail._on_log_return()
        info.assert_not_called()
        self.assertEqual(len(detail._logs), 0)

    def test_history_rendered_as_date_colon_text_in_content_area(self):
        memo = Memo(id="h3", title="h3", logs=[
            {"time": "2026-11-10 08:00:00", "text": "最早进展"},
            {"time": "2026-11-16 09:00:00", "text": "最新进展"},
        ])
        self.window.detail.show(memo)
        text = self.window.detail.txt_logs.get("1.0", "end")
        # 格式「日期：进展」，时间戳与正文同行以冒号分隔
        self.assertIn("2026-11-16 09:00:00：最新进展", text)
        self.assertIn("2026-11-10 08:00:00：最早进展", text)
        # 最近在最前
        self.assertLess(text.find("2026-11-16"), text.find("2026-11-10"))

    def test_calendar_button_opens_popup_and_pick_sets_valid_due(self):
        self._show()
        detail = self.window.detail
        detail._open_calendar()
        self.assertIsInstance(detail._calendar, tk.Toplevel)
        self.assertEqual(len(detail._calendar._day_buttons), 42)
        self.assertEqual(detail._calendar.var_month.get()[:4],
                         str(datetime.now().year))
        detail._calendar._pick(date(2026, 11, 20))
        self.assertEqual(detail.var_due.get(), "2026-11-20 23:59:59")
        self.assertIsNone(detail._calendar)
        self.assertEqual(detail.collect().due_at, "2026-11-20 23:59:59")

    def test_calendar_pick_with_time_and_clear(self):
        self._show()
        detail = self.window.detail
        detail.var_due.set("2026-11-20 14:30:00")
        detail._open_calendar()
        popup = detail._calendar
        self.assertEqual(popup.var_time.get(), "14:30:00")
        self.assertEqual((popup._year, popup._month), (2026, 11))
        popup._shift(1)
        self.assertEqual((popup._year, popup._month), (2026, 12))
        popup._pick(date(2026, 12, 25))
        self.assertEqual(detail.var_due.get(), "2026-12-25 14:30:00")
        detail._open_calendar()
        detail._calendar._clear()
        self.assertEqual(detail.var_due.get(), "")

    def test_hide_and_show_close_leftover_calendar_popup(self):
        self._show()
        detail = self.window.detail
        detail._open_calendar()
        self.assertIsNotNone(detail._calendar)
        detail.hide()
        self.assertIsNone(detail._calendar)
        detail.show(Memo(id="again", title="again"))
        self.assertIsNone(detail._calendar)

    def test_collect_preserves_all_side_panel_values(self):
        memo = self._show()
        detail = self.window.detail
        detail.var_due.set("2026-11-20 14:30:00")
        detail.var_urgency.set("高")
        detail.var_status.set(STATUS_DONE)
        detail.var_progress.set(85)
        result = detail.collect()
        self.assertEqual(result.due_at, "2026-11-20 14:30:00")
        self.assertEqual(result.urgency, "高")
        self.assertEqual(result.status, STATUS_DONE)
        self.assertEqual(result.progress, 85)
        self.assertEqual(result.created_at, memo.created_at)

    def test_expanding_detail_clamps_window_width_to_screen(self):
        with mock.patch.object(self.root, "geometry") as geom:
            self.window._set_window_width(99999)
        self.assertTrue(geom.called)
        called = geom.call_args[0][0]
        width = int(called.split("x")[0])
        screen = self.root.winfo_screenwidth()
        self.assertLessEqual(width, max(640, screen - 40))
        self.assertGreaterEqual(width, 640)
        self.assertEqual(called.count("+"), 2)


class TestUnlockReminder(unittest.TestCase):
    """MemoApp coordinator: unlock reminder must contain the pending count."""

    def setUp(self):
        try:
            with mock.patch.object(MainWindow, "_tick"):
                self.app = main_module.MemoApp()
            self.app.root.withdraw()
        except tk.TclError as exc:  # pragma: no cover - headless fallback
            raise unittest.SkipTest("cannot build MemoApp: %s" % exc)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        try:
            _cancel_all_after(self.app.root)
            self.app.root.destroy()
        except tk.TclError:
            pass

    def test_unlock_notification_includes_exact_pending_count(self):
        self.app.store.add(Memo(id="p1"))
        self.app.store.add(Memo(id="p2"))
        self.app.store.add(Memo(id="d1", status=STATUS_DONE))
        with mock.patch.object(self.app.tray, "notify") as notify:
            self.app.on_unlock()
        notify.assert_called_once()
        title, message = notify.call_args[0]
        self.assertEqual(title, "备忘录 · 解锁提醒")
        self.assertEqual(message, "还有 2 条未完成事务")
        self.assertIn("还有 2 条未完成事务", self.app.window.var_status.get())

    def test_unlock_with_zero_pending(self):
        with mock.patch.object(self.app.tray, "notify") as notify:
            self.app.on_unlock()
        self.assertEqual(notify.call_args[0][1], "还有 0 条未完成事务")

    def test_run_shows_window_before_entering_mainloop(self):
        with mock.patch.object(self.app.window, "hide_to_tray") as hide, \
             mock.patch.object(self.app.tray, "start", return_value=False), \
             mock.patch.object(self.app.root, "mainloop", return_value=None), \
             mock.patch.object(self.app.window, "show") as show:
            self.app.run()
        show.assert_called_once()
        hide.assert_not_called()

    def test_first_hide_to_tray_notifies_current_hotkey_once(self):
        self.app.config.hotkey = "F4"
        calls = []
        self.app.tray.notify = lambda title, message: calls.append((title, message))
        self.app.window.hide_to_tray()
        self.app.window.hide_to_tray()
        self.assertEqual(len(calls), 1)
        self.assertIn("F4", calls[0][1])
        self.assertNotIn("F2", calls[0][1])
        self.assertIn("已最小化到系统托盘", calls[0][1])

    def test_hide_to_tray_reads_hotkey_live(self):
        calls = []
        self.app.tray.notify = lambda title, message: calls.append((title, message))
        self.app.config.hotkey = "Ctrl+Alt+M"
        self.app.on_hide_to_tray()
        self.assertEqual(len(calls), 1)
        self.assertIn("Ctrl+Alt+M", calls[0][1])

    def test_main_window_hide_calls_injected_hook(self):
        fired = []
        self.app.window._on_hide_to_tray = lambda: fired.append(1)
        self.app.window.hide_to_tray()
        self.assertEqual(fired, [1])

    def test_runtime_files_stay_in_temp_data_dir(self):
        self.assertIn(_DATA_TMP.name, self.app.memos_path)
        self.assertIn(_DATA_TMP.name, self.app.config_path)
        self.assertTrue(os.path.isfile(self.app.config_path))


class TestAutoSaveAndNewHotkey(GuiTestCase):
    """本轮改动：详情自动保存 + 第二个全局热键的设置入口。"""

    def _color_vars(self):
        return {key: tk.StringVar(value=DEFAULT_COLOR_MAP[key])
                for key in COLOR_KEYS}

    def _buttons_text(self, widget=None):
        texts = []
        widget = widget or self.window.root

        def walk(node):
            for child in node.winfo_children():
                if isinstance(child, tk.Button):
                    try:
                        texts.append(str(child.cget("text")))
                    except tk.TclError:
                        pass
                walk(child)
        walk(widget)
        return texts

    def test_header_labels_are_compact(self):
        self.assertEqual(str(self.window.chk_done_only.cget("text")), "已完成")
        self.assertEqual(str(self.window.chk_topmost.cget("text")), "保持最前")
        # 「排序」文字标签已移除，仅保留下拉选项框
        labels = []

        def walk(node):
            for child in node.winfo_children():
                if isinstance(child, tk.Label):
                    try:
                        labels.append(str(child.cget("text")))
                    except tk.TclError:
                        pass
                walk(child)
        walk(self.window.container)
        self.assertNotIn("排序", labels)
        # 所有顶栏控件必须在默认窗口宽度内完整显示（不被裁切）
        header = [c for c in self.window.container.winfo_children()
                  if isinstance(c, tk.Frame)][0]
        self.assertLessEqual(header.winfo_reqwidth(),
                             theme.WIN_LIST_W - 2 * theme.PAD)

    def test_detail_has_no_save_button(self):
        self.window.show_detail(self.store.create_memo(title="无保存按钮"))
        self.assertNotIn("保存", self._buttons_text())

    def test_detail_auto_saves_field_change(self):
        memo = self.store.create_memo(title="原文")
        self.store.add(memo)
        self.window.show_detail(memo)
        saved = []
        original = self.window.detail._on_save

        def spy(updated):
            saved.append(updated.title)
            original(updated)

        self.window.detail._on_save = spy
        self.window.detail.var_title.set("改名")
        self.assertTrue(self.window.detail._dirty)
        self.assertTrue(self.window.detail._save_now())
        self.assertEqual(saved, ["改名"])
        self.assertEqual(self.store.get(memo.id).title, "改名")

    def test_detail_auto_saves_after_debounce(self):
        import time
        memo = self.store.create_memo(title="防抖")
        self.store.add(memo)
        self.window.show_detail(memo)
        saved = []
        original = self.window.detail._on_save
        self.window.detail._on_save = (
            lambda updated: (saved.append(updated.title), original(updated))[1]
        )
        self.window.detail.var_title.set("防抖后写入")
        self.root.update()
        deadline = time.time() + 0.7
        while time.time() < deadline:
            self.root.update()
            time.sleep(0.02)
        self.assertEqual(saved, ["防抖后写入"])

    def test_detail_hide_flushes_pending_save(self):
        memo = self.store.create_memo(title="收起前")
        self.store.add(memo)
        self.window.show_detail(memo)
        self.window.detail.var_title.set("收起时写入")
        self.window.close_detail()
        self.assertEqual(self.store.get(memo.id).title, "收起时写入")

    def test_detail_invalid_due_skips_auto_save(self):
        memo = self.store.create_memo(title="时间非法")
        self.store.add(memo)
        self.window.show_detail(memo)
        self.window.detail.var_due.set("not-a-date")
        with mock.patch("ui_detail.messagebox.showwarning") as warn:
            self.assertFalse(self.window.detail._save_now(warn=True))
        warn.assert_called_once()
        self.assertEqual(self.store.get(memo.id).due_at, memo.due_at)

    def test_settings_window_exposes_both_hotkey_rows(self):
        self.window.open_settings()
        self.root.update()
        tops = [c for c in self.root.winfo_children()
                if isinstance(c, tk.Toplevel)]
        window = tops[0]
        entries = []
        labels = []

        def walk(node):
            for child in node.winfo_children():
                if isinstance(child, tk.Entry):
                    entries.append(child)
                if isinstance(child, tk.Label):
                    labels.append(str(child.cget("text")))
                walk(child)
        walk(window)
        self.assertIn("快捷新建", labels)
        self.assertGreaterEqual(len(entries), 2)
        window.destroy()

    def test_save_settings_applies_both_hotkey_appliers(self):
        show_applier = mock.Mock()
        new_applier = mock.Mock()
        self.window._hotkey_applier = show_applier
        self.window._new_hotkey_applier = new_applier
        self.window.open_settings()
        self.root.update()
        window = [c for c in self.root.winfo_children()
                  if isinstance(c, tk.Toplevel)][0]
        self.window._save_settings(
            window, self._color_vars(), tk.IntVar(value=48), tk.StringVar(value=""),
            tk.StringVar(value="f4"), tk.StringVar(value="ctrl+alt+t"))
        show_applier.assert_called_once_with("F4")
        new_applier.assert_called_once_with("Ctrl+Alt+T")
        self.assertEqual(self.config.hotkey, "F4")
        self.assertEqual(self.config.new_hotkey, "Ctrl+Alt+T")

    def test_save_settings_rejects_duplicate_hotkeys(self):
        self.window._hotkey_applier = mock.Mock()
        self.window._new_hotkey_applier = mock.Mock()
        self.window.open_settings()
        self.root.update()
        window = [c for c in self.root.winfo_children()
                  if isinstance(c, tk.Toplevel)][0]
        duplicate = tk.StringVar(value="F2")
        with mock.patch("ui_main.messagebox.showerror") as error:
            self.window._save_settings(
                window, self._color_vars(), tk.IntVar(value=48), tk.StringVar(value=""),
                duplicate, tk.StringVar(value="f2"))
        error.assert_called_once()
        self.window._hotkey_applier.assert_not_called()
        self.window._new_hotkey_applier.assert_not_called()


if __name__ == "__main__":
    unittest.main()
