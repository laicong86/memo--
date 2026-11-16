# -*- coding: utf-8 -*-
"""Independent unit tests for session_watcher.py.

Covers WTS unlock/lock event classification and SessionWatcher callback
dispatch, including the requirement that lock/logon/logoff must NOT trigger
the unlock reminder. Pure-logic paths only, no live Win32 window is created.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import session_watcher
from session_watcher import (
    IS_WINDOWS,
    NOTIFY_FOR_ALL_SESSIONS,
    NOTIFY_FOR_THIS_SESSION,
    WM_WTSSESSION_CHANGE,
    WTS_SESSION_LOCK,
    WTS_SESSION_LOGOFF,
    WTS_SESSION_LOGON,
    WTS_SESSION_UNLOCK,
    SessionWatcher,
    is_lock_event,
    is_unlock_event,
    register_session_notification,
    unregister_session_notification,
)

WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_USER = 0x0400


class TestWin32Constants(unittest.TestCase):
    def test_session_change_constants(self):
        self.assertEqual(WM_WTSSESSION_CHANGE, 0x02B1)
        self.assertEqual(WTS_SESSION_LOGON, 0x5)
        self.assertEqual(WTS_SESSION_LOGOFF, 0x6)
        self.assertEqual(WTS_SESSION_LOCK, 0x7)
        self.assertEqual(WTS_SESSION_UNLOCK, 0x8)
        self.assertEqual(NOTIFY_FOR_THIS_SESSION, 0)
        self.assertEqual(NOTIFY_FOR_ALL_SESSIONS, 1)

    def test_platform_flag_matches_sys_platform(self):
        self.assertEqual(IS_WINDOWS, sys.platform == "win32")

    def test_module_exposes_required_api(self):
        for name in ("is_unlock_event", "is_lock_event", "register_session_notification",
                     "unregister_session_notification", "SessionWatcher"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(session_watcher, name))


class TestIsUnlockEvent(unittest.TestCase):
    def test_true_only_for_unlock_message_and_wparam(self):
        self.assertTrue(is_unlock_event(WM_WTSSESSION_CHANGE, WTS_SESSION_UNLOCK))
        self.assertTrue(is_unlock_event(0x02B1, 0x8))

    def test_false_for_lock_logon_logoff_and_other_wparams(self):
        for wparam in (WTS_SESSION_LOCK, WTS_SESSION_LOGON, WTS_SESSION_LOGOFF,
                       0, 1, 9, 0x8 + 0x10000, -1, 99999):
            with self.subTest(wparam=wparam):
                self.assertFalse(is_unlock_event(WM_WTSSESSION_CHANGE, wparam))

    def test_false_for_other_messages_even_with_unlock_wparam(self):
        for msg in (WM_CLOSE, WM_COMMAND, WM_USER, 0, 0x02B0, 0x02B2, 0x8000):
            with self.subTest(msg=msg):
                self.assertFalse(is_unlock_event(msg, WTS_SESSION_UNLOCK))

    def test_accepts_numeric_strings_via_int_conversion(self):
        self.assertTrue(is_unlock_event("689", "8"))
        self.assertFalse(is_unlock_event("689", "7"))

    def test_is_lock_event(self):
        self.assertTrue(is_lock_event(WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK))
        self.assertFalse(is_lock_event(WM_WTSSESSION_CHANGE, WTS_SESSION_UNLOCK))
        self.assertFalse(is_lock_event(WM_CLOSE, WTS_SESSION_LOCK))


class TestSessionWatcher(unittest.TestCase):
    def test_initial_state(self):
        watcher = SessionWatcher()
        self.assertEqual(watcher.hwnd, 0)
        self.assertFalse(watcher.registered)
        self.assertIsNone(watcher.on_unlock)
        self.assertIsNone(watcher.on_lock)

    def test_handle_message_unlock_triggers_only_unlock_callback(self):
        events = []
        watcher = SessionWatcher(on_unlock=lambda: events.append("unlock"),
                                 on_lock=lambda: events.append("lock"))
        self.assertTrue(watcher.handle_message(WM_WTSSESSION_CHANGE, WTS_SESSION_UNLOCK))
        self.assertEqual(events, ["unlock"])

    def test_handle_message_lock_must_not_trigger_unlock_reminder(self):
        events = []
        watcher = SessionWatcher(on_unlock=lambda: events.append("unlock"),
                                 on_lock=lambda: events.append("lock"))
        self.assertTrue(watcher.handle_message(WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK))
        self.assertEqual(events, ["lock"])
        self.assertNotIn("unlock", events)

    def test_handle_message_logon_logoff_are_ignored(self):
        events = []
        watcher = SessionWatcher(on_unlock=lambda: events.append("unlock"))
        for wparam in (WTS_SESSION_LOGON, WTS_SESSION_LOGOFF, 0, 123):
            with self.subTest(wparam=wparam):
                self.assertFalse(watcher.handle_message(WM_WTSSESSION_CHANGE, wparam))
        self.assertEqual(events, [])

    def test_handle_message_irrelevant_msg_is_ignored(self):
        events = []
        watcher = SessionWatcher(on_unlock=lambda: events.append("unlock"))
        self.assertFalse(watcher.handle_message(WM_CLOSE, WTS_SESSION_UNLOCK))
        self.assertFalse(watcher.handle_message(WM_COMMAND, WTS_SESSION_UNLOCK))
        self.assertEqual(events, [])

    def test_handle_message_with_no_callbacks_is_safe(self):
        watcher = SessionWatcher()
        self.assertTrue(watcher.handle_message(WM_WTSSESSION_CHANGE, WTS_SESSION_UNLOCK))
        self.assertTrue(watcher.handle_message(WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK))
        self.assertFalse(watcher.handle_message(WM_CLOSE, 0))

    def test_register_zero_hwnd_returns_false_without_win32_call(self):
        watcher = SessionWatcher()
        self.assertFalse(watcher.register(0))
        self.assertFalse(watcher.register(None))
        self.assertFalse(watcher.registered)

    def test_unregister_without_registration_is_safe(self):
        watcher = SessionWatcher()
        self.assertTrue(watcher.unregister())
        self.assertFalse(watcher.registered)

    def test_module_level_register_with_zero_hwnd_returns_false(self):
        self.assertFalse(register_session_notification(0))
        self.assertFalse(unregister_session_notification(0))


if __name__ == "__main__":
    unittest.main()
