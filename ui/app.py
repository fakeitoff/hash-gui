# -*- coding: utf-8 -*-
"""7z 哈希对比工具 —— 主窗口。"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from core import compare, sevenzip
from core.hasher import Entry, HashRunner, stat_entry
from core.util import (APP_ROOT, human_size, human_time, normalize_hash, norm_key,
                       open_in_explorer)
from . import theme

SETTINGS_PATH = os.path.join(APP_ROOT, "settings.json")
MEDIA_HINT = "先添加文件，勾选算法，点「计算哈希」；相同的文件会被同色标出。"

# 表格固定列 + 每个被勾选算法一个动态列
BASE_COLUMNS = ("mark", "name", "size", "mtime", "ref")
COLUMN_SPEC = {
    "mark": ("标记", 132, "w"),
    "name": ("文件", 300, "w"),
    "size": ("大小", 92, "e"),
    "mtime": ("修改时间", 122, "w"),
    "ref": ("参考", 96, "center"),
}
# 参考值搜索的算法范围选项
REF_ALL = "全部算法"
REF_TIER = {"exact": ("✓ 相同", "match"), "prefix": ("◐ 前缀", "partial"),
            "contains": ("≈ 包含", "partial")}


def _enable_dpi_awareness():
    """必须在创建 Tk 窗口之前调用，否则高分屏下界面会被系统拉伸模糊。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _detect_ui_scale(root) -> float:
    try:
        return max(1.0, min(2.0, root.winfo_fpixels("1i") / 96.0))
    except Exception:
        return 1.0


def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data: dict):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
    except OSError:
        pass                            # 程序目录只读时不报错，只是记不住设置


