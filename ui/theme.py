# -*- coding: utf-8 -*-
"""浅色主题与 ttk 样式。

与同作者的 ffmpeg-gui 观感保持一致：同一套配色、同样的自绘勾选框
（clam 主题默认把「已选中」画成叉号 ✗，反直觉，这里一并换掉）。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BG = "#f3f4f6"
PANEL = "#ffffff"
SUB = "#eef0f3"
BORDER = "#d8dbe0"
TEXT = "#1f2329"
MUTED = "#6b7280"
ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
OK = "#15803d"
WARN = "#b45309"
ERR = "#b91c1c"
LOG_BG = "#fbfbfc"

FONT = "Microsoft YaHei UI"
MONO = "Consolas"

# 重复分组的底色（循环使用），配合「标记」列区分不同组
GROUP_COLORS = ["#fff3cd", "#e2f0ff", "#e6f6e6", "#fde2e4", "#efe0ff", "#e0f2f1"]
CONFLICT_BG = "#f8d7da"
MATCH_BG = "#d1e7dd"          # 参考值完全一致
PARTIAL_BG = "#ffe9b3"        # 参考值前缀 / 包含命中

_check_images: dict = {}
_CHECK_SIZE = 14
_TICK_PATTERN = [
    "..............",
    "..............",
    "..............",
    "..........XX..",
    ".........XX...",
    "........XX....",
    "..X....XX.....",
    "..XX..XX......",
    "...XXXXX......",
    "....XXX.......",
    "....XX........",
    "..............",
    "..............",
    "..............",
]


def _check_image(fill: str, border: str, tick: str | None = None) -> tk.PhotoImage:
    img = tk.PhotoImage(width=_CHECK_SIZE, height=_CHECK_SIZE)
    img.put("#ffffff", to=(0, 0, _CHECK_SIZE, _CHECK_SIZE))
    if fill != "#ffffff":
        img.put(fill, to=(1, 1, _CHECK_SIZE - 1, _CHECK_SIZE - 1))
    img.put(border, to=(0, 0, _CHECK_SIZE, 1))
    img.put(border, to=(0, _CHECK_SIZE - 1, _CHECK_SIZE, _CHECK_SIZE))
    img.put(border, to=(0, 0, 1, _CHECK_SIZE))
    img.put(border, to=(_CHECK_SIZE - 1, 0, _CHECK_SIZE, _CHECK_SIZE))
    if tick:
        for row, line in enumerate(_TICK_PATTERN):
            for col, ch in enumerate(line):
                if ch == "X":
                    img.put(tick, to=(col, row, col + 1, row + 1))
    return img


def _install_check_indicator(style: ttk.Style):
    """用自绘图标替换 Checkbutton 的指示器；失败时安静回退到主题默认。"""
    try:
        _check_images["off"] = _check_image("#ffffff", "#9aa3ad")
        _check_images["on"] = _check_image(ACCENT, ACCENT, "#ffffff")
        _check_images["disabled"] = _check_image("#f3f4f6", "#c9cdd3")
        style.element_create(
            "Hgui.Check.indicator", "image",
            _check_images["off"],
            ("selected", _check_images["on"]),
            ("disabled", _check_images["disabled"]),
            border=0, padding=(0, 0, 6, 0), sticky="")
    except tk.TclError:
        return
    layout = [
        ("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Hgui.Check.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})]}),
        ]}),
    ]
    try:
        style.layout("TCheckbutton", layout)
    except tk.TclError:
        pass


def setup_theme(root: tk.Tk, ui_scale: float = 1.0):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    def font(size=10, bold=False):
        return (FONT, size, "bold") if bold else (FONT, size)

    root.configure(bg=BG)
    style.configure(".", background=BG, foreground=TEXT, font=font())
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL, relief="flat")
    style.configure("Card.TFrame", background=PANEL, relief="solid", borderwidth=1)

    style.configure("TLabel", background=BG, foreground=TEXT, font=font())
    style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=font(9))
    style.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED, font=font(9))
    style.configure("Title.TLabel", background=BG, foreground=TEXT, font=font(13, True))
    style.configure("Section.TLabel", background=PANEL, foreground=ACCENT, font=font(10, True))
    style.configure("Ok.TLabel", background=PANEL, foreground=OK, font=font(10, True))
    style.configure("Err.TLabel", background=PANEL, foreground=ERR, font=font(10, True))

    style.configure("TButton", background=SUB, foreground=TEXT, font=font(),
                    borderwidth=1, relief="flat", padding=(10, 5), focusthickness=0)
    style.map("TButton", background=[("pressed", "#dfe3e8"), ("active", "#e4e7eb")],
              foreground=[("disabled", "#a0a4aa")])
    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                    font=font(10, True), padding=(16, 6), borderwidth=0)
    style.map("Accent.TButton", background=[("pressed", ACCENT_HOVER), ("active", ACCENT_HOVER),
                                            ("disabled", "#9db4e8")],
              foreground=[("disabled", "#eef2ff")])
    style.configure("Danger.TButton", background="#fee2e2", foreground=ERR,
                    padding=(12, 6), borderwidth=1)
    style.map("Danger.TButton", background=[("pressed", "#fecaca"), ("active", "#fecaca")])
    style.configure("Ghost.TButton", background=PANEL, foreground=MUTED,
                    borderwidth=0, padding=(6, 2))
    style.map("Ghost.TButton", background=[("active", SUB)], foreground=[("active", ACCENT)])

    style.configure("TEntry", fieldbackground="#ffffff", foreground=TEXT,
                    bordercolor=BORDER, insertcolor=TEXT, padding=(6, 4), relief="flat")
    style.map("TEntry", bordercolor=[("focus", ACCENT)])
    style.configure("TCombobox", fieldbackground="#ffffff", background=SUB,
                    foreground=TEXT, bordercolor=BORDER, arrowcolor=MUTED,
                    padding=(6, 3), relief="flat")
    style.map("TCombobox", fieldbackground=[("readonly", "#ffffff")],
              bordercolor=[("focus", ACCENT)])
    style.configure("TCheckbutton", background=PANEL, foreground=TEXT, font=font(),
                    focusthickness=0)
    style.map("TCheckbutton", background=[("active", PANEL)],
              foreground=[("disabled", "#a0a4aa")])
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TSeparator", background=BORDER)
    style.configure("Vertical.TScrollbar", background=SUB, troughcolor=BG,
                    bordercolor=BG, arrowcolor=MUTED, relief="flat")
    style.configure("Horizontal.TScrollbar", background=SUB, troughcolor=BG,
                    bordercolor=BG, arrowcolor=MUTED, relief="flat")
    style.configure("Thin.Horizontal.TProgressbar", background=ACCENT,
                    troughcolor="#e3e6ea", lightcolor=ACCENT, darkcolor=ACCENT, thickness=10)

    # Treeview：哈希要逐字符比对，所以整表用等宽字体；重复分组靠底色区分
    style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                    bordercolor=BORDER, rowheight=int(24 * ui_scale), font=(MONO, 9))
    style.configure("Treeview.Heading", background=SUB, foreground=MUTED, font=font(9, True),
                    relief="flat", padding=(6, 5))
    style.map("Treeview", background=[("selected", "#cfe0fb")],
              foreground=[("selected", TEXT)])
    style.map("Treeview.Heading", background=[("active", "#e4e7eb")])

    _install_check_indicator(style)
    return style
