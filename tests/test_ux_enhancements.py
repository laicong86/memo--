# -*- coding: utf-8 -*-
"""第三轮体验优化的回归：日历就近弹出、HH:MM 时间框、解锁临期提醒。"""
from __future__ import annotations

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

# 注意：不要修改 MEMO_DATA_DIR 全局环境变量，避免影响其它测试模块。
import tkinter as tk

from calendar_picker import (display_time_text, join_due_text,
                             place_near_host, split_due_text)
from config import (AppConfig, COLOR_KEYS, DEFAULT_AUTO_START,
                    DEFAULT_UNLOCK_DUE_HOURS)
from main import format_unlock_reminder, upcoming_due_memos
from storage import MemoStore
from ui_main import MainWindow


class TestTimeFields(unittest.TestCase):
    def test_split_join_and_display_use_hh_mm(self):
        self.assertEqual(split_due_text("2026-11-20 14:30:00"),
                         ("2026-11-20", "14:30"))
        self.assertEqual(display_time_text("2026-11-20 14:30:00"), "14:30")
        self.assertEqual(join_due_text("2026-11-20", "15:30"),
                         "2026-11-20 15:30:00")
        self.assertEqual(join_due_text("", "15:30"), "")
        self.assertEqual(join_due_text("2026-11-20", "bad"),
                         "2026-11-20 23:59:59")
        self.assertEqual(join_due_text("2026-11-20", ""),
                         "2026-11-20 23:59:59")


class TestCalendarPlacement(unittest.TestCase):
    HOST = (800, 500, 860, 560)

    def test_place_below_anchor_and_flip_left_near_right_edge(self):
        # 锚点靠近主窗口右缘 → 弹窗改到锚点左侧，仍完整落在主窗口内
        x, y = place_near_host((1450, 700, 60, 25), (320, 300), self.HOST)
        self.assertEqual((x, y), (1190, 731))
        hx, hy, hw, hh = self.HOST
        self.assertTrue(hx <= x and hy <= y)
        self.assertTrue(x + 320 <= hx + hw and y + 300 <= hy + hh)

    def test_place_flips_above_anchor_near_bottom(self):
        x, y = place_near_host((1000, 1000, 60, 25), (320, 300), self.HOST)
        self.assertEqual(y, 694)
        self.assertLessEqual(y + 300, self.HOST[1] + self.HOST[3])

    def test_place_clamps_anchor_outside_host(self):
        x, y = place_near_host((5000, 5000, 60, 25), (320, 300), self.HOST)
        self.assertEqual((x, y), (1332, 752))

    def test_place_keeps_negative_x_on_left_monitor(self):
        """主窗口在左侧副屏（负 x）时，弹窗不能被 max(0,...) 甩回主屏。"""
        # 左侧大屏工作区 x=-1920..0，主窗口落在 -1500..-640
        host = (-1500, 100, 860, 560)
        x, y = place_near_host((-700, 300, 60, 25), (320, 300), host)
        self.assertLess(x, 0)  # 关键：保持负坐标
        hx, hy, hw, hh = host
        self.assertGreaterEqual(x, hx)
        self.assertLessEqual(x + 320, hx + hw)
        self.assertGreaterEqual(y, hy)
        self.assertLessEqual(y + 300, hy + hh)


