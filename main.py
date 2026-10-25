# -*- coding: utf-8 -*-
"""7z 哈希对比工具 —— 启动入口。

用法：
    python main.py                      启动图形界面
    python main.py 文件1 文件2 ...       启动并预加载（目录会自动展开成里面的文件）
    python main.py --run 文件1 文件2     预加载后立即开始计算
    python main.py --7z <路径>            本次启动使用指定的 7z.exe

「发送到」集成（install-sendto.bat）走的就是 ``--run`` + 多个路径这一条，
所以这里必须能吃下目录：资源管理器里多选时很可能混着文件夹。
"""
from __future__ import annotations

import os
import sys


def parse_args(argv):
    """解析命令行，返回 ``(文件列表, 是否立即计算)``。

    不在这一步过滤存在性：目录要留给界面按「含子文件夹」的设置决定怎么展开，
    而不存在的项交给界面逐行标出原因，比静默丢弃更好。
    """
    paths: list[str] = []
    auto_run = False
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--run":
            auto_run = True
        elif arg == "--7z" and index + 1 < len(argv):
            os.environ["HASHGUI_7Z"] = argv[index + 1]
            index += 1
        elif arg.startswith("-"):
            pass                       # 未知开关忽略，方便以后加
        else:
            paths.append(arg)
        index += 1
    return paths, auto_run


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    if root not in sys.path:
        sys.path.insert(0, root)

    try:
        from ui.app import App
    except ImportError as exc:
        print(f"导入界面模块失败：{exc}", file=sys.stderr)
        print("请确认 Python 自带 tkinter（Windows 官方安装包默认包含）。", file=sys.stderr)
        return 1

    paths, auto_run = parse_args(sys.argv[1:])
    app = App(initial_paths=paths)
    if auto_run:
        app.after(400, app.on_run)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
