#!/usr/bin/env python3
"""把仓库里的 skill 源码安装 / 更新到宿主的全局技能目录。

用法（在仓库根目录）：
    python skills/install.py                  # 安装到 TRAE 全局技能目录（默认）
    python skills/install.py --detect         # 扫描本机已存在的宿主技能目录
    python skills/install.py --target ~/.claude/skills

说明：
- 默认目标是 TRAE 的全局技能目录 `~/.trae-cn/skills/`，路径由宿主规定，不能自选。
- `--target` 可装到任意宿主自己的技能目录，skill 会落在 `<target>/sdz-ptah-avatar/`。
- `--detect` 只打印本机实际存在的宿主技能目录，不安装；没有就如实说明。
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

DEFAULT_SKILLS_DIR = Path.home() / ".trae-cn" / "skills"


def remove_path(path):
    """删除已存在的目标，兼容目录、普通文件与符号链接。"""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def detect_skills_dirs():
    """扫描本机实际存在的宿主技能目录，只返回真实存在且含 skills 的子目录。"""
    home = Path.home()
    bases = [home]
    config = home / ".config"
    if config.is_dir():
        bases.append(config)

    found = []
    seen = set()
    for base in bases:
        try:
            children = sorted(base.iterdir())
        except OSError:
            continue
        for child in children:
            # 家目录下只看隐藏目录（.trae-cn、.claude 等）；
            # .config 下各宿主目录通常不以 . 开头，不做隐藏过滤。
            if base == home and not child.name.startswith("."):
                continue
            if not child.is_dir():
                continue
            skills = child / "skills"
            if not skills.is_dir():
                continue
            try:
                key = skills.resolve()
            except OSError:
                key = skills
            if key in seen:
                continue
            seen.add(key)
            found.append(skills)
    return found


def print_detection():
    found = detect_skills_dirs()
    if not found:
        print("未检测到任何宿主技能目录。")
        return

    print("检测到的宿主技能目录：")
    others = 0
    for skills in found:
        try:
            is_trae = skills.resolve() == DEFAULT_SKILLS_DIR.resolve()
        except OSError:
            is_trae = False
        if is_trae:
            print("  [TRAE，默认] {}".format(skills))
        else:
            print("  [其他宿主] {}".format(skills))
            others += 1

    if others == 0:
        print("本机未检测到其他宿主。")


def install(name, mode, skills_dir):
    src = Path(__file__).resolve().parent / name
    if not (src / "SKILL.md").is_file():
        raise SystemExit("找不到 skill 源码：{}".format(src))

    skills_dir.mkdir(parents=True, exist_ok=True)
    dst = skills_dir / name
    staging = skills_dir / (".{}.staging".format(name))

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
    parser = argparse.ArgumentParser(description="同步 skill 源码到宿主全局技能目录")
    parser.add_argument("--name", default="sdz-ptah-avatar", help="skill 目录名")
    parser.add_argument(
        "--mode",
        choices=["auto", "link", "copy"],
        default="auto",
        help="auto 优先符号链接、失败回退复制（默认）；link 仅符号链接；copy 仅复制",
    )
    parser.add_argument(
        "--target",
        metavar="DIR",
        help="宿主技能目录，skill 装到 <DIR>/<name>；默认 TRAE 的 ~/.trae-cn/skills",
    )
    parser.add_argument(
        "--detect",
        action="store_true",
        help="扫描本机实际存在的宿主技能目录并打印，不安装",
    )
    args = parser.parse_args()

    if args.detect and args.target:
        parser.error("--detect 不能与 --target 同时使用")
    if args.detect:
        print_detection()
        return 0

    if args.target:
        skills_dir = Path(args.target).expanduser().resolve()
    else:
        skills_dir = DEFAULT_SKILLS_DIR

    try:
        install(args.name, args.mode, skills_dir)
    except OSError as exc:
        print("安装失败：{}".format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
