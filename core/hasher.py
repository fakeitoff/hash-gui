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
        self.on_progress = on_progress or (lambda done, total, name: None)
        self.on_log = on_log or (lambda line, kind="info": None)
        self.on_finished = on_finished or (lambda ok, msg: None)

        self.entries: dict[str, Entry] = {}
        self._stop_event = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

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

        self.on_log(f"$ {os.path.basename(self.sevenzip_path)} h "
                    f"{' '.join('-scrc' + a for a in self.algorithms)} "
                    f"[{total} 个文件]", "cmd")

        for base, relative, absolute in sevenzip.plan_batches(readable):
            if self._stop_event.is_set():
                break
            self._run_chunk(base, relative, absolute, by_path)

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
                    continue
                key, hashes = found
                entry = by_path.get(key)
                if entry is None:          # 回显路径对不上时按新条目收下，不丢结果
                    entry = by_path.setdefault(key, Entry(path=key))
                entry.hashes = hashes
                entry.error = ""
                self.on_result(entry)
                self.on_progress(sum(1 for e in by_path.values() if e.hashes),
                                 len(by_path), entry.name)
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
