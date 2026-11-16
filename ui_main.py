# -*- coding: utf-8 -*-
"""主窗口：圆角卡片外观、搜索 / 排序、自绘事务列表、配色标签、设置入口。

界面结构（对应手绘草图）：
    ┌──────────── 大圆角矩形卡片窗口 ────────────┐
    │ 「搜索」输入框   「排序」下拉   ＋新建  配色设置  │
    │  ~~~~~~~ 圆角分隔弧线 ~~~~~~~                │
    │ [彩色标签] 事务标题        创建时间，预期完成时间 │
    │ ─────────────── 横线 ────────────────────── │
    │ [彩色标签] 事务标题        创建时间，预期完成时间 │
    │ ...                    │  右侧详情面板(默认隐藏) │
    │ 状态栏：共 N 条 / 未完成 M 条 / 系统时间        │
    └──────────────────────────────────────────────┘

「＋ 新建」只填标题即可创建事务；双击某一行 → 右侧展开 DetailPanel，
在详情里补充预期完成时间、紧急程度、状态、进度，编辑文本与记录进度。
关闭按钮只隐藏到系统托盘，不退出程序。
"""
from __future__ import annotations

import math
import os
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Optional

import importer
import theme
from config import (
    AppConfig,
    COLOR_KEY_LABELS,
    COLOR_KEYS,
    COLOR_PRESETS,
    DEFAULT_COLOR_MAP,
    DEFAULT_DUE_SOON_HOURS,
    DEFAULT_UNLOCK_DUE_HOURS,
    DEFAULT_HOTKEY,
    DEFAULT_NEW_HOTKEY,
    color_for_memo,
    color_key_for_memo,
    is_valid_hex,
)
from hotkey import (
    HOTKEY_PRESETS,
    NEW_HOTKEY_PRESETS,
    is_valid_hotkey_text,
    normalize_hotkey_text,
)
from models import (
    Memo,
    SORT_CREATED,
    SORT_MODES,
    SORT_MODE_LABELS,
    URGENCY_MEDIUM,
    now_str,
)
from ui_detail import DetailPanel

LABEL_TO_SORT = {label: key for key, label in SORT_MODES}
_PRESET_VALUES = ["%s %s" % (hex_value, name) for hex_value, name in COLOR_PRESETS]


