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
PAD = 18
ROW_HEIGHT = 58
CARD_RADIUS = 22
# 顶栏（备忘录/搜索/排序/两个勾选/新建/导入/设置/最小化/关闭）在 100% DPI 下
# 自然宽度约 1076px；默认窗口需留出余量，否则右侧按钮会被挤出裁切。
WIN_LIST_W = 1120
WIN_H = 560
# 列表与详情之间有一条可拖拽分隔条；展开详情时列表区宽度保持 LIST_W 不变，
# 窗口只加宽 分隔条 + 详情面板 的宽度，避免挤压任务列表。
SPLIT_W = 6
LIST_W = WIN_LIST_W - 2 * PAD  # 1084
MIN_LIST_W = 420
MIN_DETAIL_W = 360
# 收起详情后窗口跟着收窄到的最小宽度。顶栏自然宽约 791px，加两侧 PAD
# 需约 827px；低于此值 tkinter pack 会把最右的「×」挤出窗口被裁掉。
MIN_COLLAPSED_W = 840
# 详情面板给足宽度，属性条（紧急程度/状态/进度）与历史进展才不挤。
DETAIL_PANEL_W = 600
DETAIL_W = SPLIT_W + DETAIL_PANEL_W  # 展开时窗口增加的总宽度
WIN_DETAIL_W = WIN_LIST_W + DETAIL_W
MIN_WINDOW_W = 480  # 边缘拖拽调整大小时的宽度下限
MIN_WINDOW_H = 320  # 边缘拖拽调整大小时的高度下限
RESIZE_BAND = 8  # 窗口边缘热区宽度（像素），进入该范围光标变为调整形状
# 四个角落被 CARD_RADIUS 的圆角透明区挖空，透明色像素不接收鼠标事件。
# 角落热区必须延伸到圆角内侧的实体像素上才能点得到；取与圆角同宽，
# 既能覆盖视觉上的角，又不会碰到顶栏最右侧距边约 22px 的按钮。
CORNER_RESIZE_BAND = CARD_RADIUS
