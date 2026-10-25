# -*- coding: utf-8 -*-
"""后台哈希任务：分块调用 7z、流式回填结果、支持取消。"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field

from . import sevenzip
from .util import norm_key, popen_kwargs


@dataclass
class Entry:
    """一个文件的哈希记录。"""

    path: str
    size: int = 0
    mtime: float = 0.0
    hashes: dict = field(default_factory=dict)
    error: str = ""

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    def hash_of(self, algorithm: str) -> str:
        return self.hashes.get(algorithm, "")


def stat_entry(path: str) -> Entry:
    try:
        st = os.stat(path)
        return Entry(path=path, size=st.st_size, mtime=st.st_mtime)
    except OSError as exc:
        return Entry(path=path, error=f"读取文件信息失败：{exc.strerror or exc}")


class HashRunner(threading.Thread):
    """在后台线程里用 7z 批量算哈希。

    回调都在后台线程触发，界面层需要自己切回主线程。
    """

    def __init__(self, paths, algorithms, sevenzip_path, on_result=None,
                 on_progress=None, on_log=None, on_finished=None):
        super().__init__(daemon=True)
        self.paths = [norm_key(p) for p in paths]
        self.algorithms = list(algorithms or ["SHA256"])
        self.sevenzip_path = sevenzip_path
        self.on_result = on_result or (lambda entry: None)
        # on_progress(已完成文件数, 文件总数, 当前文件名, 0~1 的整体进度)
        self.on_progress = on_progress or (lambda done, total, name, fraction: None)
        self.on_log = on_log or (lambda line, kind="info": None)
        self.on_finished = on_finished or (lambda ok, msg: None)

        self.entries: dict[str, Entry] = {}
        self._stop_event = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._total_bytes = 0
        self._bytes_before = 0
        self._chunk_total = 0         # 本批次原始总字节（整个批次内不变）
        self._chunk_done = 0          # 本批次里已出结果的文件字节
        self._last_percent = 0        # 7z 最近一次报告的批次百分比
        self._done_files = 0

    # -- 控制 -------------------------------------------------------------
    def cancel(self):
        self._stop_event.set()
        with self._lock:
            proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    @property
    def cancelled(self) -> bool:
        return self._stop_event.is_set()

    # -- 主流程 -----------------------------------------------------------
    def run(self):
        started = time.time()
        total = len(self.paths)
        if not total:
            self.on_finished(False, "没有要计算的文件")
            return
        if not self.sevenzip_path or not os.path.isfile(self.sevenzip_path):
            self.on_finished(False, "未找到 7-Zip，请先安装或手动指定 7z.exe")
            return

        by_path = {p: stat_entry(p) for p in self.paths}
        self.entries = by_path
        readable = [p for p in self.paths if not by_path[p].error]
        self._total_bytes = sum(by_path[p].size for p in readable) or 1
        self._bytes_before = sum(by_path[p].size for p in by_path if p not in set(readable))
        self._done_files = total - len(readable)

        self.on_log(f"$ {os.path.basename(self.sevenzip_path)} h "
                    f"{' '.join('-scrc' + a for a in self.algorithms)} "
                    f"[{total} 个文件]", "cmd")

        for base, relative, absolute in sevenzip.plan_batches(readable):
            if self._stop_event.is_set():
                break
            self._chunk_total = sum(by_path[p].size for p in absolute)
            self._chunk_done = 0
            self._last_percent = 0
            self._run_chunk(base, relative, absolute, by_path)
            self._bytes_before += self._chunk_total
        self._emit_progress("", 100)

        done = sum(1 for e in by_path.values() if e.hashes)
        for entry in by_path.values():
            if entry.hashes or entry.error:
                continue
            entry.error = entry.error or "未获得结果（文件不存在、被占用或已被跳过）"

        if self._stop_event.is_set():
            self.on_finished(False, f"已取消：完成 {done} / {total} 个文件")
            return
        failed = sum(1 for e in by_path.values() if not e.hashes)
        secs = max(0.01, time.time() - started)
        if failed:
            self.on_finished(False, f"{done} 个成功，{failed} 个失败 · 用时 {secs:.1f}s")
        else:
            size = sum(e.size for e in by_path.values())
            self.on_finished(True, f"{done} 个文件 · {size / 1048576:.1f} MB"
                                  f" · 用时 {secs:.1f}s（{size / 1048576 / secs:.0f} MB/s）")

    def _run_chunk(self, base, relative, absolute, by_path):
        """跑一批文件（工作目录 ``base`` + 相对路径），结果按行流式回填。"""
        cmd = sevenzip.build_command(self.sevenzip_path, self.algorithms, relative)
        try:
            proc = subprocess.Popen(cmd, cwd=base, text=True, encoding="utf-8",
                                    errors="replace", bufsize=1, **popen_kwargs())
        except OSError as exc:
            message = f"无法启动 7-Zip：{exc}"
            self.on_log(message, "error")
            for path in absolute:
                by_path[path].error = message
            return

        with self._lock:
            self._proc = proc

        warnings: dict[str, str] = {}
        in_warnings = False
        rows_seen = 0
        try:
            for raw in proc.stdout:
                if self._stop_event.is_set():
                    proc.terminate()
                    break
                stripped = raw.strip()
                if not stripped:
                    continue
                if sevenzip.WARN_START in stripped:
                    in_warnings = True
                    continue
                if in_warnings:
                    if stripped.startswith("-----"):
                        in_warnings = False
                        continue
                    found = sevenzip.parse_warning_line(stripped, base)
                    if found:
                        warnings[found[0]] = found[1]
                    continue
                found = sevenzip.parse_row(stripped, self.algorithms, base)
                if not found:
                    prog = sevenzip.parse_progress_line(stripped)
                    if prog:
                        self._emit_progress(prog[1], prog[0])
                    continue
                key, hashes = found
                entry = by_path.get(key)
                if entry is None:          # 回显路径对不上时按新条目收下，不丢结果
                    entry = by_path.setdefault(key, Entry(path=key))
                entry.hashes = hashes
                entry.error = ""
                rows_seen += 1
                self.on_result(entry)
                self._chunk_done += entry.size
                self._done_files += 1
                self._emit_progress(entry.name)
        finally:
            with self._lock:
                self._proc = None
            try:
                proc.wait(timeout=30)
            except Exception:
                proc.kill()

        for path in absolute:
            entry = by_path[path]
            if entry.hashes:
                continue
            entry.error = warnings.get(path) or "7z 未返回结果（文件不存在或被占用）"
            self.on_log(f"✗ {entry.name} —— {entry.error}", "error")
        # 没出结果的文件也算“已处理完”，否则文件计数会一直差几个
        self._done_files += max(0, len(absolute) - rows_seen)

    def _emit_progress(self, name: str, percent=None):
        """按**字节加权**汇报整体进度（0~1）。

        只按“完成文件数”算的话，单个大文件从头到尾都停在 0%、最后一格跳满，
        看起来就像没有进度条。7z 的 ``-bsp1`` 会实时吐 ``45% 文件名``，
        但那个百分比是**整批**的，所以这里取两者的较大值：

            批次内已出结果的文件字节   vs   批次总字节 × 7z 报的百分比

        只用后者会在文件刚出结果时把进度报低；只用前者（早期版本的写法）
        会在批次中途冲到 100% 再回落 —— 两个坑都得避开。
        """
        if percent is not None:
            self._last_percent = max(self._last_percent, max(0, min(100, percent)))
        in_chunk = max(float(self._chunk_done),
                       self._chunk_total * self._last_percent / 100.0)
        fraction = ((self._bytes_before + in_chunk) / self._total_bytes
                    if self._total_bytes else 1.0)
        self.on_progress(self._done_files, len(self.paths), name, max(0.0, min(1.0, fraction)))
