# -*- coding: utf-8 -*-
"""日历形式的「预期完成时间」选择器。

只用 Python 标准库（tkinter / datetime），兼容 Python 3.9，零第三方依赖，
不 import 任何 Win32 / 托盘模块，因此纯逻辑函数可在无 GUI 环境下单测。

对外提供：
    month_cells(year, month)    生成 6x7 日期网格（周一为一周起点），便于单测
    normalize_time_text(text)  把 HH:MM / HH:MM:SS 规整成 HH:MM:SS，非法返回 ""
    build_due_text(day, text)  组合出 models.is_valid_time_text 认可的文本
    parse_due_text(text)       从已有 due 文本拆出 (日期, 时间文本)
    CalendarPopup               点击弹出的月历 Toplevel
"""
from __future__ import annotations

import tkinter as tk
from datetime import date, datetime, timedelta
from typing import Any, Callable, List, Optional, Tuple

import theme
from models import parse_time

WEEK_LABELS = ("一", "二", "三", "四", "五", "六", "日")
_ROWS = 6
_COLS = 7


# ---------------------------------------------------------------------------
# 纯逻辑：不触碰 Tk，便于单元测试
# ---------------------------------------------------------------------------
def month_cells(year: int, month: int) -> List[List[Optional[date]]]:
    """返回 6 行 x 7 列的日期网格，周一为一周起点。

    属于本月的格子是 date 对象，其余（上/下月补位）为 None。
    固定 6 行保证弹窗高度稳定，不会因月份不同跳动。
    """
    first = date(year, month, 1)
    start = first - timedelta(days=first.weekday())
    cells = []  # type: List[List[Optional[date]]]
    for row in range(_ROWS):
        line = []  # type: List[Optional[date]]
        for col in range(_COLS):
            day = start + timedelta(days=row * _COLS + col)
            line.append(day if (day.year == year and day.month == month) else None)
        cells.append(line)
    return cells


def normalize_time_text(text: str) -> str:
    """把 HH:MM / H:M / HH:MM:SS 规整为 HH:MM:SS；非法或留空返回空串。"""
    value = (text or "").strip()
    if not value:
        return ""
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value, fmt).strftime("%H:%M:%S")
        except ValueError:
            continue
    return ""


def build_due_text(day: date, time_text: str = "") -> str:
    """把选中的日期与可选时间组合成合法 due 文本。

    时间留空或非法时只写 ``YYYY-MM-DD``（同样被 models 接受，视为当天 00:00:00）。
    """
    prefix = day.strftime("%Y-%m-%d")
    clock = normalize_time_text(time_text)
    return prefix + (" " + clock if clock else "")


def parse_due_text(text: str) -> Tuple[date, str]:
    """从已有 due 文本拆出 (日期, HH:MM:SS)。

    解析失败时回落到「本机系统时间的今天 + 空时间」，保证月历总能打开。
    一律使用本机 datetime.now()，不使用 UTC。
    """
    parsed = parse_time(text or "")
    if parsed is None:
        return datetime.now().date(), ""
    clock = parsed.strftime("%H:%M:%S") if ":" in (text or "") else ""
    return parsed.date(), clock


def display_time_text(text: str) -> str:
    """把完整 due 文本中的时间取成 HH:MM（如 15:30），无时间则返回空串。"""
    _, clock = parse_due_text(text)
    return clock[:5]


def split_due_text(text: str) -> Tuple[str, str]:
    """拆成 (YYYY-MM-DD, HH:MM) 两个展示字段；时间不含秒。"""
    day, clock = parse_due_text(text)
    return day.strftime("%Y-%m-%d"), clock[:5]


def join_due_text(date_text: str, time_text: str) -> str:
    """把日期字段与 HH:MM 时间字段合成可被 models 解析的完整文本。"""
    prefix = (date_text or "").strip()
    if not prefix:
        return ""
    clock = normalize_time_text(time_text)
    return prefix + (" " + clock if clock else "")


