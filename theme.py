# -*- coding: utf-8 -*-
"""界面主题常量（浅色卡片风格）。

独立成模块以避免 ui_main 与 ui_detail 之间产生循环 import。
本文件只有常量，不含任何平台相关代码。
"""
from __future__ import annotations

# 颜色
CARD_BG = "#FAFBFD"
CARD_BORDER = "#D6DEEA"
TRANSPARENT_KEY = "#010203"  # 用作 -transparentcolor 的键色，露出四角形成圆角卡片
TEXT_DARK = "#2B3A4A"
TEXT_MUTED = "#7B8A9C"
TEXT_LIGHT = "#FFFFFF"
ACCENT = "#4C7CF3"
HEADER_BG = "#EEF3FC"
DIVIDER = "#E3E9F2"
SELECT_BG = "#E8F0FF"
INPUT_BG = "#FFFFFF"
INPUT_BORDER = "#C9D6E8"
DANGER = "#C62828"
OVERDUE_TEXT = "#D32F2F"  # 列表中已逾期事务的「预期完成时间」文字颜色

# 字体（Windows 自带；找不到字族时 Tk 会自动回退）
FONT_FAMILY = "Microsoft YaHei UI"
FONT_TITLE = (FONT_FAMILY, 13, "bold")
FONT_LABEL = (FONT_FAMILY, 10)
FONT_BODY = (FONT_FAMILY, 11)
FONT_BODY_BOLD = (FONT_FAMILY, 11, "bold")
FONT_SMALL = (FONT_FAMILY, 9)
FONT_TINY = (FONT_FAMILY, 8, "bold")
FONT_TINY_PLAIN = (FONT_FAMILY, 8)  # 详情属性一字排开时用的小号常规字体

# 布局尺寸
ROW_HEIGHT = 58
CARD_RADIUS = 22
WIN_LIST_W = 780
WIN_DETAIL_W = 1280
WIN_H = 560
MIN_WINDOW_W = 480  # 边缘拖拽调整大小时的宽度下限
MIN_WINDOW_H = 320  # 边缘拖拽调整大小时的高度下限
RESIZE_BAND = 8  # 窗口边缘热区宽度（像素），进入该范围光标变为调整形状
# 详情面板内部分成「左侧编辑区 + 右侧属性区」，需要比原来更宽；
# 窗口展开时增加的宽度与面板宽度保持一致，避免列表被挤压。
DETAIL_W = 500
PAD = 18
