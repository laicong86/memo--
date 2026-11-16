# -*- coding: utf-8 -*-
"""右侧任务详情面板。

双击列表行后在窗口右侧展开，布局分为两栏：

* 顶部标题行：「任务详情」大标题右侧紧跟**属性条**——预期完成时间、
  紧急程度、状态、进度四个属性横向一字排开（标签在上、选择控件在下），
  预期完成时间点「日历」弹出月历选择。
* 下方编辑区：标题、**历史进展（按时间倒序，最近在最前，占满原备注区，
  格式「日期：进展」）**、写进展输入框（回车即记录，自动附加本机系统时间戳）。
  备注正文（memo.content）不在详情面板展示，保存时原样保留。

面板由主窗口通过 grid(row=0, column=1) 控制显隐；本模块提供
show(memo) / hide() / collect() 三个主要接口。

**自动保存**：任一属性或标题变更、进展追加后，经 400ms 防抖自动调用
on_save 回调写盘；收起面板时立即冲刷尚未写入的改动，不再需要「保存」按钮。
"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Any, Callable, List, Optional, Tuple

import theme
from calendar_picker import (
    CalendarPopup,
    join_due_text,
    split_due_text,
)
from models import (
    Memo,
    ProgressLog,
    STATUS_LEVELS,
    TIME_FORMAT,
    URGENCY_LEVELS,
    is_valid_time_text,
    normalize_progress,
    now_str,
    parse_time,
)

# 字段变更后延迟写盘的防抖时长（毫秒）：连续输入只触发一次保存
AUTO_SAVE_DELAY_MS = 400


class DetailPanel(tk.Frame):
    """可编辑的任务详情面板（左编辑区 + 右属性区）。"""

    def __init__(
        self,
        master: Any,
        on_save: Optional[Callable[[Memo], None]] = None,
        on_delete: Optional[Callable[[str], None]] = None,
        on_close: Optional[Callable[[], None]] = None,
        config: Any = None,
    ):
        super().__init__(master, bg=theme.CARD_BG, width=theme.DETAIL_W,
                         highlightthickness=0, bd=0)
        self.pack_propagate(False)
        self.grid_propagate(False)
        self._on_save = on_save
        self._on_delete = on_delete
        self._on_close = on_close
        self._config = config
        self._current = None  # type: Optional[Memo]
        self._logs = []  # type: List[ProgressLog]
        self._calendar = None  # type: Optional[CalendarPopup]
        self._save_job = None  # type: Any
        self._suspend = False  # 载入/重置表单时暂停自动保存
        self._dirty = False
        self._build()

    # ------------------------------------------------------------------
    # 构建界面
    # ------------------------------------------------------------------
    def _build(self) -> None:
        """创建全部控件：底部按钮 → 顶部标题 → 左右两栏。"""
        # 底部按钮先占位，保证在 pack 布局里一定可见（改动自动保存，无「保存」按钮）
        buttons = tk.Frame(self, bg=theme.CARD_BG)
        buttons.pack(fill="x", side="bottom", padx=14, pady=(6, 14))
        tk.Button(buttons, text="删除", font=theme.FONT_LABEL, bg=theme.HEADER_BG,
                  fg=theme.DANGER, relief="flat", cursor="hand2", width=8,
                  command=self._click_delete).pack(side="left", padx=8)
        tk.Button(buttons, text="关闭", font=theme.FONT_LABEL, bg=theme.HEADER_BG,
                  fg=theme.TEXT_DARK, relief="flat", cursor="hand2", width=8,
                  command=self._click_close).pack(side="right")

        head = tk.Frame(self, bg=theme.CARD_BG)
        head.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(head, text="任务详情", font=theme.FONT_TITLE,
                 bg=theme.CARD_BG, fg=theme.TEXT_DARK).pack(side="left")
        tk.Button(head, text="×", width=3, font=theme.FONT_BODY_BOLD,
                  bg=theme.HEADER_BG, fg=theme.TEXT_DARK, relief="flat",
                  cursor="hand2", command=self._click_close).pack(side="right")
        self.head = head

        # 属性条：紧挨「任务详情」大标题右侧，横向一字排开
        side = tk.Frame(head, bg=theme.CARD_BG)
        side.pack(side="left", fill="x", expand=True, padx=(18, 10),
                  pady=(2, 0))
        self.side = side

        self.var_created = tk.StringVar(value="")
        tk.Label(self, textvariable=self.var_created, font=theme.FONT_SMALL,
                 bg=theme.CARD_BG, fg=theme.TEXT_MUTED, anchor="w",
                 justify="left").pack(fill="x", padx=14, pady=(0, 8))

        body = tk.Frame(self, bg=theme.CARD_BG)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 2))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.body = body

        main = tk.Frame(body, bg=theme.CARD_BG)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)

        # 表单变量（保持变量名不变，供主窗口与回归测试使用）
        self.var_title = tk.StringVar(value="")
        self.var_due = tk.StringVar(value="")  # 完整时间戳（数据层唯一真值）
        self.var_due_date = tk.StringVar(value="")  # YYYY-MM-DD 展示字段
        self.var_due_time = tk.StringVar(value="")  # HH:MM 展示字段（如 15:30）
        self.var_urgency = tk.StringVar(value=URGENCY_LEVELS[1])
        self.var_status = tk.StringVar(value=STATUS_LEVELS[0])
        self.var_progress = tk.IntVar(value=0)
        self.var_log = tk.StringVar(value="")

        # 任一属性变更 → 防抖自动保存（写入表单时不触发，见 _suspend）
        for variable in (self.var_title, self.var_due, self.var_urgency,
                         self.var_status, self.var_progress):
            variable.trace_add("write", self._on_field_changed)
        # 日期 / 时间字段改动 → 合成完整 due → 由 var_due 的 trace 触发保存
        for variable in (self.var_due_date, self.var_due_time):
            variable.trace_add("write", self._on_due_part_changed)

        self._build_main(main)
        self._build_side(side)

    def _build_main(self, main: Any) -> None:
        """左栏：标题 / 历史进展（倒序，占满备注区空间）/ 写进展输入。"""
        self._field_label(main, "标题", 0)
        tk.Entry(main, textvariable=self.var_title, font=theme.FONT_BODY,
                 bg=theme.INPUT_BG, fg=theme.TEXT_DARK, relief="flat",
                 highlightthickness=1, highlightbackground=theme.INPUT_BORDER,
                 highlightcolor=theme.ACCENT).grid(row=1, column=0, sticky="ew",
                                                   pady=(0, 6))

        history = tk.Frame(main, bg=theme.INPUT_BG, highlightthickness=1,
                           highlightbackground=theme.INPUT_BORDER, bd=0)
        history.grid(row=2, column=0, sticky="nsew", pady=(6, 6))
        history.columnconfigure(0, weight=1)
        history.rowconfigure(0, weight=1)
        self.txt_logs = tk.Text(history, height=6, wrap="word",
                                font=theme.FONT_SMALL, bg=theme.INPUT_BG,
                                fg=theme.TEXT_DARK, relief="flat",
                                highlightthickness=0, bd=0)
        self.txt_logs.grid(row=0, column=0, sticky="nsew")
        self.txt_logs.tag_config("stamp", foreground=theme.TEXT_MUTED)
        self.txt_logs.tag_config("body", foreground=theme.TEXT_DARK)
        scroll = tk.Scrollbar(history, orient="vertical",
                              command=self.txt_logs.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.txt_logs.configure(yscrollcommand=scroll.set, state="disabled")
        main.rowconfigure(2, weight=1)

        log_row = tk.Frame(main, bg=theme.CARD_BG)
        log_row.grid(row=3, column=0, sticky="ew")
        log_row.columnconfigure(0, weight=1)
        self.entry_log = tk.Entry(log_row, textvariable=self.var_log,
                                  font=theme.FONT_SMALL, bg=theme.INPUT_BG,
                                  fg=theme.TEXT_DARK, relief="flat",
                                  highlightthickness=1,
                                  highlightbackground=theme.INPUT_BORDER,
                                  highlightcolor=theme.ACCENT)
        self.entry_log.grid(row=0, column=0, sticky="ew")
        self.entry_log.bind("<Return>", self._on_log_return)
        self.btn_append = tk.Button(log_row, text="追加", font=theme.FONT_SMALL,
                                    bg=theme.HEADER_BG, fg=theme.TEXT_DARK,
                                    relief="flat", cursor="hand2",
                                    command=self._append_log)
        self.btn_append.grid(row=0, column=1, padx=(6, 0))

    def _build_side(self, side: Any) -> None:
        """「任务详情」标题右侧的属性条。

        四个属性横向一字排开（列 1-4），列 0 留空做弹性间隔，
        把属性推到标题右侧；每列上面是字段名标签、下面是选择控件
        （输入框 + 日历按钮 / 下拉框 / 步进框）。
        """
        side.columnconfigure(0, weight=1)  # 弹性空白，把属性推向右侧
        for col in (1, 2, 3, 4):
            side.columnconfigure(col, weight=0)

        self._side_label(side, "预期完成时间", 1)
        due_cell = tk.Frame(side, bg=theme.CARD_BG)
        due_cell.grid(row=1, column=1, sticky="w", padx=(0, 10))
        due_cell.columnconfigure(0, weight=1)
        self.entry_due = tk.Entry(due_cell, textvariable=self.var_due_date,
                                  width=11,
                                  font=theme.FONT_TINY_PLAIN, bg=theme.INPUT_BG,
                                  fg=theme.TEXT_DARK, relief="flat",
                                  highlightthickness=1,
                                  highlightbackground=theme.INPUT_BORDER,
                                  highlightcolor=theme.ACCENT)
        self.entry_due.grid(row=0, column=0, sticky="ew")
        # 时间单独一格，只填具体时间（如 15:30），无需写完整时间戳
        tk.Label(due_cell, text=":", font=theme.FONT_TINY_PLAIN,
                 bg=theme.CARD_BG, fg=theme.TEXT_MUTED).grid(row=0, column=1)
        self.entry_due_time = tk.Entry(due_cell, textvariable=self.var_due_time,
                                       width=6,
                                       font=theme.FONT_TINY_PLAIN,
                                       bg=theme.INPUT_BG, fg=theme.TEXT_DARK,
                                       relief="flat", highlightthickness=1,
                                       highlightbackground=theme.INPUT_BORDER,
                                       highlightcolor=theme.ACCENT)
        self.entry_due_time.grid(row=0, column=2, padx=(2, 0))
        self.btn_due = tk.Button(due_cell, text="日历", width=4,
                                 font=theme.FONT_TINY_PLAIN, bg=theme.ACCENT,
                                 fg=theme.TEXT_LIGHT, relief="flat",
                                 cursor="hand2", command=self._open_calendar)
        self.btn_due.grid(row=0, column=3, padx=(3, 0))

        self._side_label(side, "紧急程度", 2)
        self.cmb_urgency = ttk.Combobox(side, textvariable=self.var_urgency,
                                        values=list(URGENCY_LEVELS),
                                        state="readonly", width=6,
                                        font=theme.FONT_TINY_PLAIN)
        self.cmb_urgency.grid(row=1, column=2, sticky="w", padx=(0, 10))

        self._side_label(side, "状态", 3)
        self.cmb_status = ttk.Combobox(side, textvariable=self.var_status,
                                       values=list(STATUS_LEVELS),
                                       state="readonly", width=6,
                                       font=theme.FONT_TINY_PLAIN)
        self.cmb_status.grid(row=1, column=3, sticky="w", padx=(0, 10))

        self._side_label(side, "进度", 4)
        progress_cell = tk.Frame(side, bg=theme.CARD_BG)
        progress_cell.grid(row=1, column=4, sticky="w")
        self.spin_progress = tk.Spinbox(progress_cell, from_=0, to=100,
                                        increment=5,
                                        textvariable=self.var_progress, width=4,
                                        font=theme.FONT_TINY_PLAIN,
                                        bg=theme.INPUT_BG, fg=theme.TEXT_DARK,
                                        relief="flat", highlightthickness=1,
                                        highlightbackground=theme.INPUT_BORDER)
        self.spin_progress.pack(side="left")
        tk.Label(progress_cell, text="%", font=theme.FONT_TINY_PLAIN,
                 bg=theme.CARD_BG, fg=theme.TEXT_MUTED).pack(
            side="left", padx=(4, 0))

    @staticmethod
    def _side_label(parent: Any, text: str, column: int) -> None:
        """属性条字段名（横向一字排开：标签在上、控件在下）。"""
        tk.Label(parent, text=text, font=theme.FONT_LABEL, bg=theme.CARD_BG,
                 fg=theme.TEXT_MUTED, anchor="w").grid(
            row=0, column=column, sticky="w", pady=(0, 3))

    @staticmethod
    def _field_label(parent: Any, text: str, row: int) -> None:
        """左栏字段名。"""
        tk.Label(parent, text=text, font=theme.FONT_LABEL, bg=theme.CARD_BG,
                 fg=theme.TEXT_MUTED, anchor="w").grid(
            row=row, column=0, sticky="w", pady=(2, 3)
        )

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def show(self, memo: Memo) -> None:
        """载入事务数据并在右侧展开面板（切换前先冲刷未保存改动）。"""
        self._save_now()
        self._cancel_save()
        self._close_calendar()
        self._suspend = True
        try:
            self._current = memo
            self._logs = [ProgressLog(text=log.text, time=log.time) for log in memo.logs]
            self.var_title.set(memo.title)
            self._set_due_text(memo.due_at)
            self.var_urgency.set(
                memo.urgency if memo.urgency in URGENCY_LEVELS else URGENCY_LEVELS[1])
            self.var_status.set(
                memo.status if memo.status in STATUS_LEVELS else STATUS_LEVELS[0])
            self.var_progress.set(memo.progress)
            self.var_log.set("")
            self.var_created.set(memo.created_at)
            self._render_logs()
        finally:
            self._suspend = False
        self._dirty = False
        self.grid(row=0, column=1, sticky="ns", padx=(6, 10), pady=4)

    def hide(self) -> None:
        """收起面板（保留 grid 配置以便再次展开）；先冲刷未保存改动。"""
        self._save_now()
        self._close_calendar()
        self.grid_remove()

    def is_visible(self) -> bool:
        """面板当前是否已显示。"""
        return bool(self.winfo_ismapped())

    def clear(self) -> None:
        """清空当前编辑状态（放弃尚未落盘的改动）。"""
        self._cancel_save()
        self._close_calendar()
        self._current = None
        self._logs = []
        self._dirty = False
        self._render_logs()

    def collect(self) -> Optional[Memo]:
        """从表单构造 Memo；输入非法时返回 None 并弹出提示。"""
        if self._current is None:
            return None
        due = self.var_due.get().strip()
        if not is_valid_time_text(due):
            messagebox.showwarning(
                "输入有误",
                "预期完成时间格式不正确。\n请使用 %s / YYYY-MM-DD，或留空。" % TIME_FORMAT,
            )
            return None
        progress = self._read_progress()
        return Memo(
            id=self._current.id,
            title=self.var_title.get().strip() or "未命名事务",
            content=self._current.content,
            created_at=self._current.created_at,
            due_at=due,
            urgency=self.var_urgency.get(),
            status=self.var_status.get(),
            progress=progress,
            logs=[log.to_dict() for log in self._logs],
        )

    # ------------------------------------------------------------------
    # 内部逻辑
    # ------------------------------------------------------------------
    def _read_progress(self) -> int:
        """安全读取进度 Spinbox（用户清空输入时不抛异常）。"""
        try:
            return normalize_progress(self.var_progress.get())
        except (tk.TclError, ValueError):
            return 0

    def _display_logs(self) -> List[ProgressLog]:
        """返回用于展示的记录：按时间戳倒序（最近在最前）。

        无法解析时间的历史数据排在最后；同一时刻的记录按写入先后倒序，
        保证刚追加的一条一定显示在最上面。内部 self._logs 仍保持写入顺序，
        保存时不会打乱原始数据。
        """
        indexed = list(enumerate(self._logs))

        def sort_key(pair: Tuple[int, ProgressLog]):
            index, log = pair
            parsed = parse_time(log.time)
            valid = 1 if parsed is not None else 0
            return (valid, parsed or datetime.min, index)

        return [log for _, log in sorted(indexed, key=sort_key, reverse=True)]

    def _render_logs(self) -> None:
        """渲染历史进展（最近更新在最前）。

        每行格式为「YYYY-MM-DD HH:MM:SS：进展内容」，时间戳用灰色弱化
        显示。无法解析时间的旧数据排在最后；同一时刻按写入先后倒序，
        保证刚追加的一条一定显示在最上面。内部 self._logs 保持写入顺序，
        保存时不会打乱原始数据。
        """
        self.txt_logs.configure(state="normal")
        self.txt_logs.delete("1.0", "end")
        for log in self._display_logs():
            self.txt_logs.insert("end", "%s：" % log.time, "stamp")
            self.txt_logs.insert("end", "%s\n" % log.text, "body")
        self.txt_logs.configure(state="disabled")
        self.txt_logs.see("1.0")

    def _append_log(self) -> None:
        """追加一条带系统时间戳的进展记录（输入框回车 / 「追加」按钮共用）。"""
        text = self.var_log.get().strip()
        if not text:
            return
        progress = self._read_progress()
        entry = "[进度 %d%%] %s" % (progress, text)
        self._logs.append(ProgressLog(text=entry, time=now_str()))
        self.var_log.set("")
        self._render_logs()
        self._dirty = True
        self._schedule_save()
        try:
            self.entry_log.focus_set()
        except tk.TclError:
            pass

    def _on_log_return(self, event: Any = None) -> str:
        """写进展输入框的回车事件：立即记录，不插入换行。"""
        self._append_log()
        return "break"

    # ------------------------------------------------------------------
    # 日历选择
    # ------------------------------------------------------------------
    def _set_due_text(self, text: str) -> None:
        """同步完整 due 与日期 / HH:MM 两个展示字段（不触发自动保存）。"""
        was_suspended = self._suspend
        self._suspend = True
        try:
            self.var_due.set(text or "")
            day, clock = split_due_text(text or "")
            self.var_due_date.set(day)
            self.var_due_time.set(clock)
        finally:
            self._suspend = was_suspended

    def _on_due_part_changed(self, *args: Any) -> None:
        """日期或时间框改动 → 合成完整 due 文本，交由 var_due 触发保存。"""
        if self._suspend:
            return
        joined = join_due_text(self.var_due_date.get(), self.var_due_time.get())
        if joined != self.var_due.get():
            self.var_due.set(joined)

    def _open_calendar(self) -> None:
        """点击「日历」按钮：在按钮旁、主窗口范围内弹出月历。"""
        self._close_calendar()
        try:
            self._calendar = CalendarPopup(
                self.winfo_toplevel(),
                initial_text=self.var_due.get(),
                on_pick=self._on_date_picked,
                on_clear=self._on_date_cleared,
                anchor=self.btn_due,
                host=self.winfo_toplevel(),
                # 时间框可能已填好但日期为空（此时 var_due 仍是空串），
                # 单独传进去，选完日期后才不会把已填时间冲掉。
                initial_time=self.var_due_time.get(),
            )
        except tk.TclError:
            self._calendar = None

    def _on_date_picked(self, text: str) -> None:
        """月历选中日期后写回预期完成时间（时间框保持 HH:MM）。"""
        self._set_due_text(text)
        self._on_field_changed()
        self._calendar = None

    def _on_date_cleared(self) -> None:
        """月历点「清除」：留空表示未设置。"""
        self._set_due_text("")
        self._on_field_changed()
        self._calendar = None

    def _close_calendar(self) -> None:
        """关闭遗留的日历弹窗（切换事务 / 收起面板时调用）。"""
        popup = self._calendar
        self._calendar = None
        if popup is None:
            return
        try:
            popup._close()
        except (tk.TclError, AttributeError):
            pass

    # ------------------------------------------------------------------
    # 自动保存
    # ------------------------------------------------------------------
    def _on_field_changed(self, *args: Any) -> None:
        """标题 / 属性字段变更：标记脏数据并安排防抖保存。"""
        if self._suspend:
            return
        self._dirty = True
        self._schedule_save()

    def _schedule_save(self) -> None:
        """防抖：连续输入合并为一次写盘。"""
        self._cancel_save()
        try:
            self._save_job = self.after(AUTO_SAVE_DELAY_MS, self._save_now)
        except tk.TclError:
            self._save_job = None

    def _cancel_save(self) -> None:
        """取消尚未触发的自动保存。"""
        job = self._save_job
        self._save_job = None
        if job is None:
            return
        try:
            self.after_cancel(job)
        except tk.TclError:
            pass

    def _save_now(self, warn: bool = False) -> bool:
        """立即写盘；无改动 / 表单非法时跳过，返回是否真正保存。"""
        self._cancel_save()
        if self._suspend or not self._dirty or self._current is None:
            return False
        due = self.var_due.get().strip()
        if not is_valid_time_text(due):
            # 自动保存期间静默跳过非法时间；用户继续编辑后仍会再次触发
            if warn:
                messagebox.showwarning(
                    "输入有误",
                    "预期完成时间格式不正确。\n请使用 %s / YYYY-MM-DD，或留空。" % TIME_FORMAT,
                )
            return False
        memo = self.collect()
        if memo is None or self._on_save is None:
            return False
        self._on_save(memo)
        self._dirty = False
        return True

    # ------------------------------------------------------------------
    # 按钮
    # ------------------------------------------------------------------
    def _click_save(self) -> None:
        """兼容旧调用：立即保存（界面已移除「保存」按钮）。"""
        self._save_now(warn=True)

    def _click_delete(self) -> None:
        """删除按钮（二次确认后回调）。"""
        if self._current is None:
            return
        if not messagebox.askyesno("确认删除", "确定要删除这条事务吗？此操作不可撤销。"):
            return
        if self._on_delete is not None:
            self._on_delete(self._current.id)

    def _click_close(self) -> None:
        """关闭按钮：由主窗口负责收起面板并恢复窗口宽度。"""
        if self._on_close is not None:
            self._on_close()
        else:
            self.hide()
