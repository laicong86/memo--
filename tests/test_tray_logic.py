# -*- coding: utf-8 -*-
"""Independent tests for the platform-independent logic of tray.py.

Only pure-Python paths are exercised: callback dispatch, command routing,
tray-icon click messages and WTS unlock forwarding. No Win32 window,
message loop or tray icon is created, so the suite is side-effect free.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import tray
from session_watcher import (
    WM_WTSSESSION_CHANGE,
    WTS_SESSION_LOCK,
    WTS_SESSION_UNLOCK,
)
from tray import (
    ID_EXIT,
    ID_NEW,
    ID_SHOW,
    IS_WINDOWS,
    WM_COMMAND,
    WM_LBUTTONDBLCLK,
    WM_LBUTTONUP,
    WM_TRAYICON,
    TrayIcon,
)


class TestTrayInitialState(unittest.TestCase):
    def test_initial_state(self):
        icon = TrayIcon()
        self.assertFalse(icon.is_running())
        self.assertIsInstance(icon.is_available(), bool)
        self.assertEqual(icon._hwnd, 0)
        self.assertFalse(icon._icon_added)
        self.assertFalse(icon._registered_class)
        self.assertTrue(icon._pending.empty())

    def test_platform_flag(self):
        self.assertEqual(IS_WINDOWS, sys.platform == "win32")

    def test_required_public_api_exists(self):
        for name in ("start", "stop", "notify", "is_available", "is_running"):
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(TrayIcon, name, None)))


class TestTrayCallbackDispatch(unittest.TestCase):
    def test_fire_invokes_callback_directly(self):
        events = []
        icon = TrayIcon(on_show=lambda: events.append("show"))
        icon._fire(icon._on_show)
        self.assertEqual(events, ["show"])

    def test_fire_passes_positional_args(self):
        seen = []
        icon = TrayIcon()
        icon._fire(lambda a, b: seen.append((a, b)), 1, 2)
        self.assertEqual(seen, [(1, 2)])

    def test_fire_prefers_dispatcher_when_provided(self):
        dispatched = []
        executed = []
        icon = TrayIcon(on_show=lambda: executed.append(1),
                        dispatcher=lambda fn, args: dispatched.append((fn, args)))
        icon._fire(icon._on_show)
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0][1], ())
        self.assertEqual(executed, [])

    def test_fire_falls_back_to_direct_call_if_dispatcher_raises(self):
        executed = []

        def broken_dispatcher(fn, args):
            raise RuntimeError("dispatcher down")

        icon = TrayIcon(on_show=lambda: executed.append(1),
                        dispatcher=broken_dispatcher)
        icon._fire(icon._on_show)
        self.assertEqual(executed, [1])

    def test_fire_handles_none_callback_and_callback_exception(self):
        icon = TrayIcon()
        icon._fire(None)

        def boom():
            raise RuntimeError("boom")

        icon2 = TrayIcon(on_show=boom)
        icon2._fire(icon2._on_show)  # must not propagate


class TestTrayCommandRouting(unittest.TestCase):
    def test_show_command(self):
        events = []
        icon = TrayIcon(on_show=lambda: events.append("show"))
        icon._handle_command(ID_SHOW)
        self.assertEqual(events, ["show"])

    def test_new_command(self):
        events = []
        icon = TrayIcon(on_new=lambda: events.append("new"))
        icon._handle_command(ID_NEW)
        self.assertEqual(events, ["new"])

    def test_exit_command(self):
        events = []
        icon = TrayIcon(on_exit=lambda: events.append("exit"))
        icon._handle_command(ID_EXIT)
        self.assertEqual(events, ["exit"])

    def test_unknown_command_is_noop(self):
        events = []
        icon = TrayIcon(on_show=lambda: events.append("show"),
                        on_new=lambda: events.append("new"),
                        on_exit=lambda: events.append("exit"))
        for command in (0, 9999, -1):
            icon._handle_command(command)
        self.assertEqual(events, [])

    def test_command_ids_are_distinct(self):
        self.assertEqual(len({ID_SHOW, ID_NEW, ID_EXIT}), 3)


class TestTrayMessageHandling(unittest.TestCase):
    def setUp(self):
        # Neutralize DefWindowProcW fallback so no real Win32 call is made
        # for unhandled messages (hwnd is 0 in these pure-logic tests).
        patcher = mock.patch.object(tray, "_user32", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_left_click_on_tray_icon_shows_window(self):
        shown = []
        icon = TrayIcon(on_show=lambda: shown.append(1))
        self.assertEqual(icon._handle_message(0, WM_TRAYICON, 0, WM_LBUTTONUP), 0)
        self.assertEqual(len(shown), 1)

    def test_double_click_on_tray_icon_shows_window(self):
        shown = []
        icon = TrayIcon(on_show=lambda: shown.append(1))
        self.assertEqual(icon._handle_message(0, WM_TRAYICON, 0, WM_LBUTTONDBLCLK), 0)
        self.assertEqual(len(shown), 1)

    def test_low_word_of_lparam_is_used_as_event(self):
        shown = []
        icon = TrayIcon(on_show=lambda: shown.append(1))
        icon._handle_message(0, WM_TRAYICON, 0, 0x12340000 | WM_LBUTTONUP)
        self.assertEqual(len(shown), 1)

    def test_wm_command_routes_menu_command(self):
        shown = []
        icon = TrayIcon(on_show=lambda: shown.append(1))
        self.assertEqual(icon._handle_message(0, WM_COMMAND, ID_SHOW, 0), 0)
        self.assertEqual(len(shown), 1)

    def test_wts_unlock_message_fires_unlock_callback(self):
        unlocked = []
        icon = TrayIcon(on_unlock=lambda: unlocked.append(1))
        self.assertEqual(
            icon._handle_message(0, WM_WTSSESSION_CHANGE, WTS_SESSION_UNLOCK, 0), 0)
        self.assertEqual(len(unlocked), 1)

    def test_wts_lock_message_must_not_fire_unlock_callback(self):
        unlocked = []
        icon = TrayIcon(on_unlock=lambda: unlocked.append(1))
        self.assertEqual(
            icon._handle_message(0, WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK, 0), 0)
        self.assertEqual(unlocked, [])

    def test_unrelated_message_does_not_fire_callbacks(self):
        events = []
        icon = TrayIcon(on_show=lambda: events.append("show"),
                        on_unlock=lambda: events.append("unlock"))
        icon._handle_message(0, WM_WTSSESSION_CHANGE, 999, 0)
        icon._handle_message(0, 0x0400, WTS_SESSION_UNLOCK, 0)
        self.assertEqual(events, [])


class TestTrayNotifyQueue(unittest.TestCase):
    def test_notify_enqueues_without_a_running_thread(self):
        icon = TrayIcon()
        icon.notify("备忘录 · 解锁提醒", "还有 3 条未完成事务")
        self.assertEqual(icon._pending.get_nowait(),
                         ("备忘录 · 解锁提醒", "还有 3 条未完成事务"))

    def test_notify_normalizes_none_to_empty_strings(self):
        icon = TrayIcon()
        icon.notify(None, None)
        self.assertEqual(icon._pending.get_nowait(), ("", ""))

    def test_multiple_notifies_preserve_fifo_order(self):
        icon = TrayIcon()
        icon.notify("a", "1")
        icon.notify("b", "2")
        self.assertEqual(icon._pending.get_nowait(), ("a", "1"))
        self.assertEqual(icon._pending.get_nowait(), ("b", "2"))

    def test_stop_before_start_is_a_noop(self):
        icon = TrayIcon()
        icon.stop()
        self.assertFalse(icon.is_running())


if __name__ == "__main__":
    unittest.main()