def place_near_host(
    anchor: Tuple[int, int, int, int],
    popup_size: Tuple[int, int],
    host: Tuple[int, int, int, int],
    gap: int = 6,
    edge: int = 8,
) -> Tuple[int, int]:
    """计算弹窗位置：贴近触发控件，并始终落在主窗口所在显示器的一侧。

    ``anchor`` / ``host`` 均为 ``(x, y, width, height)`` 屏幕坐标。
    先放在锚点下方，右侧或下方越界时翻到锚点左侧 / 上方，再夹进主窗口范围内；
    这样多屏环境下窗口在副屏时，弹窗不会跑到主屏去找。
    """
    ax, ay, aw, ah = [int(v) for v in anchor]
    pw, ph = [int(v) for v in popup_size]
    hx, hy, hw, hh = [int(v) for v in host]
    x, y = ax, ay + ah + gap
    if x + pw > hx + hw - edge:
        x = ax + aw - pw
    if y + ph > hy + hh - edge:
        y = ay - ph - gap
    x = max(hx + edge, min(x, hx + hw - pw - edge))
    y = max(hy + edge, min(y, hy + hh - ph - edge))
    return max(0, x), max(0, y)


# ---------------------------------------------------------------------------
# 月历弹窗
# ---------------------------------------------------------------------------
class CalendarPopup(tk.Toplevel):
    """点击「日历」按钮后弹出的月历选择窗口。

    选中某一天后调用 ``on_pick(due_text)``，点「清除」调用 ``on_clear()``，
    随后自动关闭；Esc / 取消 / 关闭按钮也可直接退出。
    """

    def __init__(
        self,
        master: Any,
        initial_text: str = "",
        on_pick: Optional[Callable[[str], None]] = None,
        on_clear: Optional[Callable[[], None]] = None,
        anchor: Any = None,
        host: Any = None,
        initial_time: str = "",
    ):
        super().__init__(master, bg=theme.CARD_BG)
        self.title("选择预期完成时间")
        self.resizable(False, False)
        self._on_pick = on_pick
        self._on_clear = on_clear
        self._anchor = anchor
        self._host = host
        self._today = datetime.now().date()
        self._year, self._month, self.var_time = self._initial_state(
            initial_text, initial_time)
        self.var_month = tk.StringVar(value="")
        self._day_buttons = []  # type: List[tk.Button]
        self._build()
        self._render()
        self._prepare_window(master)

    def _initial_state(
        self, initial_text: str, initial_time: str = ""
    ) -> Tuple[int, int, tk.StringVar]:
        """根据当前 due 文本 / 时间框内容决定弹窗初始年月与时间。

        用户在详情的时间框先填好 ``15:30`` 但尚未选日期时，完整 due 仍为空；
        此时以 ``initial_time`` 为准，避免选完日期后时间被清空。
        """
        day, clock = parse_due_text(initial_text)
        typed = normalize_time_text(initial_time)
        if typed:
            clock = typed
        return day.year, day.month, tk.StringVar(value=clock)

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    def _build(self) -> None:
        head = tk.Frame(self, bg=theme.CARD_BG)
        head.pack(fill="x", padx=12, pady=(12, 6))
        tk.Button(head, text="‹", width=3, font=theme.FONT_BODY_BOLD,
                  bg=theme.HEADER_BG, fg=theme.TEXT_DARK, relief="flat",
                  cursor="hand2", command=lambda: self._shift(-1)).pack(side="left")
        tk.Label(head, textvariable=self.var_month, font=theme.FONT_TITLE,
                 bg=theme.CARD_BG, fg=theme.TEXT_DARK, width=14).pack(side="left",
                                                                      expand=True)
        tk.Button(head, text="›", width=3, font=theme.FONT_BODY_BOLD,
                  bg=theme.HEADER_BG, fg=theme.TEXT_DARK, relief="flat",
                  cursor="hand2", command=lambda: self._shift(1)).pack(side="right")

        week = tk.Frame(self, bg=theme.CARD_BG)
        week.pack(fill="x", padx=12)
        for col, label in enumerate(WEEK_LABELS):
            week.columnconfigure(col, weight=1)
            tk.Label(week, text=label, font=theme.FONT_SMALL, width=3,
                     bg=theme.CARD_BG,
                     fg=theme.TEXT_MUTED if col < 5 else theme.ACCENT).grid(
                row=0, column=col, pady=(2, 4)
            )

        grid = tk.Frame(self, bg=theme.CARD_BG)
        grid.pack(fill="x", padx=12)
        for row in range(_ROWS):
            grid.rowconfigure(row, weight=1)
            for col in range(_COLS):
                grid.columnconfigure(col, weight=1)
                button = tk.Button(grid, text="", width=3, height=1,
                                   font=theme.FONT_SMALL, relief="flat", bd=0,
                                   highlightthickness=0, cursor="hand2",
                                   bg=theme.INPUT_BG, fg=theme.TEXT_DARK,
                                   activebackground=theme.ACCENT,
                                   activeforeground=theme.TEXT_LIGHT)
                button.grid(row=row, column=col, padx=1, pady=1, sticky="nsew")
                self._day_buttons.append(button)

        clock_row = tk.Frame(self, bg=theme.CARD_BG)
        clock_row.pack(fill="x", padx=12, pady=(10, 2))
        tk.Label(clock_row, text="时间", font=theme.FONT_LABEL,
                 bg=theme.CARD_BG, fg=theme.TEXT_MUTED).pack(side="left")
        tk.Entry(clock_row, textvariable=self.var_time, font=theme.FONT_SMALL,
                 bg=theme.INPUT_BG, fg=theme.TEXT_DARK, relief="flat",
                 highlightthickness=1, highlightbackground=theme.INPUT_BORDER,
                 highlightcolor=theme.ACCENT, width=12).pack(side="left", padx=8)
        tk.Label(clock_row, text="留空只记日期 · HH:MM", font=theme.FONT_TINY,
                 bg=theme.CARD_BG, fg=theme.TEXT_MUTED).pack(side="left")

        foot = tk.Frame(self, bg=theme.CARD_BG)
        foot.pack(fill="x", padx=12, pady=(8, 12))
        tk.Button(foot, text="今天", font=theme.FONT_SMALL, bg=theme.HEADER_BG,
                  fg=theme.TEXT_DARK, relief="flat", cursor="hand2",
                  command=self._pick_today).pack(side="left")
        tk.Button(foot, text="清除", font=theme.FONT_SMALL, bg=theme.HEADER_BG,
                  fg=theme.DANGER, relief="flat", cursor="hand2",
                  command=self._clear).pack(side="left", padx=8)
        tk.Button(foot, text="取消", font=theme.FONT_SMALL, bg=theme.HEADER_BG,
                  fg=theme.TEXT_DARK, relief="flat", cursor="hand2",
                  command=self._close).pack(side="right")
        self.bind("<Escape>", lambda event: self._close())
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _prepare_window(self, master: Any) -> None:
        """贴近「日历」按钮定位，并夹在主窗口范围内（多屏不会跑偏）。"""
        host_widget = self._host or master.winfo_toplevel()
        anchor_widget = self._anchor or master
        try:
            self.transient(host_widget.winfo_toplevel())
        except tk.TclError:
            pass
        try:
            self.update_idletasks()
            width = self.winfo_width() or 320
            height = self.winfo_height() or 300

            def geom(widget: Any) -> Tuple[int, int, int, int]:
                return (widget.winfo_rootx(), widget.winfo_rooty(),
                        widget.winfo_width() or 1, widget.winfo_height() or 1)

            x, y = place_near_host(
                geom(anchor_widget), (width, height), geom(host_widget))
            self.geometry("+%d+%d" % (x, y))
        except (tk.TclError, ValueError):
            pass
        try:
            self.grab_set()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def _render(self) -> None:
        """按当前年月刷新 42 个日期按钮。"""
        self.var_month.set("%d 年 %02d 月" % (self._year, self._month))
        cells = month_cells(self._year, self._month)
        for row in range(_ROWS):
            for col in range(_COLS):
                button = self._day_buttons[row * _COLS + col]
                day = cells[row][col]
                if day is None:
                    button.configure(text="", state="disabled",
                                     bg=theme.CARD_BG, fg=theme.TEXT_MUTED)
                    continue
                is_today = day == self._today
                button.configure(
                    text=str(day.day), state="normal",
                    bg=theme.SELECT_BG if is_today else theme.INPUT_BG,
                    fg=theme.ACCENT if is_today else theme.TEXT_DARK,
                    command=lambda picked=day: self._pick(picked),
                )

    def _shift(self, delta: int) -> None:
        """上 / 下月切换（支持跨年）。"""
        total = self._year * 12 + (self._month - 1) + delta
        year, month_index = divmod(total, 12)
        self._year = year
        self._month = month_index + 1
        self._render()

    def _pick(self, day: date) -> None:
        """选中日期 → 回调写回 due 文本 → 关闭。"""
        if self._on_pick is not None:
            self._on_pick(build_due_text(day, self.var_time.get()))
        self._close()

    def _pick_today(self) -> None:
        """「今天」按钮，取本机系统时间的当天。"""
        self._pick(datetime.now().date())

    def _clear(self) -> None:
        """清除预期完成时间（留空表示未设置）。"""
        if self._on_clear is not None:
            self._on_clear()
        self._close()

    def _close(self) -> None:
        """释放 grab 并销毁窗口；重复关闭不抛异常。"""
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass
