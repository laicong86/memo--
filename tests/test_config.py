# -*- coding: utf-8 -*-
"""Independent unit tests for config.py.

Covers all six color-classification branches with boundary values, color
resolution helpers, AppConfig read/write round-trip, reset, invalid values
and the due-soon-hour guard. No project source file is modified.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import (
    AppConfig,
    COLOR_KEYS,
    COLOR_KEY_LABELS,
    COLOR_PRESETS,
    CONFIG_VERSION,
    DEFAULT_COLOR_MAP,
    DEFAULT_DUE_SOON_HOURS,
    DEFAULT_HOTKEY,
    DEFAULT_SORT_MODE,
    _safe_hours,
    classify_color_key,
    color_for_memo,
    color_key_for_memo,
    is_valid_hex,
    resolve_color,
)
from models import Memo, SORT_DUE, STATUS_DONE, URGENCY_HIGH, URGENCY_LOW, URGENCY_MEDIUM

NOW = datetime(2024, 1, 1, 12, 0, 0)


class TestClassifyColorKey(unittest.TestCase):
    """All six branches plus boundary values (exact due moment / exact threshold)."""

    def test_done_branch_has_highest_priority(self):
        self.assertEqual(
            classify_color_key(STATUS_DONE, "2020-01-01 00:00:00", URGENCY_HIGH, now=NOW),
            "done")
        self.assertEqual(classify_color_key(STATUS_DONE, "", URGENCY_LOW, now=NOW), "done")
        self.assertEqual(
            classify_color_key(STATUS_DONE, "2024-01-03 12:00:00", URGENCY_MEDIUM, now=NOW),
            "done")

    def test_overdue_high_branch(self):
        self.assertEqual(
            classify_color_key("未完成", "2024-01-01 11:59:59", URGENCY_HIGH, now=NOW),
            "overdue_high")
        self.assertEqual(
            classify_color_key("未完成", "2020-01-01 00:00:00", URGENCY_HIGH, now=NOW),
            "overdue_high")

    def test_overdue_branch(self):
        for urgency in (URGENCY_MEDIUM, URGENCY_LOW, "unknown"):
            with self.subTest(urgency=urgency):
                self.assertEqual(
                    classify_color_key("未完成", "2024-01-01 11:59:59", urgency, now=NOW),
                    "overdue")

    def test_exact_due_moment_is_overdue(self):
        # Boundary: due == now, implementation uses "due <= current".
        self.assertEqual(
            classify_color_key("未完成", "2024-01-01 12:00:00", URGENCY_HIGH, now=NOW),
            "overdue_high")
        self.assertEqual(
            classify_color_key("未完成", "2024-01-01 12:00:00", URGENCY_LOW, now=NOW),
            "overdue")

    def test_due_one_second_in_future_is_not_overdue(self):
        # Boundary: due is strictly greater than now -> must NOT be overdue.
        self.assertEqual(
            classify_color_key("未完成", "2024-01-01 12:00:01", URGENCY_LOW, now=NOW),
            "due_soon")
        self.assertEqual(
            classify_color_key("未完成", "2024-01-01 12:00:01", URGENCY_LOW,
                               now=NOW + timedelta(seconds=2)), "overdue")

    def test_urgent_high_branch_when_not_overdue(self):
        self.assertEqual(
            classify_color_key("未完成", "2025-01-01 00:00:00", URGENCY_HIGH, now=NOW),
            "urgent_high")

    def test_urgent_high_when_due_at_is_empty(self):
        self.assertEqual(
            classify_color_key("未完成", "", URGENCY_HIGH, now=NOW), "urgent_high")
        self.assertEqual(
            classify_color_key("未完成", "not-a-date", URGENCY_HIGH, now=NOW),
            "urgent_high")

    def test_urgent_high_has_priority_over_due_soon(self):
        self.assertEqual(
            classify_color_key("未完成", "2024-01-02 00:00:00", URGENCY_HIGH, now=NOW),
            "urgent_high")

    def test_due_soon_branch_within_threshold(self):
        self.assertEqual(
            classify_color_key("未完成", "2024-01-02 11:59:59", URGENCY_MEDIUM, now=NOW),
            "due_soon")

    def test_due_soon_exact_threshold_is_included(self):
        # Boundary: exactly due_soon_hours (48h) away -> due_soon (<=).
        self.assertEqual(
            classify_color_key("未完成", "2024-01-03 12:00:00", URGENCY_MEDIUM, now=NOW),
            "due_soon")

    def test_one_second_beyond_threshold_is_normal(self):
        self.assertEqual(
            classify_color_key("未完成", "2024-01-03 12:00:01", URGENCY_MEDIUM, now=NOW),
            "normal")

    def test_normal_branch_far_future(self):
        self.assertEqual(
            classify_color_key("未完成", "2025-01-01 00:00:00", URGENCY_MEDIUM, now=NOW),
            "normal")
        self.assertEqual(
            classify_color_key("未完成", "2025-01-01 00:00:00", URGENCY_LOW, now=NOW),
            "normal")

    def test_normal_branch_when_due_at_empty_or_invalid(self):
        self.assertEqual(classify_color_key("未完成", "", URGENCY_MEDIUM, now=NOW), "normal")
        self.assertEqual(classify_color_key("未完成", "   ", URGENCY_LOW, now=NOW), "normal")
        self.assertEqual(classify_color_key("未完成", "garbage", URGENCY_LOW, now=NOW),
                         "normal")

    def test_all_six_keys_are_reachable(self):
        reached = {
            classify_color_key(STATUS_DONE, "2020-01-01 00:00:00", URGENCY_HIGH, now=NOW),
            classify_color_key("未完成", "2023-01-01 00:00:00", URGENCY_HIGH, now=NOW),
            classify_color_key("未完成", "2023-01-01 00:00:00", URGENCY_MEDIUM, now=NOW),
            classify_color_key("未完成", "2025-01-01 00:00:00", URGENCY_HIGH, now=NOW),
            classify_color_key("未完成", "2024-01-02 00:00:00", URGENCY_MEDIUM, now=NOW),
            classify_color_key("未完成", "", URGENCY_LOW, now=NOW),
        }
        self.assertEqual(reached, set(COLOR_KEYS))
        self.assertEqual(len(reached), 6)

    def test_custom_due_soon_hours(self):
        self.assertEqual(
            classify_color_key("未完成", "2024-01-01 13:00:00", URGENCY_LOW,
                               now=NOW, due_soon_hours=2), "due_soon")
        self.assertEqual(
            classify_color_key("未完成", "2024-01-01 14:00:00", URGENCY_LOW,
                               now=NOW, due_soon_hours=2), "due_soon")
        self.assertEqual(
            classify_color_key("未完成", "2024-01-01 15:00:00", URGENCY_LOW,
                               now=NOW, due_soon_hours=2), "normal")

    def test_invalid_due_soon_hours_falls_back_to_default(self):
        self.assertEqual(
            classify_color_key("未完成", "2024-01-02 12:00:00", URGENCY_LOW,
                               now=NOW, due_soon_hours="abc"), "due_soon")
        self.assertEqual(
            classify_color_key("未完成", "2024-01-02 12:00:00", URGENCY_LOW,
                               now=NOW, due_soon_hours=0), "due_soon")

    def test_slash_formatted_due_is_parsed_for_coloring(self):
        self.assertEqual(
            classify_color_key("未完成", "2024/1/2 12:00:00", URGENCY_MEDIUM, now=NOW),
            "due_soon")
        self.assertEqual(
            classify_color_key("未完成", "2023/12/31", URGENCY_HIGH, now=NOW),
            "overdue_high")

    def test_default_now_parameter_uses_local_system_clock(self):
        # A long-past due date must be overdue without passing an explicit now.
        self.assertEqual(classify_color_key("未完成", "2000-01-01 00:00:00",
                                           URGENCY_LOW), "overdue")
        self.assertEqual(classify_color_key("未完成", "2999-01-01 00:00:00",
                                           URGENCY_LOW), "normal")


class TestColorResolution(unittest.TestCase):
    def test_resolve_color_defaults(self):
        self.assertEqual(resolve_color(STATUS_DONE, "", URGENCY_LOW, now=NOW),
                         DEFAULT_COLOR_MAP["done"])
        self.assertEqual(resolve_color("未完成", "2023-01-01 00:00:00",
                                      URGENCY_HIGH, now=NOW),
                         DEFAULT_COLOR_MAP["overdue_high"])

    def test_resolve_color_custom_map(self):
        cfg = AppConfig()
        cfg.set_color("done", "#ABCDEF")
        self.assertEqual(resolve_color(STATUS_DONE, "", URGENCY_LOW,
                                      config=cfg, now=NOW), "#ABCDEF")

    def test_color_key_for_memo(self):
        memo = Memo(due_at="2024-01-02 12:00:00", urgency=URGENCY_MEDIUM)
        self.assertEqual(color_key_for_memo(memo, AppConfig(), NOW), "due_soon")
        done = Memo(status=STATUS_DONE)
        self.assertEqual(color_key_for_memo(done, AppConfig(), NOW), "done")

    def test_color_for_memo_uses_configurable_map(self):
        memo = Memo(due_at="2024-01-02 12:00:00", urgency=URGENCY_MEDIUM)
        cfg = AppConfig()
        cfg.set_color("due_soon", "#010203")
        self.assertEqual(color_for_memo(memo, cfg, NOW), "#010203")
        self.assertEqual(color_for_memo(memo, None, NOW),
                         DEFAULT_COLOR_MAP["due_soon"])

    def test_get_color_unknown_key_fallback(self):
        cfg = AppConfig()
        self.assertEqual(cfg.get_color("done"), DEFAULT_COLOR_MAP["done"])
        self.assertEqual(cfg.get_color("no_such_key"), DEFAULT_COLOR_MAP["normal"])


class TestAppConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = AppConfig()
        self.assertEqual(cfg.color_map, DEFAULT_COLOR_MAP)
        self.assertEqual(cfg.due_soon_hours, DEFAULT_DUE_SOON_HOURS)
        self.assertEqual(cfg.sort_mode, DEFAULT_SORT_MODE)

    def test_set_color_valid_and_invalid(self):
        cfg = AppConfig()
        self.assertTrue(cfg.set_color("done", "#aBcDeF"))
        self.assertEqual(cfg.get_color("done"), "#aBcDeF")
        self.assertFalse(cfg.set_color("done", "red"))
        self.assertEqual(cfg.get_color("done"), "#aBcDeF")
        self.assertFalse(cfg.set_color("unknown_key", "#FFFFFF"))

    def test_reset_colors_restores_defaults_and_is_independent_copy(self):
        cfg = AppConfig()
        for key in COLOR_KEYS:
            self.assertTrue(cfg.set_color(key, "#123456"))
        self.assertEqual(cfg.get_color("done"), "#123456")
        cfg.reset_colors()
        self.assertEqual(cfg.color_map, DEFAULT_COLOR_MAP)
        cfg.color_map["done"] = "#000000"
        self.assertEqual(DEFAULT_COLOR_MAP["done"], "#9E9E9E")

    def test_as_dict_structure(self):
        data = AppConfig().as_dict()
        self.assertEqual(set(data.keys()),
                         {"version", "color_map", "due_soon_hours", "sort_mode",
                          "hotkey"})
        self.assertEqual(data["version"], CONFIG_VERSION)
        self.assertEqual(data["color_map"], DEFAULT_COLOR_MAP)
        self.assertEqual(data["hotkey"], DEFAULT_HOTKEY)

    def test_apply_dict_roundtrip(self):
        source = AppConfig()
        source.set_color("overdue", "#112233")
        source.due_soon_hours = 12
        source.sort_mode = SORT_DUE
        target = AppConfig()
        target.apply_dict(source.as_dict())
        self.assertEqual(target.as_dict(), source.as_dict())

    def test_apply_dict_ignores_invalid_values(self):
        cfg = AppConfig()
        cfg.apply_dict({
            "color_map": {"done": "red", "normal": "#ABCDEF", "unknown": "#000000"},
            "due_soon_hours": 99999,
            "sort_mode": "bogus",
        })
        self.assertEqual(cfg.get_color("done"), DEFAULT_COLOR_MAP["done"])
        self.assertEqual(cfg.get_color("normal"), "#ABCDEF")
        self.assertEqual(cfg.due_soon_hours, DEFAULT_DUE_SOON_HOURS)
        self.assertEqual(cfg.sort_mode, DEFAULT_SORT_MODE)

    def test_apply_dict_non_dict_is_noop(self):
        cfg = AppConfig()
        for value in (None, "text", 42, [1, 2]):
            with self.subTest(value=repr(value)):
                cfg.apply_dict(value)
                self.assertEqual(cfg.color_map, DEFAULT_COLOR_MAP)

    def test_apply_colors_ignores_non_dict(self):
        cfg = AppConfig()
        cfg.apply_colors("not a dict")
        self.assertEqual(cfg.color_map, DEFAULT_COLOR_MAP)

    def test_save_load_roundtrip_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "nested", "config.json")
            cfg = AppConfig(path=path)
            cfg.set_color("done", "#112233")
            cfg.due_soon_hours = 12
            cfg.sort_mode = SORT_DUE
            self.assertTrue(cfg.save())
            self.assertTrue(os.path.isfile(path))
            loaded = AppConfig.load(path)
            self.assertEqual(loaded.as_dict(), cfg.as_dict())
            self.assertEqual(loaded.get_color("done"), "#112233")

    def test_load_missing_file_returns_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = AppConfig.load(os.path.join(folder, "missing.json"))
            self.assertEqual(cfg.color_map, DEFAULT_COLOR_MAP)

    def test_load_corrupt_json_returns_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{this is not json")
            cfg = AppConfig.load(path)
            self.assertEqual(cfg.color_map, DEFAULT_COLOR_MAP)
            self.assertEqual(cfg.due_soon_hours, DEFAULT_DUE_SOON_HOURS)

    def test_load_partial_config(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"color_map": {"done": "#111111"}}, handle)
            cfg = AppConfig.load(path)
            self.assertEqual(cfg.get_color("done"), "#111111")
            self.assertEqual(cfg.get_color("normal"), DEFAULT_COLOR_MAP["normal"])
            self.assertEqual(cfg.due_soon_hours, DEFAULT_DUE_SOON_HOURS)

    def test_save_without_path_returns_false(self):
        self.assertFalse(AppConfig().save())

    def test_due_soon_hours_bounds(self):
        cases = [(0, 48), (-5, 48), (1, 1), (48, 48), (8760, 8760),
                 (8761, 48), ("24", 24), ("abc", 48), (None, 48)]
        for value, expected in cases:
            with self.subTest(value=repr(value)):
                self.assertEqual(AppConfig(due_soon_hours=value).due_soon_hours, expected)

    def test_invalid_sort_mode_falls_back(self):
        self.assertEqual(AppConfig(sort_mode="bogus").sort_mode, DEFAULT_SORT_MODE)
        cfg = AppConfig()
        cfg.apply_dict({"sort_mode": 123})
        self.assertEqual(cfg.sort_mode, DEFAULT_SORT_MODE)

    def test_constructor_color_map_applies_only_valid_entries(self):
        cfg = AppConfig(color_map={"done": "#010101", "normal": "bad", "fake": "#020202"})
        self.assertEqual(cfg.get_color("done"), "#010101")
        self.assertEqual(cfg.get_color("normal"), DEFAULT_COLOR_MAP["normal"])


class TestHexValidation(unittest.TestCase):
    def test_valid_hex_values(self):
        for value in ("#FFFFFF", "#000000", "#abcdef", "#123456", "#AaBbCc"):
            with self.subTest(value=value):
                self.assertTrue(is_valid_hex(value))

    def test_invalid_hex_values(self):
        for value in ("", None, "red", "#fff", "#1234567", "#GGGGGG",
                      "#12345", "123456", 123456, "#12345G"):
            with self.subTest(value=repr(value)):
                self.assertFalse(is_valid_hex(value))

    def test_safe_hours_boundaries(self):
        self.assertEqual(_safe_hours(1), 1)
        self.assertEqual(_safe_hours(24 * 365), 24 * 365)
        self.assertEqual(_safe_hours(0, 99), 99)
        self.assertEqual(_safe_hours(10 ** 9, 7), 7)

    def test_color_key_metadata_consistency(self):
        self.assertEqual(set(COLOR_KEY_LABELS.keys()), set(COLOR_KEYS))
        self.assertEqual(set(DEFAULT_COLOR_MAP.keys()), set(COLOR_KEYS))
        self.assertEqual(len(COLOR_KEYS), 6)
        for value, _label in COLOR_PRESETS:
            self.assertTrue(is_valid_hex(value))
        for value in DEFAULT_COLOR_MAP.values():
            self.assertTrue(is_valid_hex(value))


if __name__ == "__main__":
    unittest.main()
