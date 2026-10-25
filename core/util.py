# -*- coding: utf-8 -*-
"""通用小工具：子进程参数、大小/时间格式化、资源管理器定位。"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

IS_WIN = sys.platform == "win32"
# 运行子进程时不弹出黑色控制台窗口（仅 Windows 有效）
CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0

# 程序根目录（core/ 的上一级）
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 粘贴哈希时常见的排版字符（空格、冒号、连字符、引号、换行……）
_SEPARATOR_RE = re.compile(r"[\s:,\-.'\"/\\|()\[\]{}<>]+")
_HEX_ONLY_RE = re.compile(r"^[0-9a-f]+$")
_HEX_RUN_RE = re.compile(r"[0-9a-f]+")


def popen_kwargs(**extra):
    """构建 subprocess 通用参数：隐藏控制台窗口、合并输出流。

    注意不要再传 ``capture_output``，这里已经把 stdout/stderr 接好了。
    7z 的表格走 stdout、警告走 stderr，合并成一条流才不会漏掉原因。
    """
    kw = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
    }
    if IS_WIN:
        kw["creationflags"] = CREATE_NO_WINDOW
    kw.update(extra)
    return kw


def human_size(num) -> str:
    """字节 -> 人类可读体积。"""
    if num is None:
        return "-"
    num = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(num)} B"
            return f"{num:.2f} {unit}"
        num /= 1024.0
    return f"{num:.2f} TB"


def human_time(timestamp) -> str:
    """Unix 时间戳 -> ``2026-10-25 10:31``。"""
    if not timestamp:
        return "-"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(timestamp)))
    except (TypeError, ValueError, OSError):
        return "-"


def norm_key(path: str) -> str:
    """路径归一化键：用于把 7z 回显的文件名对回我们自己的记录。"""
    try:
        return os.path.normcase(os.path.abspath(path))
    except Exception:
        return os.path.normcase(str(path))


def same_file(a: str, b: str) -> bool:
    return bool(a) and bool(b) and norm_key(a) == norm_key(b)


def open_in_explorer(path: str):
    """在文件管理器中定位文件。"""
    path = os.path.abspath(path)
    if IS_WIN:
        if os.path.isdir(path):
            os.startfile(path)  # noqa: S606
        else:
            subprocess.Popen(["explorer", "/select,", path])
    elif sys.platform == "darwin":
        if os.path.isfile(path):
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["open", path])
    else:
        target = path if os.path.isdir(path) else os.path.dirname(path)
        subprocess.Popen(["xdg-open", target])


def is_hash_text(text: str) -> bool:
    """看起来像一个十六进制哈希值（允许 0x 前缀与空格/冒号分隔）。"""
    cleaned = normalize_hash(text)
    return len(cleaned) >= 8 and all(c in "0123456789abcdef" for c in cleaned)


def normalize_hash(text) -> str:
    """把用户粘贴的哈希整理成小写纯十六进制。

    常见写法都要能吃下：``0xABCD...``、``ab cd ef ...``、``AB:CD:EF``、
    带算法前缀的 ``sha256:abcd...``、被引号/换行包起来的整行。
    """
    s = str(text or "").strip().lower()
    if not s:
        return ""
    # 去掉 sha256= / md5: 这类前缀（只在确实形如“算法名+分隔符”时去）
    for token in ("sha256", "sha512", "sha384", "sha1", "blake2sp", "md5", "crc32", "crc64"):
        for sep in ("=", ":", "/"):
            if s.startswith(token + sep):
                s = s[len(token) + 1:]
                break
    if s.startswith("0x"):
        s = s[2:]
    # 先把分隔符摘干净再判断：邮件、网页、sum 文件里常把 64 位哈希按
    # 8 位一组用空格或冒号排版，摘掉分隔符后本身就是完整哈希。
    # 早先版本直接取“最长一段连续十六进制”，把这种完整值砍成 8 位，
    # 结果本该是 exact 的匹配降级成了 prefix。
    joined = _SEPARATOR_RE.sub("", s)
    if joined and _HEX_ONLY_RE.match(joined):
        return joined
    # 还含别的文字（例如 “<hash>  文件名.bin”）时取最长的一段十六进制
    runs = _HEX_RUN_RE.findall(s)
    return max(runs, key=len) if runs else ""
