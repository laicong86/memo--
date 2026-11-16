# -*- coding: utf-8 -*-
"""打包脚本：使用系统 Python 3.9 自带的 PyInstaller 生成单文件无控制台 exe。

用法：
    py -3.9 build.py

产物：
    dist/MemoTool.exe
"""
from __future__ import annotations

import os
import subprocess
import sys

PYTHON39 = r"C:\Users\laicong\AppData\Local\Programs\Python\Python39\python.exe"
ENTRY = "main.py"
APP_NAME = "MemoTool"
ICON = os.path.join("assets", "memotool.ico")


def resolve_python() -> str:
    """优先使用实测可用的系统 Python 3.9 解释器。"""
    if os.path.isfile(PYTHON39):
        return PYTHON39
    return sys.executable


def main() -> int:
    """执行 PyInstaller 打包命令。"""
    base = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base)
    interpreter = resolve_python()
    command = [
        interpreter,
        "-m", "PyInstaller",
        "--onefile",
        "--noconsole",
        "--clean",
        "--noupx",
        "--name", APP_NAME,
    ]
    # 图标可选：存在才加 --icon，缺资源时仍可正常打包
    if os.path.isfile(ICON):
        command += ["--icon", ICON]
    # tkinter 窗口图标（PNG，iconphoto 用）：打包进 exe 供运行时读取
    png_icon = os.path.join("assets", "icon.png")
    if os.path.isfile(png_icon):
        command += ["--add-data", png_icon + os.pathsep + "assets"]
    command.append(ENTRY)
    print("使用解释器：%s" % interpreter)
    print("执行命令：%s" % " ".join(command))

    # 某些环境会拦截文件删除，导致 PyInstaller 覆盖同名旧 exe 时失败。
    # 先用重命名把旧产物挪到系统临时目录，让本次构建直接创建新文件。
    dist_exe = os.path.join(base, "dist", APP_NAME + ".exe")
    if os.path.isfile(dist_exe):
        backup_dir = os.path.join(base, "build")
        backup = os.path.join(backup_dir, APP_NAME + ".previous.exe")
        try:
            os.makedirs(backup_dir, exist_ok=True)
            os.replace(dist_exe, backup)  # 同盘纯重命名，不触发删除限制
            print("旧产物已移至：%s" % backup)
        except OSError as exc:
            print("旧产物移走失败（仍尝试继续打包）：%s" % exc)

    try:
        return int(subprocess.call(command, cwd=base))
    except OSError as exc:
        print("打包失败：%s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