class TestUnlockDueLogic(unittest.TestCase):
    NOW = datetime(2026, 11, 16, 12, 0, 0)

    def _memo(self, title, delta_hours=None, status="未完成"):
        due = ""
        if delta_hours is not None:
            due = (self.NOW + timedelta(hours=delta_hours)).strftime(
                "%Y-%m-%d %H:%M:%S")
        return SimpleNamespace(title=title, due_at=due, status=status)

    def test_only_pending_due_within_window_sorted(self):
        memos = [
            self._memo("30小时后", 30),
            self._memo("五小时后", 5),
            self._memo("已完成", 2, status="已完成"),
            self._memo("已超期", -3),
            self._memo("无时间"),
        ]
        items = upcoming_due_memos(memos, 24, self.NOW)
        self.assertEqual([m.title for m, _ in items], ["五小时后"])

    def test_boundaries_are_inclusive(self):
        memos = [self._memo("整24小时", 24), self._memo("刚过24小时", 24.01)]
        items = upcoming_due_memos(memos, 24, self.NOW)
        self.assertEqual([m.title for m, _ in items], ["整24小时"])

    def test_reminder_text_lists_titles_and_countdown(self):
        items = upcoming_due_memos([self._memo("五小时后", 5)], 24, self.NOW)
        text = format_unlock_reminder(7, items, 24, self.NOW)
        self.assertIn("还有 7 条未完成事务", text)
        self.assertIn("24小时内到期", text)
        self.assertIn("五小时后", text)
        self.assertIn("5.0小时后", text)

    def test_no_upcoming_items_returns_plain_count(self):
        self.assertEqual(format_unlock_reminder(3, [], 24, self.NOW),
                         "还有 3 条未完成事务")


class TestUnlockHoursConfig(unittest.TestCase):
    def test_default_and_round_trip(self):
        self.assertEqual(AppConfig().unlock_due_hours, DEFAULT_UNLOCK_DUE_HOURS)
        folder = tempfile.TemporaryDirectory(prefix="memo_cfg_ux_")
        self.addCleanup(folder.cleanup)
        path = os.path.join(folder.name, "config.json")
        AppConfig(unlock_due_hours=12).save(path)
        self.assertEqual(AppConfig.load(path).unlock_due_hours, 12)
        self.assertEqual(AppConfig(unlock_due_hours="abc").unlock_due_hours,
                         DEFAULT_UNLOCK_DUE_HOURS)


class _TkCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="memo_ux_gui_")
        self.addCleanup(self.folder.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self._destroy)
        self.store = MemoStore(os.path.join(self.folder.name, "m.json")).load()
        self.config = AppConfig()
        with mock.patch.object(MainWindow, "_tick"):
            self.window = MainWindow(
                self.root, self.store, self.config,
                os.path.join(self.folder.name, "c.json"))

    def _destroy(self):
        try:
            for child in list(self.root.winfo_children()):
                if isinstance(child, tk.Toplevel):
                    child.destroy()
            self.root.destroy()
        except tk.TclError:
            pass

    def _color_vars(self):
        return {key: tk.StringVar(value=self.config.get_color(key))
                for key in COLOR_KEYS}


class TestAutoStartGui(_TkCase):
    def test_settings_persists_and_applies_autostart(self):
        applied = []  # type: list
        self.window._autostart_applier = applied.append
        auto_var = tk.BooleanVar(value=True)
        self.window._save_settings(
            tk.Toplevel(self.root), self._color_vars(),
            tk.IntVar(value=48), tk.StringVar(value=""),
            tk.StringVar(value="F2"), tk.StringVar(value="Ctrl+Alt+N"),
            tk.IntVar(value=24), auto_var)
        self.assertIs(self.config.auto_start, True)
        self.assertEqual(applied, [True])
        reloaded = AppConfig.load(self.window.config_path)
        self.assertIs(reloaded.auto_start, True)

    def test_reset_restores_default_autostart(self):
        var = tk.BooleanVar(value=True)
        self.window._reset_settings(
            self._color_vars(),
            {key: tk.Label(self.root) for key in COLOR_KEYS},
            tk.IntVar(value=48), tk.StringVar(value=""),
            auto_start_var=var)
        self.assertEqual(var.get(), DEFAULT_AUTO_START)


