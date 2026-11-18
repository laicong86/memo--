# -*- coding: utf-8 -*-
"""Independent unit tests for models.py.

Covers default field values, local-system-time creation timestamp, time
parsing/validation, progress normalization, serialization and helpers.
No project source file is modified by this test suite.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import models
from models import (
    Memo,
    ProgressLog,
    SORT_CREATED,
    SORT_DUE,
    SORT_MODE_LABELS,
    SORT_MODES,
    SORT_URGENCY,
    STATUS_DONE,
    STATUS_LEVELS,
    STATUS_PENDING,
    TIME_FORMAT,
    URGENCY_HIGH,
    URGENCY_LEVELS,
    URGENCY_LOW,
    URGENCY_MEDIUM,
    URGENCY_ORDER,
    is_valid_time_text,
    normalize_progress,
    now_dt,
    now_str,
    parse_time,
)


class TestMemoDefaults(unittest.TestCase):
    def test_default_field_values(self):
        m = Memo()
        self.assertEqual(m.title, "")
        self.assertEqual(m.content, "")
        self.assertEqual(m.due_at, "")
        self.assertEqual(m.urgency, URGENCY_MEDIUM)
        self.assertEqual(m.status, STATUS_PENDING)
        self.assertEqual(m.progress, 0)
        self.assertEqual(m.logs, [])
        self.assertTrue(m.id)
        self.assertNotEqual(m.created_at, "")

    def test_id_is_auto_generated_and_unique(self):
        ids = {Memo().id for _ in range(50)}
        self.assertEqual(len(ids), 50)

    def test_created_at_is_local_system_time(self):
        before = datetime.now() - timedelta(seconds=2)
        m = Memo()
        after = datetime.now() + timedelta(seconds=2)
        created = datetime.strptime(m.created_at, TIME_FORMAT)
        self.assertGreaterEqual(created, before)
        self.assertLessEqual(created, after)

    def test_created_at_is_not_utc(self):
        local = datetime.now()
        utc = datetime.utcnow()
        created = datetime.strptime(Memo().created_at, TIME_FORMAT)
        self.assertLess(abs((created - local).total_seconds()), 5)
        # On a machine with a real UTC offset, UTC would differ by hours.
        if abs((local - utc).total_seconds()) > 60:
            self.assertGreater(abs((created - utc).total_seconds()), 60)

    def test_now_str_roundtrips_to_local_now(self):
        value = now_str()
        parsed = datetime.strptime(value, TIME_FORMAT)
        self.assertEqual(len(value), 19)
        self.assertLess(abs((parsed - datetime.now()).total_seconds()), 3)

    def test_now_dt_is_naive_local_datetime(self):
        value = now_dt()
        self.assertIsInstance(value, datetime)
        self.assertIsNone(value.tzinfo)
        self.assertLess(abs((value - datetime.now()).total_seconds()), 3)

    def test_invalid_urgency_falls_back_to_medium(self):
        self.assertEqual(Memo(urgency="URGENT").urgency, URGENCY_MEDIUM)
        self.assertEqual(Memo(urgency="").urgency, URGENCY_MEDIUM)
        self.assertEqual(Memo(urgency=None).urgency, URGENCY_MEDIUM)

    def test_invalid_status_falls_back_to_pending(self):
        self.assertEqual(Memo(status="CLOSED").status, STATUS_PENDING)
        self.assertEqual(Memo(status="").status, STATUS_PENDING)
        self.assertEqual(Memo(status=None).status, STATUS_PENDING)

    def test_progress_is_normalized_on_construction(self):
        self.assertEqual(Memo(progress=-20).progress, 0)
        self.assertEqual(Memo(progress=250).progress, 100)
        self.assertEqual(Memo(progress="abc").progress, 0)
        self.assertEqual(Memo(progress="65").progress, 65)

    def test_explicit_created_at_is_preserved(self):
        stamp = "2020-05-06 07:08:09"
        self.assertEqual(Memo(created_at=stamp).created_at, stamp)

    def test_explicit_id_is_preserved(self):
        self.assertEqual(Memo(id="fixed-id").id, "fixed-id")


class TestMemoSerialization(unittest.TestCase):
    def test_to_dict_has_exact_key_set(self):
        expected = {
            "id", "title", "content", "created_at", "due_at",
            "urgency", "status", "progress", "logs", "workspace",
        }
        self.assertEqual(set(Memo().to_dict().keys()), expected)

    def test_from_dict_roundtrip_preserves_all_fields(self):
        m = Memo(
            id="abc", title="title", content="body",
            created_at="2024-01-01 10:00:00",
            due_at="2024-02-01 10:00:00",
            urgency=URGENCY_HIGH, status=STATUS_DONE, progress=77,
        )
        m.add_progress("step one", progress=77, time="2024-01-02 09:00:00")
        restored = Memo.from_dict(m.to_dict())
        self.assertEqual(restored.to_dict(), m.to_dict())

    def test_from_dict_none_and_empty_are_safe(self):
        for value in (None, {}, {"title": None}):
            with self.subTest(value=value):
                m = Memo.from_dict(value)
                self.assertEqual(m.title, "")
                self.assertEqual(m.urgency, URGENCY_MEDIUM)
                self.assertEqual(m.status, STATUS_PENDING)
                self.assertEqual(m.progress, 0)
                self.assertTrue(m.created_at)

    def test_from_dict_normalizes_invalid_enums(self):
        m = Memo.from_dict({"urgency": "unknown", "status": "unknown", "progress": 999})
        self.assertEqual(m.urgency, URGENCY_MEDIUM)
        self.assertEqual(m.status, STATUS_PENDING)
        self.assertEqual(m.progress, 100)

    def test_from_dict_rebuilds_progress_logs(self):
        m = Memo.from_dict({"logs": [{"time": "2024-01-01 00:00:00", "text": "a"}, None, {}]})
        self.assertEqual(len(m.logs), 3)
        self.assertIsInstance(m.logs[0], ProgressLog)
        self.assertEqual(m.logs[0].text, "a")
        self.assertEqual(m.logs[0].time, "2024-01-01 00:00:00")

    def test_logs_accept_progress_log_instances(self):
        log = ProgressLog(text="raw", time="2024-01-01 00:00:00")
        self.assertIs(Memo(logs=[log]).logs[0], log)


class TestMemoHelpers(unittest.TestCase):
    def test_add_progress_appends_log_with_system_timestamp(self):
        m = Memo()
        before = datetime.now() - timedelta(seconds=2)
        log = m.add_progress("step", progress=60)
        after = datetime.now() + timedelta(seconds=2)
        self.assertEqual(len(m.logs), 1)
        self.assertEqual(m.progress, 60)
        self.assertEqual(log.text, "step")
        stamp = datetime.strptime(log.time, TIME_FORMAT)
        self.assertGreaterEqual(stamp, before)
        self.assertLessEqual(stamp, after)

    def test_add_progress_without_value_keeps_progress(self):
        m = Memo(progress=40)
        m.add_progress("note")
        self.assertEqual(m.progress, 40)
        self.assertEqual(m.logs[0].text, "note")

    def test_add_progress_clamps_invalid_value(self):
        m = Memo()
        m.add_progress("note", progress=500)
        self.assertEqual(m.progress, 100)
        m.add_progress("note2", progress=-5)
        self.assertEqual(m.progress, 0)

    def test_is_done_and_is_pending(self):
        self.assertTrue(Memo(status=STATUS_DONE).is_done())
        self.assertFalse(Memo(status=STATUS_DONE).is_pending())
        self.assertTrue(Memo().is_pending())

    def test_matches_is_case_insensitive_and_covers_title_and_content(self):
        m = Memo(title="Buy MILK", content="from Shop")
        self.assertTrue(m.matches("milk"))
        self.assertTrue(m.matches("SHOP"))
        self.assertTrue(m.matches("buy milk"))
        self.assertFalse(m.matches("bread"))
        self.assertTrue(m.matches(""))
        self.assertTrue(m.matches("   "))

    def test_is_overdue_and_is_due_soon(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        self.assertTrue(Memo(due_at="2024-01-01 11:00:00").is_overdue(now))
        self.assertFalse(Memo(due_at="2024-01-01 11:00:00").is_due_soon(48, now))
        self.assertTrue(Memo(due_at="2024-01-02 12:00:00").is_due_soon(48, now))
        self.assertFalse(Memo(due_at="2024-01-03 12:00:01").is_due_soon(48, now))
        self.assertFalse(Memo(due_at="").is_overdue(now))
        self.assertFalse(Memo(due_at="").is_due_soon(48, now))
        self.assertFalse(Memo(due_at="bad").is_overdue(now))
        self.assertFalse(Memo(due_at="2024-01-01 11:00:00",
                              status=STATUS_DONE).is_overdue(now))

    def test_due_datetime_parses_and_handles_missing(self):
        self.assertEqual(Memo(due_at="2024-03-04 05:06:07").due_datetime(),
                         datetime(2024, 3, 4, 5, 6, 7))
        self.assertIsNone(Memo().due_datetime())
        self.assertIsNone(Memo(due_at="nonsense").due_datetime())


class TestProgressLog(unittest.TestCase):
    def test_default_time_is_current_system_time(self):
        before = datetime.now() - timedelta(seconds=2)
        log = ProgressLog(text="hello")
        after = datetime.now() + timedelta(seconds=2)
        stamp = datetime.strptime(log.time, TIME_FORMAT)
        self.assertGreaterEqual(stamp, before)
        self.assertLessEqual(stamp, after)
        self.assertEqual(log.text, "hello")

    def test_to_dict_from_dict_roundtrip(self):
        log = ProgressLog(text="a", time="2024-01-01 01:02:03")
        restored = ProgressLog.from_dict(log.to_dict())
        self.assertEqual(restored.to_dict(), log.to_dict())

    def test_from_dict_none_fills_time(self):
        log = ProgressLog.from_dict(None)
        self.assertEqual(log.text, "")
        self.assertTrue(log.time)

    def test_explicit_time_is_preserved(self):
        self.assertEqual(ProgressLog(time="2020-01-01 00:00:00").time,
                         "2020-01-01 00:00:00")


class TestTimeTools(unittest.TestCase):
    def test_parse_time_supported_formats(self):
        cases = {
            "2024-01-02 03:04:05": datetime(2024, 1, 2, 3, 4, 5),
            "2024-01-02 03:04": datetime(2024, 1, 2, 3, 4, 0),
            "2024-01-02": datetime(2024, 1, 2, 0, 0, 0),
            "2024/01/02 03:04:05": datetime(2024, 1, 2, 3, 4, 5),
            "2024/01/02 03:04": datetime(2024, 1, 2, 3, 4, 0),
            "2024/01/02": datetime(2024, 1, 2, 0, 0, 0),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_time(text), expected)

    def test_parse_time_strips_whitespace(self):
        self.assertEqual(parse_time("  2024-01-02  "), datetime(2024, 1, 2))

    def test_parse_time_single_digit_components(self):
        self.assertEqual(parse_time("2024-1-2 9:5:3"), datetime(2024, 1, 2, 9, 5, 3))
        self.assertEqual(parse_time("2024/6/1"), datetime(2024, 6, 1))

    def test_parse_time_empty_and_blank_return_none(self):
        for value in ("", "   ", None):
            with self.subTest(value=value):
                self.assertIsNone(parse_time(value))

    def test_parse_time_invalid_inputs_return_none(self):
        for value in ("not-a-date", "2024-13-01", "2024-02-30",
                      "2024-01-02 25:00:00", "yesterday", "20240102", "2024-01-02T03:04:05"):
            with self.subTest(value=value):
                self.assertIsNone(parse_time(value))

    def test_is_valid_time_text_empty_and_blank_are_valid(self):
        for value in ("", "   ", None):
            with self.subTest(value=value):
                self.assertTrue(is_valid_time_text(value))

    def test_is_valid_time_text_valid_inputs(self):
        for value in ("2024-01-02 03:04:05", "2024-01-02", "2024/01/02 03:04",
                      "  2024-12-31  "):
            with self.subTest(value=value):
                self.assertTrue(is_valid_time_text(value))

    def test_is_valid_time_text_invalid_inputs(self):
        for value in ("not-a-date", "2024-13-01", "2024-02-30", "tomorrow"):
            with self.subTest(value=value):
                self.assertFalse(is_valid_time_text(value))


class TestNormalizeProgress(unittest.TestCase):
    def test_valid_range_passthrough(self):
        for value in (0, 1, 50, 99, 100):
            with self.subTest(value=value):
                self.assertEqual(normalize_progress(value), value)

    def test_negative_clamped_to_zero(self):
        for value in (-1, -5, -100, -10000):
            with self.subTest(value=value):
                self.assertEqual(normalize_progress(value), 0)

    def test_above_100_clamped(self):
        for value in (101, 150, 500, 10000):
            with self.subTest(value=value):
                self.assertEqual(normalize_progress(value), 100)

    def test_non_numeric_returns_zero(self):
        for value in ("abc", "12.5", "", None, [], {}, object()):
            with self.subTest(value=repr(value)):
                self.assertEqual(normalize_progress(value), 0)

    def test_numeric_string_and_float(self):
        self.assertEqual(normalize_progress("50"), 50)
        self.assertEqual(normalize_progress("120"), 100)
        self.assertEqual(normalize_progress(3.9), 3)
        self.assertEqual(normalize_progress(True), 1)


class TestModelConstants(unittest.TestCase):
    def test_urgency_and_status_levels(self):
        self.assertEqual(URGENCY_LEVELS, ("高", "中", "低"))
        self.assertEqual(STATUS_LEVELS, ("未完成", "已完成"))
        self.assertEqual(URGENCY_HIGH, "高")
        self.assertEqual(URGENCY_MEDIUM, "中")
        self.assertEqual(URGENCY_LOW, "低")
        self.assertEqual(STATUS_PENDING, "未完成")
        self.assertEqual(STATUS_DONE, "已完成")

    def test_sort_modes(self):
        self.assertEqual(SORT_CREATED, "created_at")
        self.assertEqual(SORT_DUE, "due_at")
        self.assertEqual(SORT_URGENCY, "urgency")
        self.assertEqual(len(SORT_MODES), 3)
        self.assertEqual(set(SORT_MODE_LABELS.keys()),
                         {SORT_CREATED, SORT_DUE, SORT_URGENCY})

    def test_urgency_order_covers_all_levels(self):
        self.assertEqual(set(URGENCY_ORDER.keys()), set(URGENCY_LEVELS))
        self.assertEqual(sorted(URGENCY_ORDER.values()), [0, 1, 2])

    def test_time_format_matches_now_str(self):
        value = now_str()
        self.assertEqual(datetime.strptime(value, TIME_FORMAT).strftime(TIME_FORMAT),
                         value)


if __name__ == "__main__":
    unittest.main()