def round_rect_points(x1: float, y1: float, x2: float, y2: float,
                      radius: float, steps: int = 8) -> List[float]:
    """生成圆角矩形的多边形顶点（四角各采样四分之一圆）。

    返回扁平化坐标列表，可直接传给 Canvas.create_polygon / create_line。
    """
    r = max(0.0, min(float(radius), (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    n = max(1, int(steps))
    points = []  # type: List[float]
    corners = (
        (x2 - r, y1 + r, -math.pi / 2.0),
        (x2 - r, y2 - r, 0.0),
        (x1 + r, y2 - r, math.pi / 2.0),
        (x1 + r, y1 + r, math.pi),
    )
    for cx, cy, start in corners:
        for index in range(n + 1):
            angle = start + (math.pi / 2.0) * index / float(n)
            points.append(cx + r * math.cos(angle))
            points.append(cy + r * math.sin(angle))
    return points


class MainWindow(object):
    """备忘录主窗口控制器。"""

    def __init__(
        self,
        root: tk.Tk,
        store: Any,
        config: AppConfig,
        config_path: str,
        on_request_exit: Any = None,
    ):
        self.root = root
        self.store = store
        self.config = config
        self.config_path = config_path
        self._on_request_exit = on_request_exit

        self._keyword = ""
        self._sort_mode = config.sort_mode if config.sort_mode in SORT_MODE_LABELS else SORT_CREATED
        self._selected_id = ""
        self._scroll = 0
        self._rows = []  # type: List[Memo]
        self._detail_open = False
        self._never_shown = True
        self._hint = ""
        self._closed = False
        self._drag_ox = 0
        self._drag_oy = 0
        self._hotkey_applier = None  # 由 MemoApp 注入：应用全局热键的回调
        self._resizing = False
        self._resize_edge = ""  # n / s / e / w / ne / nw / se / sw
        self._resize_start = (0, 0, 0, 0)  # x_root, y_root, width, height
        self._resize_anchor = (0, 0)  # 窗口左上角屏幕坐标（nw/w/n 方向拖动时用）

        self._setup_style()
        self._build_window()
        self._build_header()
        self._build_separator()
        self._build_body()
        self._build_footer()
        self.refresh()
        self._tick()

    # ------------------------------------------------------------------
    # 样式与窗口骨架
    # ------------------------------------------------------------------
    def _setup_style(self) -> None:
        """配置 ttk 浅色主题。"""
        try:
            style = ttk.Style(self.root)
            if "clam" in style.theme_names():
                style.theme_use("clam")
            style.configure("TCombobox", fieldbackground=theme.INPUT_BG,
                            background=theme.HEADER_BG, foreground=theme.TEXT_DARK,
                            arrowcolor=theme.TEXT_DARK, bordercolor=theme.INPUT_BORDER)
            style.configure("TButton", background=theme.HEADER_BG,
                            foreground=theme.TEXT_DARK, borderwidth=0,
                            padding=(8, 4))
            style.map("TButton", background=[("active", theme.SELECT_BG)])
            style.configure("TFrame", background=theme.CARD_BG)
            style.configure("TLabel", background=theme.CARD_BG,
                            foreground=theme.TEXT_DARK)
        except tk.TclError:
            pass

    def _build_window(self) -> None:
        """创建无边框透明键色窗口 + 圆角卡片 Canvas。"""
        self.root.title("备忘录工具")
        self._apply_app_icon()
        self.root.overrideredirect(True)
        try:
            self.root.attributes("-transparentcolor", theme.TRANSPARENT_KEY)
        except tk.TclError:
            pass
        self.root.configure(bg=theme.TRANSPARENT_KEY)
        self.root.geometry("%dx%d" % (theme.WIN_LIST_W, theme.WIN_H))

        self.canvas = tk.Canvas(self.root, bg=theme.TRANSPARENT_KEY,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.container = tk.Frame(self.canvas, bg=theme.CARD_BG)
        self._win_item = self.canvas.create_window(
            8, 8, anchor="nw", window=self.container, tags=("content",)
        )
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        # 窗口边缘热区：实现 8 方向自由调整大小
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_resize_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        # 关键：全局监听 Motion。Tk 的空光标会继承父级，内部控件的光标
        # 全部继承自 Canvas；若只在 Canvas 上监听，指针进入内部后 Canvas
        # 收不到事件，边缘设置的调整光标会“卡”在整个窗口上。
        # 这里按指针相对 Canvas 的实时位置统一计算光标，离开热区即复位。
        self.root.bind_all("<Motion>", self._on_any_motion, add="+")
        # 卡片内容容器的空白区域同样可按住拖动窗口
        self.container.bind("<ButtonPress-1>", self._start_drag)
        self.container.bind("<B1-Motion>", self._on_drag)

    def _apply_app_icon(self) -> None:
        """给窗口/任务栏设置应用图标（tk.PhotoImage 仅支持 PNG/GIF）。

        依次尝试源码目录 assets/icon.png、PyInstaller 打包时经 --add-data
        解包到 _MEIPASS 的 assets/icon.png；都不可用时静默跳过（保持默认）。
        """
        candidates = []
        try:
            candidates.append(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png"))
        except Exception:
            pass
        bundle_dir = getattr(sys, "_MEIPASS", "")  # PyInstaller 解包目录
        if bundle_dir:
            candidates.append(os.path.join(bundle_dir, "assets", "icon.png"))
        for path in candidates:
            if path and os.path.isfile(path):
                try:
                    photo = tk.PhotoImage(file=path)
                    self.root.iconphoto(True, photo)
                    self._icon_photo = photo  # 防止被 GC 回收
                    return
                except tk.TclError:
                    continue

    def _on_canvas_configure(self, event: Any) -> None:
        """窗口尺寸变化时重绘圆角卡片并同步内容容器尺寸。"""
        width, height = int(event.width), int(event.height)
        if width < 40 or height < 40:
            return
        self._draw_card(width, height)
        try:
            self.canvas.itemconfigure(self._win_item,
                                      width=max(10, width - 16),
                                      height=max(10, height - 16))
        except tk.TclError:
            pass
        self.draw_rows()

    def _draw_card(self, width: int, height: int) -> None:
        """绘制浅色圆角矩形卡片背景。"""
        self.canvas.delete("card")
        points = round_rect_points(2, 2, width - 2, height - 2,
                                   theme.CARD_RADIUS, 10)
        self.canvas.create_polygon(points, fill=theme.CARD_BG,
                                   outline=theme.CARD_BORDER, width=1,
                                   tags=("card",))
        self.canvas.tag_lower("card")
        self.canvas.tag_raise("content")

    # ------------------------------------------------------------------
    # 顶部：搜索 / 排序 / 按钮
    # ------------------------------------------------------------------
    def _build_header(self) -> None:
        """构建顶部控件行。"""
        header = tk.Frame(self.container, bg=theme.CARD_BG)
        header.pack(fill="x", padx=theme.PAD, pady=(14, 2))

        drag_label = tk.Label(header, text="备忘录", font=theme.FONT_TITLE,
                              bg=theme.CARD_BG, fg=theme.TEXT_DARK)
        drag_label.pack(side="left", padx=(0, 4))
        # 标题左侧的空白区域也可按住拖动窗口
        drag_spacer = tk.Frame(header, width=8, bg=theme.CARD_BG)
        drag_spacer.pack(side="left", padx=(0, 4))
        for widget in (header, drag_label, drag_spacer):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)

        tk.Label(header, text="搜索", font=theme.FONT_LABEL, bg=theme.CARD_BG,
                 fg=theme.TEXT_MUTED).pack(side="left", padx=(8, 4))
        self.var_search = tk.StringVar(value="")
        tk.Entry(header, textvariable=self.var_search, width=12,
                 font=theme.FONT_LABEL, bg=theme.INPUT_BG, fg=theme.TEXT_DARK,
                 relief="flat", highlightthickness=1,
                 highlightbackground=theme.INPUT_BORDER,
                 highlightcolor=theme.ACCENT).pack(side="left", ipady=3)
        self.var_search.trace_add("write", self._on_search_changed)

        self.cmb_sort = ttk.Combobox(header, state="readonly", width=12,
                                     font=theme.FONT_LABEL,
                                     values=[label for _, label in SORT_MODES])
        try:
            self.cmb_sort.current(
                [key for key, _ in SORT_MODES].index(self._sort_mode)
            )
        except ValueError:
            self.cmb_sort.current(0)
        self.cmb_sort.pack(side="left", padx=(10, 0), ipady=2)
        self.cmb_sort.bind("<<ComboboxSelected>>", self._on_sort_changed)

        # 仅显示已完成：勾选后列表只列已完成任务
        self.var_done_only = tk.BooleanVar(value=False)
        self.chk_done_only = tk.Checkbutton(
            header, text="已完成", variable=self.var_done_only,
            command=self._on_done_only_changed, font=theme.FONT_LABEL,
            bg=theme.CARD_BG, fg=theme.TEXT_MUTED, activebackground=theme.CARD_BG,
            activeforeground=theme.TEXT_DARK, relief="flat",
            cursor="hand2", highlightthickness=0)
        self.chk_done_only.pack(side="left", padx=(8, 0))

        # 窗口保持最前：勾选后主窗口始终置顶（盖在其它窗口之上）
        self.var_topmost = tk.BooleanVar(value=False)
        self.chk_topmost = tk.Checkbutton(
            header, text="保持最前", variable=self.var_topmost,
            command=self._on_topmost_changed, font=theme.FONT_LABEL,
            bg=theme.CARD_BG, fg=theme.TEXT_MUTED, activebackground=theme.CARD_BG,
            activeforeground=theme.TEXT_DARK, relief="flat",
            cursor="hand2", highlightthickness=0)
        self.chk_topmost.pack(side="left", padx=(8, 0))

        self._make_button(header, "＋ 新建", self.new_memo, side="left", padx=(10, 4))
        self._make_button(header, "导入", self.open_import, side="left", padx=4)
        self._make_button(header, "设置", self.open_settings, side="left", padx=4)
        self._make_button(header, "—", self.hide_to_tray, side="right", padx=4, width=3)
        self._make_button(header, "×", self.hide_to_tray, side="right", padx=4, width=3)

    def _make_button(self, parent: Any, text: str, command: Any,
                     side: str = "left", padx: Any = 4, width: int = 0) -> None:
        """创建统一的浅色按钮。"""
        button = tk.Button(parent, text=text, font=theme.FONT_LABEL,
                           bg=theme.HEADER_BG, fg=theme.TEXT_DARK,
                           relief="flat", cursor="hand2", command=command)
        if width:
            button.configure(width=width)
        button.pack(side=side, padx=padx, ipady=2, ipadx=4)

    # ------------------------------------------------------------------
    # 圆角分隔弧线
    # ------------------------------------------------------------------
    def _build_separator(self) -> None:
        """构建草图中的圆角分隔弧线。"""
        separator = tk.Canvas(self.container, height=18, bg=theme.CARD_BG,
                              highlightthickness=0, bd=0)
        separator.pack(fill="x", padx=theme.PAD, pady=(2, 4))
        separator.bind("<Configure>", lambda event: self._draw_separator(separator, event.width))
        # 分隔弧线区域也可按住拖动窗口
        separator.bind("<ButtonPress-1>", self._start_drag)
        separator.bind("<B1-Motion>", self._on_drag)
        self._build_quick_new()

    def _build_quick_new(self) -> None:
        """创建默认隐藏的「只填标题」快速新建条。

        新建只需输入标题并回车，其余字段全部走默认值；用户之后双击列表行，
        才在右侧详情面板里补充预期完成时间、紧急程度、状态、进度等信息。
        """
        frame = tk.Frame(self.container, bg=theme.HEADER_BG)
        self.quick_frame = frame
        self._quick_open = False

        tk.Label(frame, text="新事务", font=theme.FONT_LABEL,
                 bg=theme.HEADER_BG, fg=theme.TEXT_MUTED).pack(
            side="left", padx=(10, 6))
        self.var_quick_title = tk.StringVar(value="")
        self.entry_quick_title = tk.Entry(
            frame, textvariable=self.var_quick_title, width=28,
            font=theme.FONT_BODY, bg=theme.INPUT_BG, fg=theme.TEXT_DARK,
            relief="flat", highlightthickness=1,
            highlightbackground=theme.INPUT_BORDER,
            highlightcolor=theme.ACCENT,
        )
        self.entry_quick_title.pack(side="left", ipady=3, pady=6)
        self.entry_quick_title.bind("<Return>", self._commit_quick_new)
        self.entry_quick_title.bind("<Escape>", self._cancel_quick_new)
        self._make_button(frame, "确定", self._commit_quick_new,
                          side="left", padx=(8, 2))
        self._make_button(frame, "取消", self._cancel_quick_new,
                          side="left", padx=2)

    @staticmethod
    def _draw_separator(canvas: Any, width: int) -> None:
        """画一条中间轻微下凹的平滑弧线。"""
        canvas.delete("all")
        w = max(20, int(width))
        points = [
            0.0, 4.0,
            w * 0.25, 5.0,
            w * 0.50, 11.0,
            w * 0.75, 5.0,
            float(w), 4.0,
        ]
        canvas.create_line(points, fill=theme.DIVIDER, width=2,
                           smooth=True, capstyle="round", splinesteps=16)

    # ------------------------------------------------------------------
    # 主体：列表 + 详情面板
    # ------------------------------------------------------------------
    def _build_body(self) -> None:
        """构建列表 Canvas 与右侧详情面板（grid 布局，详情默认隐藏）。"""
        body = tk.Frame(self.container, bg=theme.CARD_BG)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=(0, 4))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0)
        body.rowconfigure(0, weight=1)
        self.body = body

        self.list_canvas = tk.Canvas(body, bg=theme.CARD_BG,
                                     highlightthickness=0, bd=0)
        self.list_canvas.grid(row=0, column=0, sticky="nsew")
        self.list_canvas.bind("<Configure>", lambda e: self.draw_rows())
        self.list_canvas.bind("<Button-1>", self._on_row_click)
        self.list_canvas.bind("<Double-Button-1>", self._on_row_double_click)
        self.list_canvas.bind("<MouseWheel>", self._on_mouse_wheel)

        self.detail = DetailPanel(
            body,
            on_save=self._on_detail_save,
            on_delete=self._on_detail_delete,
            on_close=self.close_detail,
            config=self.config,
        )

    def _build_footer(self) -> None:
        """构建底部状态栏。"""
        footer = tk.Frame(self.container, bg=theme.CARD_BG)
        footer.pack(fill="x", padx=theme.PAD, pady=(0, 12))
        self.var_status = tk.StringVar(value="")
        status_label = tk.Label(footer, textvariable=self.var_status,
                                font=theme.FONT_SMALL,
                                bg=theme.CARD_BG, fg=theme.TEXT_MUTED,
                                anchor="w")
        status_label.pack(side="left", fill="x", expand=True)
        # 底部状态栏空白区域也可按住拖动窗口
        for widget in (footer, status_label):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)

    # ------------------------------------------------------------------
    # 窗口拖动与显隐
    # ------------------------------------------------------------------
    def _start_drag(self, event: Any) -> None:
        """记录拖动起点。"""
        self._drag_ox = int(event.x_root) - self.root.winfo_x()
        self._drag_oy = int(event.y_root) - self.root.winfo_y()

    def _on_drag(self, event: Any) -> None:
        """移动无边框窗口。"""
        x = int(event.x_root) - self._drag_ox
        y = int(event.y_root) - self._drag_oy
        self.root.geometry("+%d+%d" % (x, y))

    # ------------------------------------------------------------------
    # 窗口边缘自由调整大小（8 方向）
    # ------------------------------------------------------------------
    def _edge_at(self, x: int, y: int) -> str:
        """根据鼠标在 canvas 上的坐标返回所在边缘方向；内部区域返回空串。"""
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width < 40 or height < 40:
            return ""
        band = theme.RESIZE_BAND  # 边缘热区宽度（像素）
        left = x <= band
        right = x >= width - band
        top = y <= band
        bottom = y >= height - band
        parts = ""
        if top:
            parts += "n"
        elif bottom:
            parts += "s"
        if left:
            parts += "w"
        elif right:
            parts += "e"
        return parts

    _EDGE_CURSORS = {
        "n": "top_side", "s": "bottom_side", "e": "right_side",
        "w": "left_side", "ne": "top_right_corner", "nw": "top_left_corner",
        "se": "bottom_right_corner", "sw": "bottom_left_corner",
    }

    def _on_canvas_motion(self, event: Any) -> None:
        """兼容保留：Canvas 自身的 Motion 处理（全局监听已覆盖此场景）。"""
        self._apply_cursor_for(int(event.x), int(event.y))

    def _on_any_motion(self, event: Any) -> None:
        """全局 Motion：无论指针在窗口哪个控件上，都统一计算光标。

        指针可能悬在内部控件（Entry / Combobox / 列表等）上，此时事件坐标
        是相对该控件的，不能直接用于边缘判断——需要换算成相对 Canvas 的
        坐标。复位时只清 Canvas 的光标，内部控件继承到的也就是普通箭头；
        有自己光标的控件（按钮 hand2、输入框 xterm）不受影响。
        """
        if self._resizing:
            return
        x, y = self._pointer_over_canvas()
        self._apply_cursor_for(x, y)

    def _pointer_over_canvas(self) -> Any:
        """把当前指针位置换算成相对 Canvas 的坐标；不在窗口内返回 None。"""
        try:
            px = self.root.winfo_pointerx()
            py = self.root.winfo_pointery()
            if self.root.winfo_containing(px, py) is None:
                return None
            return (px - self.canvas.winfo_rootx(),
                    py - self.canvas.winfo_rooty())
        except (tk.TclError, KeyError):
            return None

    def _apply_cursor_for(self, x: Any, y: Any) -> None:
        """按 Canvas 坐标设置边缘光标；离开热区立即复位为普通箭头。"""
        try:
            if x is None or not self._edge_at(int(x), int(y)):
                self.canvas.configure(cursor="")
            else:
                self.canvas.configure(
                    cursor=self._EDGE_CURSORS[self._edge_at(int(x), int(y))])
        except (tk.TclError, KeyError):
            pass

    def _on_canvas_press(self, event: Any) -> None:
        """在边缘热区按下 → 进入调整大小状态并记录起始几何。"""
        edge = self._edge_at(int(event.x), int(event.y))
        if not edge:
            return
        self._resizing = True
        self._resize_edge = edge
        try:
            self._resize_start = (
                int(event.x_root), int(event.y_root),
                self.root.winfo_width(), self.root.winfo_height(),
            )
            self._resize_anchor = (self.root.winfo_x(), self.root.winfo_y())
        except tk.TclError:
            self._resizing = False

    def _on_canvas_resize_motion(self, event: Any) -> None:
        """拖动边缘：按方向计算新宽高与位置，并做最小/屏幕尺寸钳制。"""
        if not self._resizing:
            return
        edge = self._resize_edge
        sx, sy, w0, h0 = self._resize_start
        dx = int(event.x_root) - sx
        dy = int(event.y_root) - sy
        x, y = self._resize_anchor

        min_w = theme.MIN_WINDOW_W
        min_h = theme.MIN_WINDOW_H
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
        except tk.TclError:
            sw, sh = 1920, 1080
        max_w = max(min_w, sw - 20)
        max_h = max(min_h, sh - 60)

        width = w0
        height = h0
        if "e" in edge:
            width = max(min_w, min(max_w, w0 + dx))
        if "s" in edge:
            height = max(min_h, min(max_h, h0 + dy))
        if "w" in edge:
            width = max(min_w, min(max_w, w0 - dx))
            x = x + (w0 - width)
        if "n" in edge:
            height = max(min_h, min(max_h, h0 - dy))
            y = y + (h0 - height)
        x = max(-width + 60, min(x, sw - 60))
        y = max(0, min(y, sh - 40))
        try:
            self.root.geometry("%dx%d+%d+%d" % (width, height, x, y))
        except tk.TclError:
            self._resizing = False

    def _on_canvas_release(self, event: Any) -> None:
        """松开边缘：结束调整大小状态并恢复光标。"""
        if self._resizing:
            self._resizing = False
            self._resize_edge = ""
            try:
                self.canvas.configure(cursor="")
            except tk.TclError:
                pass

    def _place_bottom_right(self) -> None:
        """首次显示时把卡片定位到屏幕右下区域（贴近托盘）。"""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, sw - theme.WIN_LIST_W - 28)
        y = max(0, sh - theme.WIN_H - 70)
        self.root.geometry("%dx%d+%d+%d" % (theme.WIN_LIST_W, theme.WIN_H, x, y))

    def show(self) -> None:
        """显示 / 恢复主窗口。"""
        if self._closed:
            return
        if self._never_shown:
            self._place_bottom_right()
            self._never_shown = False
        self.root.deiconify()
        try:
            self.root.overrideredirect(True)
        except tk.TclError:
            pass
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(250, lambda: self._clear_topmost())
        except tk.TclError:
            pass
        self.refresh()

    def _clear_topmost(self) -> None:
        """短暂置顶后恢复；若勾选了「窗口保持最前」则保持置顶不取消。"""
        if self.var_topmost.get():
            return
        try:
            self.root.attributes("-topmost", False)
        except tk.TclError:
            pass

    def _on_topmost_changed(self) -> None:
        """「窗口保持最前」勾选切换：立即应用 / 取消置顶。"""
        try:
            self.root.attributes("-topmost", bool(self.var_topmost.get()))
        except tk.TclError:
            pass

    def hide_to_tray(self) -> None:
        """隐藏窗口到系统托盘（不退出程序）。"""
        try:
            self.root.withdraw()
        except tk.TclError:
            pass

    def _set_window_width(self, width: int) -> None:
        """展开 / 收起详情面板时调整窗口宽度，并保证不贴屏幕边缘。

        详情面板宽度固定，列表区可伸缩；小屏幕上把窗口收窄一点不会裁掉
        右侧属性区，只是列表变窄，同时留出 20px 左右边距保持圆角观感。
        """
        try:
            x = self.root.winfo_x()
            y = self.root.winfo_y()
            sw = self.root.winfo_screenwidth()
            width = max(640, min(int(width), sw - 40))
            if x + width > sw - 10:
                x = max(0, sw - width - 20)
            self.root.geometry("%dx%d+%d+%d" % (width, theme.WIN_H, x, y))
        except tk.TclError:
            self.root.geometry("%dx%d" % (width, theme.WIN_H))

    # ------------------------------------------------------------------
    # 列表绘制
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """重新查询数据并刷新列表与状态栏。"""
        rows = self.store.sorted_by(self._sort_mode, self._keyword)
        if self.var_done_only.get():
            rows = [memo for memo in rows if memo.is_done()]
        self._rows = rows
        self._scroll = max(0, min(self._scroll, self._max_scroll()))
        self.draw_rows()
        self._update_status()

    def _max_scroll(self) -> int:
        """当前可滚动的最大行偏移。"""
        height = self.list_canvas.winfo_height() or (theme.WIN_H - 140)
        visible = max(1, int(height) // theme.ROW_HEIGHT)
        return max(0, len(self._rows) - visible)

    def _round_rect(self, canvas: Any, x1: float, y1: float,
                    x2: float, y2: float, radius: float, **kwargs: Any) -> None:
        """在 Canvas 上画一个圆角矩形色块（用作彩色标签）。"""
        points = round_rect_points(x1, y1, x2, y2, radius, 5)
        canvas.create_polygon(points, **kwargs)

    def draw_rows(self) -> None:
        """自绘事务列表：彩色标签、标题、创建时间/预期完成时间、分隔横线。"""
        canvas = self.list_canvas
        canvas.delete("all")
        width = canvas.winfo_width() or (theme.WIN_LIST_W - 2 * theme.PAD)
        height = canvas.winfo_height() or 340
        if width < 60 or height < 40:
            return
        if not self._rows:
            canvas.create_text(width // 2, height // 2,
                               text="暂无事务，点击「＋ 新建」开始记录",
                               fill=theme.TEXT_MUTED, font=theme.FONT_BODY)
            return

        for index, memo in enumerate(self._rows):
            y0 = index * theme.ROW_HEIGHT - self._scroll * theme.ROW_HEIGHT
            y1 = y0 + theme.ROW_HEIGHT
            if y1 <= 0 or y0 >= height:
                continue
            color = color_for_memo(memo, self.config)

            if memo.id == self._selected_id:
                canvas.create_rectangle(4, y0 + 2, width - 4, y1 - 2,
                                        fill=theme.SELECT_BG, outline="")

            # 左侧：按「紧急程度 + 预期完成时间」填充颜色的标签
            self._round_rect(canvas, 10, y0 + 16, 66, y0 + 40, 8,
                             fill=color, outline="")
            canvas.create_text(38, y0 + 28, text=memo.urgency,
                               fill=theme.TEXT_LIGHT, font=theme.FONT_TINY)

            title = memo.title or "未命名事务"
            if len(title) > 24:
                title = title[:23] + "…"
            canvas.create_text(78, y0 + 10, anchor="nw", text=title,
                               fill=theme.TEXT_DARK, font=theme.FONT_BODY_BOLD)
            canvas.create_text(78, y0 + 33, anchor="nw",
                               text="%s · 进度 %d%% · %s" % (
                                   memo.status, memo.progress, memo.urgency),
                               fill=theme.TEXT_MUTED, font=theme.FONT_SMALL)

            # 右侧：创建时间，预期完成时间（已逾期时「预期 …」用红色）
            created_text = "创建 %s" % memo.created_at
            canvas.create_text(width - 12, y0 + 24, anchor="ne",
                               text=created_text, fill=theme.TEXT_MUTED,
                               font=theme.FONT_SMALL, tags=("right_text",))
            due_text = "，预期 %s" % (memo.due_at or "未设置")
            if memo.due_at and color_key_for_memo(memo, self.config) in (
                    "overdue_high", "overdue"):
                due_color = theme.OVERDUE_TEXT
            else:
                due_color = theme.TEXT_MUTED
            created_width = int(
                tk.font.Font(root=canvas, font=theme.FONT_SMALL).measure(created_text))
            canvas.create_text(width - 12 - created_width, y0 + 24,
                               anchor="ne", text=due_text, fill=due_color,
                               font=theme.FONT_SMALL, tags=("right_text",))

            # 行间横线
            canvas.create_line(10, y1 - 1, width - 10, y1 - 1,
                               fill=theme.DIVIDER, width=1)

    def _row_at(self, y: int) -> Optional[Memo]:
        """根据鼠标 y 坐标命中事务行。"""
        index = int((int(y) + self._scroll * theme.ROW_HEIGHT) // theme.ROW_HEIGHT)
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    # ------------------------------------------------------------------
    # 列表交互
    # ------------------------------------------------------------------
    def _on_row_click(self, event: Any) -> None:
        """单击选中行。"""
        memo = self._row_at(event.y)
        self._selected_id = memo.id if memo is not None else ""
        self.draw_rows()

    def _on_row_double_click(self, event: Any) -> None:
        """双击行 → 右侧展开任务详情。"""
        memo = self._row_at(event.y)
        if memo is not None:
            self.show_detail(memo)

    def _on_mouse_wheel(self, event: Any) -> None:
        """滚轮滚动自绘列表。"""
        delta = -1 if int(event.delta) > 0 else 1
        self._scroll = max(0, min(self._max_scroll(), self._scroll + delta))
        self.draw_rows()

    def _on_search_changed(self, *args: Any) -> None:
        """搜索关键字变化。"""
        self._keyword = self.var_search.get()
        self._scroll = 0
        self.refresh()

    def _on_sort_changed(self, event: Any = None) -> None:
        """排序模式切换（同时持久化到配置文件）。"""
        self._sort_mode = LABEL_TO_SORT.get(self.cmb_sort.get(), SORT_CREATED)
        self.config.sort_mode = self._sort_mode
        try:
            self.config.save(self.config_path)
        except OSError:
            pass
        self._scroll = 0
        self.refresh()

    def _on_done_only_changed(self) -> None:
        """「仅显示已完成」勾选切换：复位滚动并刷新列表。"""
        self._scroll = 0
        self.refresh()

    # ------------------------------------------------------------------
    # 详情面板联动
    # ------------------------------------------------------------------
    def show_detail(self, memo: Memo) -> None:
        """展开右侧详情面板并加宽窗口。"""
        self._selected_id = memo.id
        self._detail_open = True
        self.detail.show(memo)
        self._set_window_width(theme.WIN_DETAIL_W)
        self.draw_rows()

    def close_detail(self) -> None:
        """收起右侧详情面板并恢复窗口宽度。"""
        self._detail_open = False
        self.detail.hide()
        self._set_window_width(theme.WIN_LIST_W)
        self.draw_rows()

    def new_memo(self) -> None:
        """「＋ 新建」：只展开一行标题输入，不再弹出完整详情表单。"""
        if not self._quick_open:
            self.quick_frame.pack(fill="x", padx=theme.PAD, pady=(0, 4),
                                  before=self.body)
        self._quick_open = True
        self.var_quick_title.set("")
        self.entry_quick_title.focus_set()

    def _cancel_quick_new(self, event: Any = None) -> None:
        """Esc / 取消：收起快速新建条，不创建任何事务。"""
        self._quick_open = False
        self.var_quick_title.set("")
        self.quick_frame.pack_forget()

    def _commit_quick_new(self, event: Any = None) -> Optional[Memo]:
        """回车 / 确定：用标题创建事务；空标题直接忽略，不弹窗。"""
        title = self.var_quick_title.get()
        if not title.strip():
            self.entry_quick_title.focus_set()
            return None
        memo = self.create_memo_from_title(title)
        if memo is None:
            return None
        self._cancel_quick_new()
        return memo

    def create_memo_from_title(self, title: str) -> Optional[Memo]:
        """只用标题创建事务并立即入库，其余字段全部使用默认值。

        创建时间由 Memo 自动写入本机系统时间；预期完成时间、紧急程度、
        状态、内容、进度与进度记录都留到用户双击该行后在详情面板里设置。
        """
        text = (title or "").strip()
        if not text:
            return None
        memo = self.store.create_memo(title=text, urgency=URGENCY_MEDIUM)
        self.store.add(memo)
        try:
            self.store.save()
        except OSError as exc:
            messagebox.showerror("保存失败", "无法写入数据文件：%s" % exc)
            return None
        # 清掉搜索过滤，保证刚创建的行一定出现在列表里
        self.var_search.set("")
        self._selected_id = memo.id
        if self._detail_open:
            self.close_detail()
        self.refresh()
        return memo

    def _on_detail_save(self, memo: Memo) -> None:
        """详情自动保存：新建则 add，已存在则 update。

        不回调 DetailPanel.show()，避免连续输入时反复重置表单与焦点。
        """
        if self.store.get(memo.id) is not None:
            self.store.update(memo)
        else:
            self.store.add(memo)
        try:
            self.store.save()
        except OSError as exc:
            messagebox.showerror("保存失败", "无法写入数据文件：%s" % exc)
            return
        self._selected_id = memo.id
        self.refresh()

    def _on_detail_delete(self, memo_id: str) -> None:
        """删除事务（先放弃待写入的改动，避免刚删又被自动保存写回）。"""
        self.detail.clear()
        self.store.delete(memo_id)
        try:
            self.store.save()
        except OSError as exc:
            messagebox.showerror("保存失败", "无法写入数据文件：%s" % exc)
        self._selected_id = ""
        self.close_detail()
        self.refresh()

    # ------------------------------------------------------------------
    # 状态栏 / 系统时间
    # ------------------------------------------------------------------
    def set_status_hint(self, text: str) -> None:
        """设置一条临时状态提示（解锁提醒时使用）。"""
        self._hint = text or ""
        self._update_status()

    def _update_status(self) -> None:
        """刷新状态栏（每次刷新都取本机系统时间）。"""
        try:
            hint = (" · " + self._hint) if self._hint else ""
            done_only = ""
            if self.var_done_only.get():
                done_only = " · 已完成 %d 条" % sum(
                    1 for memo in self.store.list_all() if memo.is_done())
            self.var_status.set(
                "共 %d 条 · 未完成 %d 条%s · 系统时间 %s%s" % (
                    self.store.count_all(),
                    self.store.count_pending(),
                    done_only,
                    now_str(),
                    hint,
                )
            )
        except tk.TclError:
            pass

    def _tick(self) -> None:
        """每秒刷新状态栏的系统时间显示。"""
        if self._closed:
            return
        try:
            self._update_status()
            self.root.after(1000, self._tick)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # 导入窗口（文本批量导入）
    # ------------------------------------------------------------------
    def open_import(self) -> None:
        """打开「导入」窗口：粘贴多行文本批量创建/更新事务。

        规则：√ 或 done = 已完成；连续两个以上 - 分隔「标题 -- 进展说明」，
        无完成标记则未完成，且说明同步为该任务的进展记录。
        """
        window = tk.Toplevel(self.root)
        window.title("导入事务")
        window.configure(bg=theme.CARD_BG)
        window.geometry("560x460")
        window.resizable(True, True)
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass

        tk.Label(window, text="粘贴要导入的文本（每行一条事务）", font=theme.FONT_TITLE,
                 bg=theme.CARD_BG, fg=theme.TEXT_DARK).pack(anchor="w", padx=16,
                                                            pady=(14, 2))
        tk.Label(window,
                 text="行尾 √ 或 done = 已完成；连续两个以上 - 分隔「标题--进展说明」，"
                      "没有完成标记则未完成，说明会作为进展记录写入该任务；"
                      "标题与现有事务相同则更新，不重复创建。",
                 font=theme.FONT_TINY, bg=theme.CARD_BG, fg=theme.TEXT_MUTED,
                 wraplength=520, justify="left").pack(anchor="w", padx=16, pady=(0, 8))

        example = ("示例：\n"
                   "1、绑定方案，协议--芯片实现初步学习    √\n"
                   "2、绑定方案，测试                                    done\n"
                   "3、绑定方案，实现                                    ---待调试")
        tk.Label(window, text=example, font=theme.FONT_TINY_PLAIN,
                 bg=theme.HEADER_BG, fg=theme.TEXT_MUTED, justify="left",
                 padx=10, pady=6).pack(fill="x", padx=16)

        text_widget = tk.Text(window, height=10, wrap="none",
                              font=theme.FONT_SMALL, bg=theme.INPUT_BG,
                              fg=theme.TEXT_DARK, relief="flat",
                              highlightthickness=1,
                              highlightbackground=theme.INPUT_BORDER,
                              highlightcolor=theme.ACCENT, undo=True)
        text_widget.pack(fill="both", expand=True, padx=16, pady=(10, 6))
        text_widget.focus_set()

        var_result = tk.StringVar(value="")
        tk.Label(window, textvariable=var_result, font=theme.FONT_SMALL,
                 bg=theme.CARD_BG, fg=theme.TEXT_MUTED, anchor="w").pack(
            fill="x", padx=16)

        def do_import() -> None:
            raw = text_widget.get("1.0", "end")
            items = importer.parse_text(raw)
            if not items:
                messagebox.showinfo("没有可导入内容", "文本为空或没有可识别的事务行。",
                                    parent=window)
                return
            created, updated, _ = importer.apply_import(self.store, items)
            try:
                self.store.save()
            except OSError as exc:
                messagebox.showerror("保存失败", "无法写入数据文件：%s" % exc,
                                     parent=window)
                return
            self.refresh()
            var_result.set("导入完成：新建 %d 条，更新 %d 条（共 %d 行）。"
                           % (created, updated, len(items)))
            text_widget.delete("1.0", "end")

        buttons = tk.Frame(window, bg=theme.CARD_BG)
        buttons.pack(fill="x", padx=16, pady=(6, 14))
        tk.Button(buttons, text="导入", font=theme.FONT_BODY_BOLD,
                  bg=theme.ACCENT, fg=theme.TEXT_LIGHT, relief="flat",
                  cursor="hand2", width=10, command=do_import).pack(side="left")
        tk.Button(buttons, text="关闭", font=theme.FONT_LABEL,
                  bg=theme.HEADER_BG, fg=theme.TEXT_DARK, relief="flat",
                  cursor="hand2", width=8,
                  command=window.destroy).pack(side="right")

    # ------------------------------------------------------------------
    # 设置窗口（配色与规则 + 快捷键）
    # ------------------------------------------------------------------
    def open_settings(self) -> None:
        """打开「设置」窗口：配色规则、临期阈值、排序、全局快捷键。"""
        window = tk.Toplevel(self.root)
        window.title("设置")
        window.configure(bg=theme.CARD_BG)
        window.geometry("520x600")
        window.resizable(False, False)
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass

        # ---------------- 配色与规则 ----------------
        color_frame = tk.LabelFrame(window, text="配色与规则",
                                    font=theme.FONT_LABEL, bg=theme.CARD_BG,
                                    fg=theme.TEXT_DARK)
        color_frame.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(color_frame,
                 text="选择预设颜色，或直接填写 #RRGGBB。规则按「紧急程度 + 预期完成时间 + 状态」判定。",
                 font=theme.FONT_TINY, bg=theme.CARD_BG, fg=theme.TEXT_MUTED,
                 wraplength=460, justify="left").pack(anchor="w", padx=10,
                                                      pady=(2, 6))

        grid = tk.Frame(color_frame, bg=theme.CARD_BG)
        grid.pack(fill="x", padx=10, pady=(0, 6))
        grid.columnconfigure(1, weight=1)

        color_vars = {}  # type: Dict[str, tk.StringVar]
        previews = {}  # type: Dict[str, tk.Label]
        for index, key in enumerate(COLOR_KEYS):
            tk.Label(grid, text=COLOR_KEY_LABELS.get(key, key),
                     font=theme.FONT_LABEL, bg=theme.CARD_BG,
                     fg=theme.TEXT_DARK, anchor="w", width=14).grid(
                row=index, column=0, sticky="w", pady=3)
            variable = tk.StringVar(value=self.config.get_color(key))
            combo = ttk.Combobox(grid, textvariable=variable,
                                 values=_PRESET_VALUES, font=theme.FONT_LABEL,
                                 width=22)
            combo.grid(row=index, column=1, sticky="w", pady=3)
            preview = tk.Label(grid, text="  ", width=3, bg=self.config.get_color(key),
                               relief="flat")
            preview.grid(row=index, column=2, padx=(8, 0))

            def _on_color_change(event=None, var=variable, lab=preview):
                self._refresh_preview(var, lab)

            combo.bind("<<ComboboxSelected>>", _on_color_change)
            combo.bind("<KeyRelease>", _on_color_change)
            color_vars[key] = variable
            previews[key] = preview

        options = tk.Frame(color_frame, bg=theme.CARD_BG)
        options.pack(fill="x", padx=10, pady=(6, 8))
        tk.Label(options, text="临期阈值(小时)", font=theme.FONT_LABEL,
                 bg=theme.CARD_BG, fg=theme.TEXT_DARK, width=14,
                 anchor="w").grid(row=0, column=0, sticky="w", pady=4)
        hours_var = tk.IntVar(value=self.config.due_soon_hours)
        tk.Spinbox(options, from_=1, to=8760, textvariable=hours_var, width=10,
                   font=theme.FONT_LABEL, bg=theme.INPUT_BG, fg=theme.TEXT_DARK,
                   relief="flat").grid(row=0, column=1, sticky="w", pady=4)

        tk.Label(options, text="解锁提醒提前(小时)", font=theme.FONT_LABEL,
                 bg=theme.CARD_BG, fg=theme.TEXT_DARK, width=18,
                 anchor="w").grid(row=1, column=0, sticky="w", pady=4)
        unlock_hours_var = tk.IntVar(value=self.config.unlock_due_hours)
        tk.Spinbox(options, from_=1, to=8760, textvariable=unlock_hours_var,
                   width=10, font=theme.FONT_LABEL, bg=theme.INPUT_BG,
                   fg=theme.TEXT_DARK, relief="flat").grid(
            row=1, column=1, sticky="w", pady=4)

        tk.Label(options, text="默认排序", font=theme.FONT_LABEL,
                 bg=theme.CARD_BG, fg=theme.TEXT_DARK, width=14,
                 anchor="w").grid(row=2, column=0, sticky="w", pady=4)
        sort_var = tk.StringVar(value=SORT_MODE_LABELS.get(self.config.sort_mode,
                                                           SORT_MODES[0][1]))
        ttk.Combobox(options, textvariable=sort_var, state="readonly", width=16,
                     font=theme.FONT_LABEL,
                     values=[label for _, label in SORT_MODES]).grid(
            row=2, column=1, sticky="w", pady=4)

        # ---------------- 快捷键 ----------------
        hotkey_frame = tk.LabelFrame(window, text="快捷键",
                                     font=theme.FONT_LABEL, bg=theme.CARD_BG,
                                     fg=theme.TEXT_DARK)
        hotkey_frame.pack(fill="x", padx=14, pady=(6, 4))
        tk.Label(hotkey_frame,
                 text="快捷呼出：在「弹出 / 最小化」之间切换主窗口；"
                      "快捷新建：弹出窗口并展开新建输入行。"
                      "示例：F2、Ctrl+Alt+M、Ctrl+Shift+F9。修改后点「保存设置」生效。",
                 font=theme.FONT_TINY, bg=theme.CARD_BG, fg=theme.TEXT_MUTED,
                 wraplength=460, justify="left").pack(anchor="w", padx=10,
                                                      pady=(2, 6))
        hotkey_row = tk.Frame(hotkey_frame, bg=theme.CARD_BG)
        hotkey_row.pack(fill="x", padx=10, pady=(0, 8))
        tk.Label(hotkey_row, text="快捷呼出", font=theme.FONT_LABEL,
                 bg=theme.CARD_BG, fg=theme.TEXT_DARK, width=14,
                 anchor="w").pack(side="left")
        hotkey_var = tk.StringVar(value=self.config.hotkey)
        hotkey_entry = tk.Entry(hotkey_row, textvariable=hotkey_var, width=18,
                                font=theme.FONT_LABEL, bg=theme.INPUT_BG,
                                fg=theme.TEXT_DARK, relief="flat",
                                highlightthickness=1,
                                highlightbackground=theme.INPUT_BORDER,
                                highlightcolor=theme.ACCENT)
        hotkey_entry.pack(side="left", ipady=3)
        hotkey_status = tk.Label(hotkey_row, text="", font=theme.FONT_TINY,
                                 bg=theme.CARD_BG, fg=theme.TEXT_MUTED)
        hotkey_status.pack(side="left", padx=(8, 0))

        def _on_hotkey_changed(*args: Any) -> None:
            text = hotkey_var.get().strip()
            if not text:
                hotkey_status.configure(text="", fg=theme.TEXT_MUTED)
            elif is_valid_hotkey_text(text):
                hotkey_status.configure(
                    text="有效：%s" % normalize_hotkey_text(text),
                    fg=theme.TEXT_MUTED)
            else:
                hotkey_status.configure(text="无法识别", fg=theme.DANGER)

        hotkey_var.trace_add("write", _on_hotkey_changed)
        _on_hotkey_changed()

        def _pick_hotkey_preset(text: str) -> None:
            hotkey_var.set(text)

        presets_row = tk.Frame(hotkey_frame, bg=theme.CARD_BG)
        presets_row.pack(fill="x", padx=10, pady=(0, 8))
        tk.Label(presets_row, text="常用：", font=theme.FONT_TINY,
                 bg=theme.CARD_BG, fg=theme.TEXT_MUTED).pack(side="left")
        for preset in HOTKEY_PRESETS:
            tk.Button(presets_row, text=preset, font=theme.FONT_TINY,
                      bg=theme.HEADER_BG, fg=theme.TEXT_DARK, relief="flat",
                      cursor="hand2",
                      command=lambda t=preset: _pick_hotkey_preset(t)).pack(
                side="left", padx=(6, 0))

        # 第二组：快速新建（呼出窗口并展开新建输入行）
        new_row = tk.Frame(hotkey_frame, bg=theme.CARD_BG)
        new_row.pack(fill="x", padx=10, pady=(0, 4))
        tk.Label(new_row, text="快捷新建", font=theme.FONT_LABEL,
                 bg=theme.CARD_BG, fg=theme.TEXT_DARK, width=14,
                 anchor="w").pack(side="left")
        new_hotkey_var = tk.StringVar(value=self.config.new_hotkey)
        tk.Entry(new_row, textvariable=new_hotkey_var, width=18,
                 font=theme.FONT_LABEL, bg=theme.INPUT_BG,
                 fg=theme.TEXT_DARK, relief="flat", highlightthickness=1,
                 highlightbackground=theme.INPUT_BORDER,
                 highlightcolor=theme.ACCENT).pack(side="left", ipady=3)
        new_hotkey_status = tk.Label(new_row, text="", font=theme.FONT_TINY,
                                     bg=theme.CARD_BG, fg=theme.TEXT_MUTED)
        new_hotkey_status.pack(side="left", padx=(8, 0))

        def _on_new_hotkey_changed(*args: Any) -> None:
            text = new_hotkey_var.get().strip()
            if not text:
                new_hotkey_status.configure(text="", fg=theme.TEXT_MUTED)
            elif is_valid_hotkey_text(text):
                new_hotkey_status.configure(
                    text="有效：%s" % normalize_hotkey_text(text),
                    fg=theme.TEXT_MUTED)
            else:
                new_hotkey_status.configure(text="无法识别", fg=theme.DANGER)

        new_hotkey_var.trace_add("write", _on_new_hotkey_changed)
        _on_new_hotkey_changed()

        new_presets_row = tk.Frame(hotkey_frame, bg=theme.CARD_BG)
        new_presets_row.pack(fill="x", padx=10, pady=(0, 8))
        tk.Label(new_presets_row, text="常用：", font=theme.FONT_TINY,
                 bg=theme.CARD_BG, fg=theme.TEXT_MUTED).pack(side="left")
        for preset in NEW_HOTKEY_PRESETS:
            tk.Button(new_presets_row, text=preset, font=theme.FONT_TINY,
                      bg=theme.HEADER_BG, fg=theme.TEXT_DARK, relief="flat",
                      cursor="hand2",
                      command=lambda t=preset: new_hotkey_var.set(t)).pack(
                side="left", padx=(6, 0))

        buttons = tk.Frame(window, bg=theme.CARD_BG)
        buttons.pack(fill="x", side="bottom", padx=16, pady=14)
        tk.Button(buttons, text="保存设置", font=theme.FONT_BODY_BOLD,
                  bg=theme.ACCENT, fg=theme.TEXT_LIGHT, relief="flat",
                  cursor="hand2", width=10,
                  command=lambda: self._save_settings(
                      window, color_vars, hours_var, sort_var,
                      hotkey_var, new_hotkey_var, unlock_hours_var)).pack(side="left")
        tk.Button(buttons, text="恢复默认", font=theme.FONT_LABEL,
                  bg=theme.HEADER_BG, fg=theme.TEXT_DARK, relief="flat",
                  cursor="hand2", width=9,
                  command=lambda: self._reset_settings(
                      color_vars, previews, hours_var, sort_var,
                      hotkey_var, new_hotkey_var, unlock_hours_var)).pack(
            side="left", padx=8)
        tk.Button(buttons, text="退出程序", font=theme.FONT_LABEL,
                  bg=theme.HEADER_BG, fg=theme.DANGER, relief="flat",
                  cursor="hand2", width=9,
                  command=self._exit_from_settings).pack(side="right")
        tk.Button(buttons, text="关闭", font=theme.FONT_LABEL,
                  bg=theme.HEADER_BG, fg=theme.TEXT_DARK, relief="flat",
                  cursor="hand2", width=8,
                  command=window.destroy).pack(side="right", padx=8)

    @staticmethod
    def _refresh_preview(variable: Any, preview: Any) -> None:
        """设置窗口中的颜色块预览。"""
        raw = variable.get().strip()
        hex_value = raw.split()[0] if raw else ""
        if is_valid_hex(hex_value):
            try:
                preview.configure(bg=hex_value)
            except tk.TclError:
                pass

    def _save_settings(self, window: Any, color_vars: Dict[str, tk.StringVar],
                       hours_var: Any, sort_var: Any,
                       hotkey_var: Any = None,
                       new_hotkey_var: Any = None,
                       unlock_hours_var: Any = None) -> None:
        """校验并保存设置，立即刷新列表颜色与全局热键。"""
        for key in COLOR_KEYS:
            raw = color_vars[key].get().strip()
            hex_value = raw.split()[0] if raw else ""
            if not self.config.set_color(key, hex_value):
                messagebox.showerror("输入有误",
                                     "「%s」的颜色值不合法，请填写 #RRGGBB。" %
                                     COLOR_KEY_LABELS.get(key, key),
                                     parent=window)
                return
        try:
            self.config.due_soon_hours = max(1, min(8760, int(hours_var.get())))
        except (tk.TclError, ValueError):
            self.config.due_soon_hours = DEFAULT_DUE_SOON_HOURS
        if unlock_hours_var is not None:
            try:
                self.config.unlock_due_hours = max(
                    1, min(8760, int(unlock_hours_var.get())))
            except (tk.TclError, ValueError):
                self.config.unlock_due_hours = DEFAULT_UNLOCK_DUE_HOURS
        self.config.sort_mode = LABEL_TO_SORT.get(sort_var.get(), SORT_CREATED)
        self._sort_mode = self.config.sort_mode
        try:
            self.cmb_sort.set(SORT_MODE_LABELS[self._sort_mode])
        except (tk.TclError, KeyError):
            pass
        # 快捷键：非法输入直接拦截在设置窗口里，不写盘不生效
        parsed_hotkeys = []  # type: List[str]
        for label, variable in (("快捷呼出", hotkey_var),
                                ("快捷新建", new_hotkey_var)):
            if variable is None:
                continue
            text = variable.get().strip()
            if not is_valid_hotkey_text(text):
                messagebox.showerror(
                    "输入有误",
                    "%s「%s」无法识别。\n示例：F2、Ctrl+Alt+M、Ctrl+Shift+F9。" %
                    (label, text),
                    parent=window)
                return
            parsed_hotkeys.append(normalize_hotkey_text(text))
        if len(parsed_hotkeys) == 2 and parsed_hotkeys[0] == parsed_hotkeys[1]:
            # 两个热键不能相同：Win32 同一组合注册两次会失败（1409）
            messagebox.showerror(
                "输入有误", "「快捷呼出」与「快捷新建」不能设置为同一个按键。",
                parent=window)
            return
        if hotkey_var is not None:
            self.config.hotkey = parsed_hotkeys[0]
        if new_hotkey_var is not None:
            self.config.new_hotkey = parsed_hotkeys[-1]
        try:
            self.config.save(self.config_path)
        except OSError as exc:
            messagebox.showerror("保存失败", "无法写入配置文件：%s" % exc,
                                 parent=window)
            return
        # 应用新热键（注册在托盘线程，线程安全）
        if hotkey_var is not None and getattr(self, "_hotkey_applier", None):
            try:
                self._hotkey_applier(self.config.hotkey)
            except Exception:
                pass
        if new_hotkey_var is not None and getattr(self, "_new_hotkey_applier", None):
            try:
                self._new_hotkey_applier(self.config.new_hotkey)
            except Exception:
                pass
        self.refresh()
        window.destroy()

    def _reset_settings(self, color_vars: Dict[str, tk.StringVar],
                        previews: Dict[str, tk.Label], hours_var: Any,
                        sort_var: Any, hotkey_var: Any = None,
                        new_hotkey_var: Any = None,
                        unlock_hours_var: Any = None) -> None:
        """把设置窗口表单恢复为默认值（不立即写盘，需点击保存）。"""
        for key in COLOR_KEYS:
            color_vars[key].set(DEFAULT_COLOR_MAP[key])
            try:
                previews[key].configure(bg=DEFAULT_COLOR_MAP[key])
            except tk.TclError:
                pass
        hours_var.set(DEFAULT_DUE_SOON_HOURS)
        if unlock_hours_var is not None:
            unlock_hours_var.set(DEFAULT_UNLOCK_DUE_HOURS)
        sort_var.set(SORT_MODES[0][1])
        if hotkey_var is not None:
            hotkey_var.set(DEFAULT_HOTKEY)
        if new_hotkey_var is not None:
            new_hotkey_var.set(DEFAULT_NEW_HOTKEY)

    def _exit_from_settings(self) -> None:
        """设置窗口中的退出入口（走完整清理流程）。"""
        if self._on_request_exit is not None:
            self._on_request_exit()
        else:
            self._closed = True
            self.root.quit()