class TestDetailTimeGui(_TkCase):
    def test_time_field_displays_hh_mm_and_autosaves(self):
        memo = self.store.create_memo(title="时间测试",
                                      due_at="2026-11-20 14:30:00")
        self.store.add(memo)
        self.window.show_detail(memo)
        self.root.update()
        self.assertEqual(self.window.detail.var_due_date.get(), "2026-11-20")
        self.assertEqual(self.window.detail.var_due_time.get(), "14:30")
        self.window.detail.var_due_time.set("15:30")
        self.root.update()
        self.assertEqual(self.window.detail.var_due.get(),
                         "2026-11-20 15:30:00")
        self.assertTrue(self.window.detail._save_now())
        self.assertEqual(self.store.get(memo.id).due_at,
                         "2026-11-20 15:30:00")

    def test_time_typed_before_picking_date_is_kept(self):
        """先填时间、后选日期：选完日期后已填的 15:30 不能被日历清掉。"""
        memo = self.store.create_memo(title="先填时间")
        self.store.add(memo)
        self.window.show_detail(memo)
        self.root.update()
        detail = self.window.detail
        # 用户先清空默认日期、填好时间，再点日历选日期
        detail.var_due_date.set("")
        detail.var_due_time.set("15:30")
        self.root.update()
        # 日期为空时完整 due 仍为空，但时间框里已经有用户输入
        self.assertEqual(detail.var_due.get(), "")
        self.assertEqual(detail.var_due_time.get(), "15:30")

        detail._open_calendar()
        popup = detail._calendar
        self.assertIsNotNone(popup)
        try:
            self.assertEqual(popup.var_time.get(), "15:30:00")
            popup._pick(date(2026, 11, 20))
        finally:
            if detail._calendar is not None:
                detail._calendar._close()
        self.assertEqual(detail.var_due.get(), "2026-11-20 15:30:00")
        self.assertEqual(detail.var_due_date.get(), "2026-11-20")
        self.assertEqual(detail.var_due_time.get(), "15:30")
        self.assertTrue(detail._save_now())
        self.assertEqual(self.store.get(memo.id).due_at,
                         "2026-11-20 15:30:00")

    def test_empty_time_field_defaults_to_end_of_day(self):
        """时间框留空 / 非法时，只选日期也按当天 23:59:59 保存。"""
        memo = self.store.create_memo(title="默认时间")
        self.store.add(memo)
        self.window.show_detail(memo)
        self.root.update()
        detail = self.window.detail
        detail.var_due_date.set("2026-11-22")
        detail.var_due_time.set("")
        self.root.update()
        self.assertEqual(detail.var_due.get(), "2026-11-22 23:59:59")
        self.assertTrue(detail._save_now())
        self.assertEqual(self.store.get(memo.id).due_at,
                         "2026-11-22 23:59:59")

    def test_calendar_popup_is_near_button_and_inside_host(self):
        self.window.show()
        self.root.update()
        self.window.detail._open_calendar()
        self.root.update()
        popup = self.window.detail._calendar
        self.assertIsNotNone(popup)
        try:
            hx, hy = self.root.winfo_rootx(), self.root.winfo_rooty()
            hw, hh = self.root.winfo_width(), self.root.winfo_height()
            px, py = popup.winfo_rootx(), popup.winfo_rooty()
            if hw > 1 and hh > 1:
                self.assertLessEqual(hx, px)
                self.assertLessEqual(hy, py)
                self.assertLessEqual(px + popup.winfo_width(), hx + hw)
                self.assertLessEqual(py + popup.winfo_height(), hy + hh)
            self.assertIsNotNone(popup.var_time)
        finally:
            popup._close()

    def test_settings_saves_and_resets_unlock_hours(self):
        unlock_var = tk.IntVar(value=12)
        colors = self._color_vars()
        previews = {key: tk.Label(self.root) for key in COLOR_KEYS}
        self.window._reset_settings(
            colors, previews, tk.IntVar(value=48), tk.StringVar(value=""),
            unlock_hours_var=unlock_var)
        self.assertEqual(unlock_var.get(), DEFAULT_UNLOCK_DUE_HOURS)
        unlock_var.set(12)
        self.window._save_settings(
            tk.Toplevel(self.root), colors, tk.IntVar(value=48),
            tk.StringVar(value=""), tk.StringVar(value="F2"),
            tk.StringVar(value="Ctrl+Alt+N"), unlock_var)
        self.assertEqual(self.config.unlock_due_hours, 12)


if __name__ == "__main__":
    unittest.main()
