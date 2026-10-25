# -*- coding: utf-8 -*-
"""7-Zip 定位、能力探测与 ``7z h`` 输出解析。

只依赖系统里已有的 7-Zip，不内置二进制、也不写任何临时文件。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

from .util import IS_WIN, popen_kwargs

# (7z 开关名, 界面显示名, 十六进制长度)
ALGORITHMS = [
    ("SHA256", "SHA-256", 64),
    ("MD5", "MD5", 32),
    ("SHA1", "SHA-1", 40),
    ("SHA512", "SHA-512", 128),
    ("SHA384", "SHA-384", 96),
    ("CRC32", "CRC-32", 8),
    ("BLAKE2SP", "BLAKE2sp", 128),
]
ALG_HEX_LEN = {name: length for name, _label, length in ALGORITHMS}
ALG_LABEL = {name: label for name, label, _length in ALGORITHMS}

# 一次命令能塞下的路径总长度上限。Windows 的命令行限制约 32767 字符，
# 留出开关与转义的余量，超过就拆成多个 7z 进程。
MAX_CMD_CHARS = 14000

_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_VERSION_RE = re.compile(r"7-Zip\s+(\d[\w.\-]*)")

_WIN_FALLBACK_DIRS = [
    r"D:\ProgramFiles\7-Zip",
    r"C:\Program Files\7-Zip",
    r"C:\Program Files (x86)\7-Zip",
    r"D:\Program Files\7-Zip",
    r"E:\Program Files\7-Zip",
]
_EXE_NAMES = ("7z.exe", "7za.exe", "7zr.exe")


class SevenZipNotFound(RuntimeError):
    pass


def _exe_from_registry() -> str:
    """从注册表 App Paths 反查安装目录（7zFM.exe 与 7z.exe 同目录）。

    用户没把 7-Zip 加进 PATH 是常态，这条路径能覆盖绝大多数安装。
    """
    if not IS_WIN:
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\7zFM.exe"
    for hive, flag in ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
                       (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                       (winreg.HKEY_CURRENT_USER, 0)):
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | flag) as key:
                value, _type = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        fm = str(value or "").strip('"')
        if not fm:
            continue
        for name in _EXE_NAMES:
            cand = os.path.join(os.path.dirname(fm), name)
            if os.path.isfile(cand):
                return cand
    return ""


def candidate_paths() -> list[str]:
    """按优先级列出所有可能的 7z 可执行文件。"""
    found: list[str] = []

    def add(path: str):
        if path and os.path.isfile(path) and path not in found:
            found.append(path)

    # 1) 用户显式指定
    add(os.environ.get("HASHGUI_7Z", "").strip())
    # 2) PATH 里的完整版优先
    for name in ("7z", "7za"):
        add(shutil.which(name) or "")
    # 3) 常见安装目录
    for folder in _WIN_FALLBACK_DIRS:
        for name in _EXE_NAMES:
            add(os.path.join(folder, name))
    # 4) 注册表
    add(_exe_from_registry())
    # 5) PATH 里的精简版（7zr 也支持 h 命令，算哈希够用）
    add(shutil.which("7zr") or "")
    return found


def locate() -> str:
    """返回可用的 7z 可执行文件路径，找不到抛 :class:`SevenZipNotFound`。"""
    for path in candidate_paths():
        if works(path):
            return path
    raise SevenZipNotFound(
        "未找到 7-Zip。\n\n"
        "请安装 7-Zip（https://www.7-zip.org/download.html），"
        "或在界面里点「指定 7z.exe」手动选择安装目录下的 7z.exe。\n\n"
        "已尝试的位置：PATH、C:\\Program Files\\7-Zip、D:\\ProgramFiles\\7-Zip、注册表 App Paths。"
    )


_ok_cache: dict[str, bool] = {}


def works(path: str) -> bool:
    """确认这个文件真能当 7-Zip 用（有些同名残留不可执行）。"""
    if not path or not os.path.isfile(path):
        return False
    if path in _ok_cache:
        return _ok_cache[path]
    ok = False
    try:
        proc = subprocess.run([path, "-h"], timeout=15, **popen_kwargs())
        ok = proc.returncode == 0 and b"7-Zip" in (proc.stdout or b"")
    except Exception:
        ok = False
    _ok_cache[path] = ok
    return ok


def version(path: str) -> str:
    """返回形如 ``7-Zip 26.00 (x64)`` 的版本串。"""
    if not path:
        return ""
    try:
        proc = subprocess.run([path], timeout=15, **popen_kwargs())
        text = (proc.stdout or b"").decode("utf-8", errors="replace")
    except Exception:
        return ""
    for line in text.splitlines():
        m = _VERSION_RE.search(line)
        if m:
            return line.strip()
    return ""


def build_command(sevenzip: str, algorithms, paths) -> list[str]:
    """拼 ``7z h`` 命令。

    ``-sccUTF-8`` 必须带：7z 默认按控制台 OEM 码页输出（中文 Windows 是 GBK），
    不带这个开关时中文文件名会解出乱码，路径也就对不回去了。

    ``-bsp1`` 把进度行显式送到 stdout。不带的话 7z 在输出被重定向时不报进度，
    整个表等到算完才一次性刷出来 —— 单个大文件就表现为进度条一直 0% 然后跳 100%。
    """
    cmd = [sevenzip, "h"]
    cmd += [f"-scrc{alg}" for alg in algorithms]
    cmd += ["-sccUTF-8", "-bsp1"]
    cmd += list(paths)
    return cmd


_PROGRESS_RE = re.compile(r"^(\d{1,3})%(?:\s+(.*))?$")


def parse_progress_line(line: str):
    """解析 7z 的进度行 ``45% 文件名``，返回 ``(百分比, 当前文件)``；不是进度行返回 None。"""
    m = _PROGRESS_RE.match(line.strip())
    if not m:
        return None
    percent = int(m.group(1))
    if percent > 100:
        return None
    return percent, (m.group(2) or "").strip()


def plan_batches(paths) -> list[tuple[str, list[str], list[str]]]:
    """把待算文件切成 ``(工作目录, 相对路径, 原始绝对路径)`` 批次。

    为什么不能直接传绝对路径：``7z h`` 的 Name 列会把绝对路径**削成文件名**，
    两个不同目录下的同名文件回显完全一样，结果就只能靠猜。传相对路径时
    它原样回显（实测 ``_t\\dir1\\same.bin``），才能可靠对回去。

    同盘符的文件共用一个「最近公共祖先」当工作目录；跨盘符必须分开，
    否则 ``os.path.relpath`` 会抛 ``ValueError``。每批再按命令行长度上限拆分。
    """
    import collections

    groups: dict[str, list[str]] = collections.OrderedDict()
    for path in paths:
        full = os.path.abspath(path)
        drive = os.path.splitdrive(full)[0].upper() or "\\"
        groups.setdefault(drive, []).append(full)

    batches: list[tuple[str, list[str], list[str]]] = []
    for fulls in groups.values():
        base = os.path.commonpath(fulls) if len(set(os.path.dirname(f) for f in fulls)) > 1 \
            else os.path.dirname(fulls[0])
        if os.path.isfile(base):          # 只有一个文件时 commonpath 可能直接是文件
            base = os.path.dirname(base)
        current: list[str] = []
        absolute: list[str] = []
        used = len(base) + 96             # 开关与 7z 自身占用的长度
        for full in fulls:
            rel = os.path.relpath(full, base)
            need = len(rel) + 3
            if current and used + need > MAX_CMD_CHARS:
                batches.append((base, current, absolute))
                current, absolute, used = [], [], len(base) + 96
            current.append(rel)
            absolute.append(full)
            used += need
        if current:
            batches.append((base, current, absolute))
    return batches


WARN_START = "WARNINGS for files and folders"
_DASH_LINE = re.compile(r"^-{5,}")


def parse_row(line: str, algorithms, base: str = ""):
    """把 ``7z h`` 表格里的一行解析成 ``(路径, {算法: 哈希})``；不是数据行返回 None。

    表格长这样（多算法时每个算法各占一列）::

        SHA256                                                           MD5        Size  Name
        ---------------------------------------------------------------- ------------ ----  ----
        f48173d2...c386 4ad9b7718b9037d13964252220fd774f            16  dir1\\a.txt

    最后的「汇总哈希」行没有 Name 列，靠字段个数就能排除；表头与分隔行
    靠「Size 必须是纯数字」排除；文件名里带空格不受影响，因为名称是
    切分后剩下的整段。``base`` 是发起命令时的工作目录，用于把回显的
    相对路径还原成绝对路径（见 :func:`plan_batches`）。
    """
    count = len(algorithms or [])
    if count == 0:
        return None
    parts = line.split(None, count + 1)
    if len(parts) < count + 2:
        return None
    hashes, size, name = parts[:count], parts[count], parts[count + 1]
    if not size.isdigit():
        return None
    for alg, value in zip(algorithms, hashes):
        expected = ALG_HEX_LEN.get(alg)
        if expected and len(value) != expected:
            return None
        if not _HEX_RE.match(value):
            return None
    name = name.strip().strip('"')
    if not name:
        return None
    full = name if os.path.isabs(name) else os.path.join(base or os.getcwd(), name)
    return os.path.normcase(os.path.abspath(full)), {
        alg: value.upper() for alg, value in zip(algorithms, hashes)}


def parse_warning_line(line: str, base: str = ""):
    """解析警告块里的 ``路径 : 原因`` 行。"""
    stripped = line.strip()
    if _DASH_LINE.match(stripped):
        return None
    path, sep, reason = stripped.rpartition(" : ")
    if not sep or not path or not reason:
        return None
    full = path.strip()
    if not os.path.isabs(full):
        full = os.path.join(base or os.getcwd(), full)
    return os.path.normcase(os.path.abspath(full)), reason.strip()


def parse_output(text: str, algorithms, base: str = "") -> tuple[dict, dict]:
    """解析整段 ``7z h`` 输出，返回 ``({路径: {算法: 哈希}}, {路径: 失败原因})``。"""
    results: dict[str, dict] = {}
    warnings: dict[str, str] = {}
    in_warnings = False
    for raw_line in (text or "").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if WARN_START in stripped:
            in_warnings = True
            continue
        if in_warnings:
            if stripped.startswith("-----"):
                in_warnings = False
                continue
            found = parse_warning_line(stripped, base)
            if found:
                warnings[found[0]] = found[1]
            continue
        found = parse_row(stripped, algorithms, base)
        if found:
            results[found[0]] = found[1]
    return results, warnings


def supported_algorithms(sevenzip: str, sample_file: str) -> set[str]:
    """探测本机 7z 支持哪些哈希算法。

    老版本 7-Zip 没有 BLAKE2SP / SHA-384，勾选了会让整条命令直接报
    ``Unknown switch``；这里先一把梭（一条命令带上全部算法），
    失败再逐个试，最后把不支持的从界面里灰掉。
    """
    names = [name for name, _label, _length in ALGORITHMS]
    if not sevenzip or not sample_file or not os.path.isfile(sample_file):
        return set(names)
    try:
        proc = subprocess.run(build_command(sevenzip, names, [sample_file]),
                              timeout=60, **popen_kwargs())
        text = (proc.stdout or b"").decode("utf-8", errors="replace")
        if proc.returncode == 0 and "Command Line Error" not in text:
            return set(names)
    except Exception:
        pass

    supported: set[str] = set()
    for alg in names:
        try:
            proc = subprocess.run(build_command(sevenzip, [alg], [sample_file]),
                                  timeout=60, **popen_kwargs())
            text = (proc.stdout or b"").decode("utf-8", errors="replace")
            if proc.returncode == 0 and "Command Line Error" not in text:
                supported.add(alg)
        except Exception:
            continue
    return supported or {"SHA256", "MD5", "SHA1"}