class App(tk.Tk):
    def __init__(self, initial_paths=None):
        _enable_dpi_awareness()
        super().__init__()
        self.ui_scale = _detect_ui_scale(self)
        self.title("7z 哈希对比 —— SHA-256 / MD5 批量计算与查重")
        settings = load_settings()
        self.settings = settings
        width = int(1120 * self.ui_scale)
        height = int(720 * self.ui_scale)
        geometry = str(settings.get("geometry") or f"{width}x{height}")
        self.geometry(geometry)
        self.minsize(int(760 * self.ui_scale), int(480 * self.ui_scale))
        theme.setup_theme(self, self.ui_scale)

        # ---- 运行环境 ----
        self.sevenzip_path = ""
        self.sevenzip_version = ""
        self.startup_error = ""
        manual = (os.environ.get("HASHGUI_7Z", "").strip()
                  or str(settings.get("sevenzip") or "").strip())
        if manual and sevenzip.works(manual):
            self.sevenzip_path = manual
        else:
            try:
                self.sevenzip_path = sevenzip.locate()
            except sevenzip.SevenZipNotFound as exc:
                self.startup_error = str(exc)
        if self.sevenzip_path:
            self.sevenzip_version = sevenzip.version(self.sevenzip_path)

        # ---- 状态 ----
        self.entries: dict[str, Entry] = {}
        self.sort_state = ("name", True)
        self.runner: HashRunner | None = None
        self._queue: queue.Queue = queue.Queue()
        self._analysis_dirty = False
        self._last_dir = str(settings.get("last_dir") or "")

        self.algorithm_vars: dict[str, tk.BooleanVar] = {}
        self.algorithms = list(settings.get("algorithms") or ["SHA256"])
        self.ref_var = tk.StringVar(value="")
        self.ref_alg = tk.StringVar(value=str(settings.get("ref_alg") or REF_ALL))
        self.only_dupes = tk.BooleanVar(value=False)
        self.full_hash = tk.BooleanVar(value=bool(settings.get("full_hash")))
        # 只影响“显示/复制/导出”，内部一律按大写存，所以切换不会改变比对结果
        self.hash_case = tk.StringVar(value="lower" if str(
            settings.get("hash_case")) == "lower" else "upper")
        self.include_sub = tk.BooleanVar(value=bool(settings.get("include_sub", True)))
        self.status_text = tk.StringVar(value=MEDIA_HINT)
        self.summary_text = tk.StringVar(value="")
        self.percent_text = tk.StringVar(value="")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._closing = False
        self._drain_after = self.after(120, self._drain_queue)
        self._probe_after = self.after(60, self._probe_capabilities)

        if self.startup_error:
            self.log(self.startup_error, "error")
            self.btn_run.configure(state="disabled")
        else:
            self.log(f"已就绪 · {self.sevenzip_version} · {self.sevenzip_path}", "ok")

        if initial_paths:
            self.add_paths(initial_paths)

    # ==================================================================
    # 界面
    # ==================================================================
    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self._build_topbar()
        self._build_toolbar()
        self._build_table()
        self._build_analysis()
        self._build_bottom()

    def _build_topbar(self):
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 4))
        bar.columnconfigure(3, weight=1)
        ttk.Label(bar, text="7z 哈希对比", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.lbl_7z = ttk.Label(bar, text="", style="Muted.TLabel")
        self.lbl_7z.grid(row=0, column=1, sticky="w", padx=(14, 0))
        self._refresh_7z_label()
        ttk.Button(bar, text="指定 7z.exe…", style="Ghost.TButton",
                   command=self._pick_sevenzip).grid(row=0, column=2, padx=(8, 0))

    def _refresh_7z_label(self):
        if self.sevenzip_path:
            short = os.path.basename(self.sevenzip_path)
            self.lbl_7z.configure(text=f"● {self.sevenzip_version or short}", foreground=theme.OK)
            self.lbl_7z.bind("<Enter>", lambda e: self.lbl_7z.configure(text=self.sevenzip_path))
            self.lbl_7z.bind("<Leave>", lambda e: self._refresh_7z_label())
        else:
            self.lbl_7z.configure(text="● 未找到 7-Zip", foreground=theme.ERR)

    def _build_toolbar(self):
        card = ttk.Frame(self, style="Card.TFrame", padding=(12, 8))
        card.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))

        row1 = ttk.Frame(card, style="Panel.TFrame")
        row1.pack(fill="x")
        for text, cmd in (("添加文件…", self.pick_files),
                          ("添加文件夹…", self.pick_folder),
                          ("移除选中", self.remove_selected),
                          ("清空", self.clear_files)):
            ttk.Button(row1, text=text, command=cmd).pack(side="left", padx=(0, 6))
        ttk.Checkbutton(row1, text="含子文件夹", variable=self.include_sub).pack(
            side="left", padx=(2, 12))
        self.count_text = tk.StringVar(value="0 个文件")
        ttk.Label(row1, textvariable=self.count_text, style="PanelMuted.TLabel").pack(
            side="left", padx=(2, 0))

        row2 = ttk.Frame(card, style="Panel.TFrame")
        row2.pack(fill="x", pady=(8, 0))
        ttk.Label(row2, text="算法", style="Section.TLabel").pack(side="left", padx=(0, 8))
        self.algorithm_checks: dict[str, ttk.Checkbutton] = {}
        for name, label, _length in sevenzip.ALGORITHMS:
            var = tk.BooleanVar(value=name in self.algorithms)
            self.algorithm_vars[name] = var
            check = ttk.Checkbutton(row2, text=label, variable=var,
                                    command=self._on_algorithms_changed)
            check.pack(side="left", padx=(0, 10))
            self.algorithm_checks[name] = check
        self.btn_stop = ttk.Button(row2, text="停止", style="Danger.TButton",
                                   command=self.on_cancel, state="disabled")
        self.btn_stop.pack(side="right", padx=(0, 8))
        self.btn_run = ttk.Button(row2, text="▶  计算哈希", style="Accent.TButton",
                                  command=self.on_run)
        self.btn_run.pack(side="right")

    def _build_table(self):
        holder = ttk.Frame(self, style="Card.TFrame", padding=(6, 6))
        holder.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 6))
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(holder, columns=BASE_COLUMNS, show="headings",
                                 selectmode="extended")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.vsb = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview)
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.hsb = ttk.Scrollbar(holder, orient="horizontal", command=self.tree.xview)
        self.hsb.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)

        for color in theme.GROUP_COLORS:
            self.tree.tag_configure(f"grp{theme.GROUP_COLORS.index(color)}", background=color)
        self.tree.tag_configure("conflict", background=theme.CONFLICT_BG)
        self.tree.tag_configure("match", background=theme.MATCH_BG)
        self.tree.tag_configure("partial", background=theme.PARTIAL_BG)
        self.tree.tag_configure("error", foreground=theme.ERR)

        self.tree.bind("<Button-3>", self._popup_menu)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_verdict())
        self.tree.bind("<Double-1>", self._on_double_click)
        self._apply_columns()

    def _hash_width(self, algorithm: str) -> int:
        """哈希列宽：默认只显示前缀，勾选「完整」后按十六进制位数留够宽度。"""
        if not self.full_hash.get():
            return int(150 * self.ui_scale)
        length = sevenzip.ALG_HEX_LEN.get(algorithm, 64)
        return int(max(160, 7.2 * length) * self.ui_scale)

    def _show(self, value: str) -> str:
        """按「哈希大小写」设置渲染一个哈希值。

        内部统一存大写，比对也按大写走（``compare.reference_hit`` 两边都转大写），
        所以这个开关只改显示、复制和导出，不会改变任何匹配结果。
        """
        if not value:
            return ""
        return value.lower() if self.hash_case.get() == "lower" else value.upper()

    def _display_hash(self, entry, algorithm: str) -> str:
        value = entry.hash_of(algorithm) if entry else ""
        if not value:
            return ""
        shown = value if self.full_hash.get() else value[:16] + "…"
        return self._show(shown)

    def _on_case_toggled(self):
        self._rebuild_rows()

    def _on_full_hash_toggled(self):
        self._apply_columns()
        self._rebuild_rows()

    def _apply_columns(self):
        """按勾选的算法重建列（保留行数据）。"""
        algs = self.selected_algorithms()
        self.tree.configure(columns=list(BASE_COLUMNS) + list(algs))
        for key in BASE_COLUMNS:
            title, width, anchor = COLUMN_SPEC[key]
            self.tree.heading(key, text=title, command=lambda k=key: self._sort_by(k))
            self.tree.column(key, width=int(width * self.ui_scale), minwidth=60,
                             anchor=anchor, stretch=(key == "name"))
        for alg in algs:
            label = sevenzip.ALG_LABEL.get(alg, alg)
            self.tree.heading(alg, text=label, command=lambda a=alg: self._sort_by(a))
            self.tree.column(alg, width=self._hash_width(alg), minwidth=110,
                             anchor="w", stretch=False)

    def _build_analysis(self):
        card = ttk.Frame(self, style="Card.TFrame", padding=(12, 8))
        card.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 6))
        card.columnconfigure(1, weight=1)

        left = ttk.Frame(card, style="Panel.TFrame")
        left.grid(row=0, column=0, sticky="nw", padx=(0, 14))
        ttk.Checkbutton(left, text="只看重复文件", variable=self.only_dupes,
                        command=self._rebuild_rows).pack(anchor="w")
        ttk.Checkbutton(left, text="显示完整哈希", variable=self.full_hash,
                        command=self._on_full_hash_toggled).pack(anchor="w", pady=(2, 0))
        case_row = ttk.Frame(left, style="Panel.TFrame")
        case_row.pack(anchor="w", pady=(2, 0))
        ttk.Label(case_row, text="哈希大小写", style="PanelMuted.TLabel").pack(side="left")
        for value, text in (("upper", "大写"), ("lower", "小写")):
            ttk.Radiobutton(case_row, text=text, value=value, variable=self.hash_case,
                            command=self._on_case_toggled).pack(side="left", padx=(6, 0))
        ttk.Button(left, text="导出报告…", style="Ghost.TButton",
                   command=self._export).pack(anchor="w", pady=(6, 0))
        ttk.Button(left, text="复制选中哈希", style="Ghost.TButton",
                   command=self._copy_selected).pack(anchor="w", pady=(4, 0))

        right = ttk.Frame(card, style="Panel.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        ref_row = ttk.Frame(right, style="Panel.TFrame")
        ref_row.grid(row=0, column=0, sticky="ew")
        ref_row.columnconfigure(1, weight=1)
        ttk.Label(ref_row, text="参考值", style="Section.TLabel").grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(ref_row, textvariable=self.ref_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(ref_row, text="清空", width=5, style="Ghost.TButton",
                   command=lambda: self.ref_var.set("")).grid(row=0, column=2, padx=(6, 0))
        self.ref_cb = ttk.Combobox(ref_row, textvariable=self.ref_alg,
                                   values=self._ref_choices(), width=10, state="readonly")
        self.ref_cb.grid(row=0, column=3, padx=(8, 0))
        self.ref_var.trace_add("write", lambda *a: self._mark_dirty())
        self.ref_cb.bind("<<ComboboxSelected>>", lambda e: self._mark_dirty())
        ttk.Label(ref_row, text="支持部分匹配：粘贴完整值或任意 ≥4 位片段",
                  style="PanelMuted.TLabel").grid(row=1, column=1, sticky="w", pady=(2, 0))

        self.verdict = tk.Text(right, height=4, wrap="word", bg="#fbfbfc", fg=theme.TEXT,
                               relief="solid", borderwidth=1, font=(theme.FONT, 9),
                               padx=8, pady=6, state="disabled")
        self.verdict.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self.verdict.tag_configure("ok", foreground=theme.OK)
        self.verdict.tag_configure("bad", foreground=theme.ERR)
        ttk.Label(right, textvariable=self.summary_text, style="PanelMuted.TLabel").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

    def _build_bottom(self):
        bar = ttk.Frame(self, style="Card.TFrame", padding=(12, 8))
        bar.grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 10))
        bar.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(bar, style="Thin.Horizontal.TProgressbar",
                                        mode="determinate", maximum=100)
        self.progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(bar, textvariable=self.percent_text, width=8, anchor="e").grid(
            row=0, column=1, padx=(10, 0))
        ttk.Label(bar, textvariable=self.status_text, style="Muted.TLabel",
                  anchor="w").grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        self.log_text = tk.Text(bar, height=4, wrap="none", bg=theme.LOG_BG, fg=theme.MUTED,
                                relief="flat", borderwidth=0, font=(theme.MONO, 8),
                                padx=6, pady=4, state="disabled")
        self.log_text.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        for tag, color in (("info", "#3b4148"), ("cmd", theme.ACCENT), ("warn", theme.WARN),
                           ("error", theme.ERR), ("ok", theme.OK)):
            self.log_text.tag_configure(tag, foreground=color)

    # ==================================================================
    # 右键菜单
    # ==================================================================
    def _popup_menu(self, event):
        """右键菜单：整个生命周期只建一次，避免每次右键都泄漏一个 Menu 控件。"""
        if getattr(self, "_menu", None) is None:
            self._menu = tk.Menu(self, tearoff=0)
            self._menu.add_command(label="复制哈希值", command=self._copy_selected)
            self._menu.add_command(label="复制「文件名 + 哈希」", command=self._copy_row_with_name)
            self._menu.add_command(label="在资源管理器中显示", command=self._reveal_row)
            self._menu.add_command(label="只重算这一行", command=self._recompute_row)
            self._menu.add_separator()
            self._menu.add_command(label="添加文件…", command=self.pick_files)
            self._menu.add_command(label="移除选中", command=self.remove_selected)
            self._menu.add_command(label="清空列表", command=self.clear_files)
        iid = self.tree.identify_row(event.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        has_row = bool(iid)
        for index in range(4):            # 前四项要有选中行才可用
            self._menu.entryconfigure(index, state="normal" if has_row else "disabled")
        self._menu.tk_popup(event.x_root, event.y_root)

    def _first_selected(self) -> str:
        selection = self.tree.selection()
        return selection[0] if selection else ""

    def _reveal_row(self):
        key = self._first_selected()
        if key and os.path.exists(key):
            open_in_explorer(key)

    def _recompute_row(self):
        keys = list(self.tree.selection())
        if keys:
            self._recompute(keys)

    def _copy_row_with_name(self):
        key = self._first_selected()
        if key:
            self._copy_with_names(self.entries.get(key))

    # ==================================================================
    # 文件管理
    # ==================================================================
    def pick_files(self):
        files = filedialog.askopenfilenames(parent=self, title="选择要计算哈希的文件",
                                            initialdir=self._last_dir or None)
        if files:
            self._last_dir = os.path.dirname(files[0])
            self.add_files(files)

    def pick_folder(self):
        folder = filedialog.askdirectory(parent=self, title="选择文件夹",
                                         initialdir=self._last_dir or None)
        if not folder:
            return
        self._last_dir = folder
        files = self._walk(folder, self.include_sub.get())
        if not files:
            messagebox.showinfo("提示", "该文件夹里没有文件。")
            return
        if len(files) > 5000 and not messagebox.askyesno(
                "文件很多", f"共找到 {len(files)} 个文件，全部加入列表？"):
            return
        self.add_files(files)

    @staticmethod
    def _walk(folder: str, recursive: bool) -> list[str]:
        out: list[str] = []
        if recursive:
            for root, dirs, names in os.walk(folder):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                out += [os.path.join(root, n) for n in names]
        else:
            try:
                out = [os.path.join(folder, n) for n in os.listdir(folder)
                       if os.path.isfile(os.path.join(folder, n))]
            except OSError:
                pass
        return out

    def add_files(self, paths):
        added = 0
        for path in paths:
            key = norm_key(path)
            if key in self.entries:
                continue
            self.entries[key] = stat_entry(key)
            added += 1
        if added:
            self._rebuild_rows()
            self.log(f"已添加 {added} 个文件，共 {len(self.entries)} 个。", "info")
        self._update_verdict()

    def add_paths(self, paths):
        """把命令行 / 「发送到」给来的路径加进列表：目录按「含子文件夹」设置展开。

        不存在的项也保留成一行（``stat_entry`` 会带上原因），静默丢弃会让人
        以为工具没收到那个文件。
        """
        files: list[str] = []
        folders = 0
        for path in paths:
            if not path:
                continue
            if os.path.isdir(path):
                folders += 1
                files += self._walk(path, self.include_sub.get())
            else:
                files.append(path)
        if folders:
            self.log(f"展开 {folders} 个文件夹（{'含' if self.include_sub.get() else '不含'}子文件夹）"
                     f"，共 {len(files)} 个文件。", "info")
        self.add_files(files)

    def remove_selected(self):
        keys = list(self.tree.selection())
        for key in keys:
            self.entries.pop(key, None)
        if keys:
            self._rebuild_rows()

    def clear_files(self):
        self.entries.clear()
        self._rebuild_rows()
        self.status_text.set("列表已清空")

    # ==================================================================
    # 算法选择
    # ==================================================================
    def selected_algorithms(self) -> list[str]:
        return [name for name, _label, _n in sevenzip.ALGORITHMS
                if self.algorithm_vars.get(name) and self.algorithm_vars[name].get()]

    def _ref_choices(self) -> list[str]:
        """参考值搜索的算法范围选项：已勾选的算法 + 「全部算法」。"""
        return [sevenzip.ALG_LABEL.get(a, a) for a in self.selected_algorithms()] + [REF_ALL]

    def _ref_algorithms(self) -> list[str]:
        """把下拉框的选择换算成实际要搜的算法名列表。"""
        choice = self.ref_alg.get()
        if not choice or choice == REF_ALL:
            return self.selected_algorithms()
        for name, label, _n in sevenzip.ALGORITHMS:
            if choice in (name, label):
                return [name]
        return self.selected_algorithms()[:1]

    def _on_algorithms_changed(self):
        chosen = self.selected_algorithms()
        if not chosen:
            self.algorithm_vars["SHA256"].set(True)      # 至少留一个
            chosen = ["SHA256"]
        self.algorithms = chosen
        self._apply_columns()
        self._rebuild_rows()
        choices = self._ref_choices()
        self.ref_cb.configure(values=choices)
        if self.ref_alg.get() not in choices:
            self.ref_alg.set(REF_ALL)

    def _probe_capabilities(self):
        """后台探测本机 7z 支持哪些算法，把不支持的灰掉。"""
        if not self.sevenzip_path:
            return
        sample = os.path.join(APP_ROOT, "main.py")

        def work():
            supported = sevenzip.supported_algorithms(self.sevenzip_path, sample)
            self._queue.put(("caps", supported))

        threading.Thread(target=work, daemon=True).start()

    # ==================================================================
    # 计算
    # ==================================================================
    def on_run(self):
        if self._busy():
            return
        if not self.sevenzip_path:
            messagebox.showerror("未找到 7-Zip", self.startup_error or "请先安装 7-Zip。")
            return
        paths = self._sorted_keys()
        paths = [k for k in paths if k in self.entries]
        if not paths:
            messagebox.showinfo("提示", "请先添加文件。")
            return
        self._recompute(paths)

    def _recompute(self, paths):
        algs = self.selected_algorithms()
        for key in paths:
            entry = self.entries.get(key)
            if entry:
                entry.hashes = {}
                entry.error = ""
        self._mark_dirty()
        self.btn_run.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.progress.configure(value=0)
        self.percent_text.set("0%")
        self.runner = HashRunner(
            paths, algs, self.sevenzip_path,
            on_result=lambda e: self._queue.put(("result", e)),
            on_progress=lambda done, total, name: self._queue.put(("progress", done, total, name)),
            on_log=lambda line, kind="info": self._queue.put(("log", line, kind)),
            on_finished=lambda ok, msg: self._queue.put(("finished", ok, msg)),
        )
        self.runner.start()

    def on_cancel(self):
        if self._busy():
            self.runner.cancel()
            self.status_text.set("正在停止…")

    def _busy(self) -> bool:
        runner = self.runner
        if runner is None:
            return False
        try:
            return bool(runner.is_alive())
        except Exception:
            return False

    # ==================================================================
    # 表格渲染
    # ==================================================================
    def _rebuild_rows(self):
        only_dupes = self.only_dupes.get()
        analysis = compare.analyse(list(self.entries.values()), self.selected_algorithms()) \
            if only_dupes else None
        visible = self._sorted_keys()
        self.tree.delete(*self.tree.get_children(""))
        for key in visible:
            entry = self.entries.get(key)
            if entry is None:
                continue
            if only_dupes and key not in analysis["duplicates"]:
                continue
            values = [
                "",
                entry.name,
                human_size(entry.size) if entry.size else "-",
                human_time(entry.mtime),
                "",
            ]
            for alg in self.selected_algorithms():
                values.append(self._display_hash(entry, alg))
            tags = ("error",) if entry.error and not entry.hashes else ()
            self.tree.insert("", "end", iid=key, values=values, tags=tags)
        total = len(self.entries)
        shown = len(self.tree.get_children(""))
        self.count_text.set(f"{total} 个文件"
                            + (f"（显示 {shown} 个）" if shown != total else ""))
        self._apply_analysis()

    def _sorted_keys(self) -> list[str]:
        column, ascending = self.sort_state
        entries = list(self.entries.values())
        if column == "name":
            key = lambda e: e.name.lower()
        elif column == "size":
            key = lambda e: e.size
        elif column == "mtime":
            key = lambda e: e.mtime
        elif column == "mark":
            key = lambda e: e.name.lower()
        elif column == "ref":
            key = lambda e: e.name.lower()
        else:
            key = lambda e: e.hash_of(column) or "\uffff"
        try:
            return [e.path for e in sorted(entries, key=key, reverse=not ascending)]
        except TypeError:
            return [e.path for e in entries]

    def _sort_by(self, column: str):
        if self.sort_state[0] == column:
            self.sort_state = (column, not self.sort_state[1])
        else:
            self.sort_state = (column, True)
        self._rebuild_rows()

    def _mark_dirty(self):
        self._analysis_dirty = True

    def _apply_analysis(self):
        """着色重复分组、写「标记」「参考」列、刷新汇总。"""
        algs = self.selected_algorithms()
        entries = list(self.entries.values())
        analysis = compare.analyse(entries, algs)
        primary = analysis["primary"]

        reference = self.ref_var.get().strip()
        ref_algs = self._ref_algorithms()
        hits = compare.reference_hits(entries, ref_algs, reference) if reference else {}
        # 少于 4 位十六进制时 reference_hits 故意不匹配（否则满屏都是命中），
        # 界面要说明原因，不然用户以为搜索没生效。
        ref_too_short = bool(reference) and not hits and \
            len(normalize_hash(reference)) < compare.REFERENCE_MIN_CHARS
        show_alg_in_ref = self.ref_alg.get() in ("", REF_ALL)

        groups = compare.duplicate_groups(entries, primary) if primary else []
        group_tag: dict[str, str] = {}
        for index, group in enumerate(groups, 1):
            tag = f"grp{(index - 1) % len(theme.GROUP_COLORS)}"
            for entry in group:
                group_tag[entry.path] = tag

        computed = sum(1 for e in entries if e.hashes)
        # 每一行都要重新算 tags：原来只在重复组的循环里设置，导致
        # 「唯一文件命中参考值」不上色（最常见的用法恰恰是拿一个哈希去
        # 一堆文件里找它），而且清空参考值后旧高亮撤不掉。
        for entry in entries:
            if not self.tree.exists(entry.path):
                continue
            hit = hits.get(entry.path)
            if hit:
                tags = [REF_TIER[hit[0]][1]]
            elif entry.path in analysis["conflicts"]:
                tags = ["conflict"]
            elif entry.path in group_tag:
                tags = [group_tag[entry.path]]
            elif entry.error and not entry.hashes:
                tags = ["error"]
            else:
                tags = []

            mark = analysis["labels"].get(entry.path, "")
            if entry.path in analysis["conflicts"]:
                mark = "⚠ 算法结果不一致"
            elif entry.error and not entry.hashes:
                mark = "✗ 未算出"
            ref_flag = ""
            if reference and not ref_too_short:
                if hit:
                    level, alg = hit
                    text = REF_TIER[level][0]
                    if show_alg_in_ref and level != "exact":
                        text = f"{text} {sevenzip.ALG_LABEL.get(alg, alg)}"
                    ref_flag = text
                else:
                    ref_flag = "✗ 未命中"
            values = list(self.tree.item(entry.path, "values"))
            if len(values) >= 5:
                values[0] = mark
                values[4] = ref_flag
                self.tree.item(entry.path, values=values, tags=tuple(tags))
            else:
                self.tree.item(entry.path, tags=tuple(tags))

        dupes = len(analysis["duplicates"])
        parts = [f"共 {len(entries)} 个文件", f"已算出 {computed} 个"]
        if analysis["group_count"]:
            parts.append(f"{analysis['group_count']} 组重复（涉及 {dupes} 个文件）")
        else:
            parts.append("未发现重复")
        if ref_too_short:
            parts.append(f"参考值太短（至少 {compare.REFERENCE_MIN_CHARS} 位才不误导）")
        elif reference:
            tiers = {"exact": 0, "prefix": 0, "contains": 0}
            for level, _alg in hits.values():
                tiers[level] += 1
            parts.append(f"参考值命中 {len(hits)} 个：完全一致 {tiers['exact']}"
                         f" · 前缀 {tiers['prefix']} · 包含 {tiers['contains']}")
        if analysis["conflicts"]:
            parts.append(f"⚠ {len(analysis['conflicts'])} 个文件的算法结果互相矛盾")
        self.summary_text.set(" · ".join(parts))
        self._analysis_dirty = False

    def _update_verdict(self):
        selected = list(self.tree.selection())
        text = compare.compare_paths(list(self.entries.values()),
                                    self.selected_algorithms(), selected)
        self.verdict.configure(state="normal")
        self.verdict.delete("1.0", "end")
        tag = "ok" if "一致 ✅" in text else ("bad" if "差异 ❌" in text else "")
        self.verdict.insert("1.0", text + "\n", tag)
        self.verdict.configure(state="disabled")

    # ==================================================================
    # 复制 / 导出
    # ==================================================================
    def _copy_selected(self):
        algs = self.selected_algorithms()
        primary = algs[0] if algs else "SHA256"
        lines = [self._show(self.entries[k].hash_of(primary)) for k in self.tree.selection()
                 if k in self.entries and self.entries[k].hash_of(primary)]
        if not lines:
            self.status_text.set("选中的行还没有哈希结果")
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.status_text.set(f"已复制 {len(lines)} 条 {primary} 哈希")

    def _copy_with_names(self, entry):
        if entry is None or not entry.hashes:
            self.status_text.set("这一行还没有哈希结果")
            return
        algs = self.selected_algorithms() or list(entry.hashes)
        text = "\n".join(f"{entry.name}  {alg}={self._show(entry.hash_of(alg))}" for alg in algs
                         if entry.hash_of(alg))
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_text.set("已复制「文件名 + 哈希」")

    def _on_double_click(self, _event):
        """双击行 = 在资源管理器里定位该文件。"""
        for key in self.tree.selection():
            if os.path.exists(key):
                open_in_explorer(key)
                return

    def _export(self):
        if not self.entries:
            self.status_text.set("没有可导出的结果")
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="导出哈希报告", defaultextension=".txt",
            initialfile=time.strftime("哈希报告_%Y%m%d_%H%M.") + "txt",
            filetypes=[("文本文件", "*.txt"), ("CSV 文件", "*.csv")])
        if not path:
            return
        algs = self.selected_algorithms()
        entries = [self.entries[k] for k in self._sorted_keys() if k in self.entries]
        try:
            with open(path, "w", encoding="utf-8-sig" if path.lower().endswith(".csv") else "utf-8") as fh:
                if path.lower().endswith(".csv"):
                    fh.write("文件,大小,修改时间," + ",".join(algs) + "\n")
                    for e in entries:
                        row = [e.name, str(e.size), human_time(e.mtime)] + \
                              [self._show(e.hash_of(a)) for a in algs]
                        fh.write(",".join('"' + c.replace('"', '""') + '"' for c in row) + "\n")
                else:
                    fh.write(f"{self.sevenzip_version or self.sevenzip_path}\n")
                    fh.write("生成时间：" + time.strftime("%Y-%m-%d %H:%M:%S") + "\n\n")
                    for e in entries:
                        fh.write(f"{e.path}\n")
                        fh.write(f"    大小: {human_size(e.size)}    修改时间: {human_time(e.mtime)}\n")
                        for alg in algs:
                            if e.hash_of(alg):
                                fh.write(f"    {alg}: {self._show(e.hash_of(alg))}\n")
                            elif e.error:
                                fh.write(f"    {alg}: （{e.error}）\n")
                        fh.write("\n")
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self.log(f"报告已导出：{path}", "ok")
        self.status_text.set(f"报告已导出：{os.path.basename(path)}")

    # ==================================================================
    # 日志与队列
    # ==================================================================
    def log(self, line: str, kind: str = "info"):
        self._queue.put(("log", line, kind))

    def _write_log(self, line: str, kind: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", str(line) + "\n", kind or "info")
        total = int(self.log_text.index("end-1c").split(".")[0])
        if total > 600:
            self.log_text.delete("1.0", f"{total - 500}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _drain_queue(self):
        if self._closing:
            return
        try:
            for _ in range(200):
                try:
                    event = self._queue.get_nowait()
                except queue.Empty:
                    break
                kind = event[0]
                if kind == "log":
                    self._write_log(event[1], event[2])
                elif kind == "result":
                    entry = event[1]
                    self.entries[entry.path] = entry
                    if self.tree.exists(entry.path):
                        values = list(self.tree.item(entry.path, "values"))
                        for index, alg in enumerate(self.selected_algorithms()):
                            col = 5 + index
                            if col < len(values):
                                values[col] = self._display_hash(entry, alg)
                        if values and (entry.error and not entry.hashes):
                            values[0] = "✗ 未算出"
                        self.tree.item(entry.path, values=values,
                                       tags=("error",) if entry.error and not entry.hashes else ())
                    self._mark_dirty()
                elif kind == "progress":
                    done, total, name = event[1], event[2], event[3]
                    percent = done * 100.0 / max(1, total)
                    self.progress.configure(value=percent)
                    self.percent_text.set(f"{percent:.0f}%")
                    self.status_text.set(f"正在计算 {name}（{done}/{total}）")
                elif kind == "caps":
                    self._apply_capabilities(event[1])
                elif kind == "finished":
                    self._on_finished(event[1], event[2])
        except Exception:
            pass
        if self._analysis_dirty:
            self._apply_analysis()
        self._drain_after = self.after(120, self._drain_queue)

    def _apply_capabilities(self, supported):
        if not supported:
            return
        for name, var in self.algorithm_vars.items():
            if name not in supported:
                var.set(False)
                check = self.algorithm_checks.get(name)
                if check is not None:
                    check.configure(state="disabled")
        self._on_algorithms_changed()
        missing = [n for n, _l, _c in sevenzip.ALGORITHMS if n not in supported]
        if missing:
            self.log(f"本机 7-Zip 不支持：{'、'.join(missing)}（已在界面上禁用）", "warn")

    def _on_finished(self, ok: bool, message: str):
        self.btn_run.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self._apply_analysis()
        self._update_verdict()
        self.status_text.set(message)
        self._write_log(("✔ " if ok else "■ ") + message, "ok" if ok else "error")
        if ok:
            self.progress.configure(value=100)
            self.percent_text.set("100%")

    # ==================================================================
    # 7z 路径
    # ==================================================================
    def _pick_sevenzip(self):
        path = filedialog.askopenfilename(
            parent=self, title="选择 7z.exe（或 7za.exe / 7zr.exe）",
            initialdir=os.path.dirname(self.sevenzip_path) or r"C:\Program Files\7-Zip",
            filetypes=[("7-Zip 可执行文件", "7z.exe 7za.exe 7zr.exe"), ("所有文件", "*.*")])
        if not path:
            return
        if not sevenzip.works(path):
            messagebox.showerror("无法使用", f"这个文件不能当 7-Zip 用：\n{path}")
            return
        self.sevenzip_path = path
        self.sevenzip_version = sevenzip.version(path)
        self.startup_error = ""
        self.settings["sevenzip"] = path
        save_settings({**self.settings, "sevenzip": path})
        self._refresh_7z_label()
        self.btn_run.configure(state="normal")
        self.log(f"已切换 7-Zip：{self.sevenzip_version or path}", "ok")
        self._probe_capabilities()

    # ==================================================================
    # 关闭
    # ==================================================================
    def destroy(self):
        self._closing = True
        for attr in ("_drain_after", "_probe_after"):
            aid = getattr(self, attr, None)
            if aid:
                try:
                    self.after_cancel(aid)
                except Exception:
                    pass
                setattr(self, attr, None)
        try:
            super().destroy()
        except tk.TclError:
            pass

    def _on_close(self):
        try:
            if self._busy():
                if not messagebox.askyesno("正在计算", "哈希还没算完，确定退出？"):
                    return
                self.runner.cancel()
        except Exception:
            pass
        try:
            data = dict(self.settings)
            data["algorithms"] = self.selected_algorithms()
            data["sevenzip"] = self.sevenzip_path
            data["include_sub"] = self.include_sub.get()
            data["full_hash"] = self.full_hash.get()
            data["hash_case"] = self.hash_case.get()
            data["ref_alg"] = self.ref_alg.get()
            data["last_dir"] = self._last_dir
            data["geometry"] = self.geometry()
            save_settings(data)
        except Exception:
            pass
        self.destroy()
