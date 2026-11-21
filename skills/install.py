#!/usr/bin/env python3
"""把仓库里的 skill 源码安装 / 更新到本机全局 skill 目录。

用法（在仓库根目录）：
    python skills/install.py
    python skills/install.py --mode copy

说明：
- 默认（--mode auto）优先创建符号链接：改仓库源码立即生效，无需重复同步。
- 无创建符号链接权限时（如 Windows 未开启开发者模式）自动回退为复制，
  并打印回退原因；--mode copy 可强制走复制。
- 复制模式下改完源码，需要重新执行本脚本才会生效。
- 目标目录已存在时会被覆盖，保证全局副本与仓库一致。
"""

import argparse
import os
import shutil
import sys
from pathlib import Path


def remove_path(path):
    """删除已存在的目标，兼容目录、普通文件与符号链接。"""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def install(name, mode):
    src = Path(__file__).resolve().parent / name
    if not (src / "SKILL.md").is_file():
        raise SystemExit("找不到 skill 源码：{}".format(src))

    root = Path.home() / ".trae-cn" / "skills"
    root.mkdir(parents=True, exist_ok=True)
    dst = root / name
    staging = root / (".{}.staging".format(name))

    linked = False
    if mode != "copy":
        remove_path(dst)
        try:
            os.symlink(str(src), str(dst), target_is_directory=True)
            linked = True
        except (OSError, NotImplementedError) as exc:
            print("创建符号链接失败，回退为复制：{}".format(exc))

    if not linked:
        if staging.exists() or staging.is_symlink():
            remove_path(staging)
        shutil.copytree(
            str(src),
            str(staging),
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        remove_path(dst)
        shutil.move(str(staging), str(dst))

    if not (dst / "SKILL.md").is_file():
        raise SystemExit("安装失败：{} 下没有 SKILL.md".format(dst))

    if linked:
        print("已安装（符号链接）：{} -> {}".format(dst, src))
        print("修改仓库源码后立即生效，无需再次同步。")
    else:
        print("已安装（复制）：{}".format(dst))
        print("修改仓库源码后需重新执行本脚本才会生效。")


def main():
    parser = argparse.ArgumentParser(description="同步 skill 源码到本机全局 skill 目录")
    parser.add_argument("--name", default="sdz-ptah-avatar", help="skill 目录名")
    parser.add_argument(
        "--mode",
        choices=["auto", "link", "copy"],
        default="auto",
        help="auto 优先符号链接、失败回退复制（默认）；link 仅符号链接；copy 仅复制",
    )
    args = parser.parse_args()
    try:
        install(args.name, args.mode)
    except OSError as exc:
        print("安装失败：{}".format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
