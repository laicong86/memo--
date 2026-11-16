# -*- coding: utf-8 -*-
"""日历选择器的纯逻辑单元测试（标准库 unittest，不创建任何窗口）。

覆盖月历网格、时间规整、due 文本组合与解析、以及本机系统时间回落。
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from calendar_picker import (WEEK_LABELS, _COLS, _ROWS, build_due_text,
                             month_cells, normalize_time_text, parse_due_text)
from models import is_valid_time_text, parse_time


class TestMonthCells(unittest.TestCase):
    def test_grid_is_always_six_by_seven(self):
        for year, month in ((2026, 1), (2026, 2), (2024, 2), (2026, 11),
                            (2026, 12), (2000, 2), (2100, 2)):
            with self.subTest(year=year, month=month):
                cells = month_cells(year, month)
                self.assertEqual(len(cells), _ROWS)
                self.assertTrue(all(len(row) == _COLS for row in cells))

    def test_monday_is_the_first_column(self):
        # 2026-11-01 是星期日，按周一起始应落在第一行最后一列
        cells = month_cells(2026, 11)
        self.assertIsNone(cells[0][0])
        self.assertEqual(cells[0][6], date(2026, 11, 1))
        self.assertEqual(cells[1][0], date(2026, 11, 2))

    def test_leap_february_has_29_days_and_2100_has_28(self):
        for year, expected in ((2024, 29), (2000, 29), (2100, 28), (2026, 28)):
            with self.subTest(year=year):
                cells = month_cells(year, 2)
                days = [cell for row in cells for cell in row if cell is not None]
                self.assertEqual(len(days), expected)
                self.assertEqual(days[0], date(year, 2, 1))
                self.assertEqual(days[-1], date(year, 2, expected))
                self.assertEqual(len([r for row in cells for r in row
                                      if r is None]), 42 - expected)

    def test_every_cell_belongs_to_requested_month_and_is_ascending(self):
        cells = month_cells(2026, 12)
        days = [cell for row in cells for cell in row if cell is not None]
        self.assertTrue(all(day.year == 2026 and day.month == 12 for day in days))
        self.assertEqual(days, sorted(days))
        self.assertEqual(days[-1] - days[0], timedelta(days=30))

    def test_year_boundary_month_is_valid(self):
        january = [c for row in month_cells(2027, 1) for c in row if c]
        december = [c for row in month_cells(2026, 12) for c in row if c]
        self.assertEqual(january[0], date(2027, 1, 1))
        self.assertEqual(december[-1], date(2026, 12, 31))

    def test_week_labels_start_on_monday(self):
        self.assertEqual(WEEK_LABELS[0], "一")
        self.assertEqual(len(WEEK_LABELS), 7)


class TestNormalizeTimeText(unittest.TestCase):
    def test_valid_values_are_normalized(self):
        cases = {
            "09:05": "09:05:00",
            "9:5": "09:05:00",
            "14:30:59": "14:30:59",
            " 23:59 ": "23:59:00",
            "00:00:00": "00:00:00",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_time_text(raw), expected)

    def test_invalid_values_return_empty_string(self):
        for raw in ("", "   ", "24:00", "12:60", "-1:00", "abc", "12",
                    "2026-11-20", None):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_time_text(raw), "")


class TestBuildDueText(unittest.TestCase):
    def test_date_only_is_valid_and_parses_back(self):
        text = build_due_text(date(2026, 11, 20))
        self.assertEqual(text, "2026-11-20")
        self.assertTrue(is_valid_time_text(text))
        self.assertEqual(parse_time(text).date(), date(2026, 11, 20))

    def test_date_with_time_is_valid_and_parses_back(self):
        text = build_due_text(date(2026, 11, 20), "14:30")
        self.assertEqual(text, "2026-11-20 14:30:00")
        self.assertTrue(is_valid_time_text(text))
        self.assertEqual(parse_time(text), datetime(2026, 11, 20, 14, 30, 0))

    def test_invalid_time_falls_back_to_date_only(self):
        for raw in ("非法", "25:99", "abc", None):
            with self.subTest(raw=raw):
                text = build_due_text(date(2026, 1, 5), raw)
                self.assertEqual(text, "2026-01-05")
                self.assertTrue(is_valid_time_text(text))

    def test_single_digit_month_day_is_zero_padded(self):
        self.assertEqual(build_due_text(date(2026, 1, 5)), "2026-01-05")


class TestParseDueText(unittest.TestCase):
    def test_full_timestamp_keeps_clock(self):
        self.assertEqual(parse_due_text("2026-11-20 14:30:00"),
                         (date(2026, 11, 20), "14:30:00"))

    def test_date_only_has_no_clock(self):
        self.assertEqual(parse_due_text("2026-11-20"),
                         (date(2026, 11, 20), ""))

    def test_slash_format_is_supported(self):
        day, clock = parse_due_text("2026/6/1 09:00")
        self.assertEqual(day, date(2026, 6, 1))
        self.assertEqual(clock, "09:00:00")

    def test_invalid_or_empty_falls_back_to_local_today(self):
        today = datetime.now().date()
        for raw in ("", "   ", "not-a-date", "2026-13-45"):
            with self.subTest(raw=raw):
                self.assertEqual(parse_due_text(raw), (today, ""))

    def test_round_trip_with_build_due_text(self):
        for day, clock in ((date(2026, 11, 20), "08:00:00"),
                           (date(2024, 2, 29), ""),
                           (date(2027, 1, 1), "23:59:59")):
            with self.subTest(day=day, clock=clock):
                self.assertEqual(parse_due_text(build_due_text(day, clock)),
                                 (day, clock))


if __name__ == "__main__":
    unittest.main()
