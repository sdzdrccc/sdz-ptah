# -*- coding: utf-8 -*-
"""ComfyUI 能力探针（只读）：枚举 checkpoint、关键节点与显存，并与注册表比对。

适用于任意 ComfyUI 实例——本地（127.0.0.1:8188）或魔搭创空间托管的在线实例。

用法：
    py -3 design/probe/comfyui_probe.py
    py -3 design/probe/comfyui_probe.py --url http://127.0.0.1:8189
    py -3 design/probe/comfyui_probe.py --url https://xxx.ms.show --header "Authorization: Bearer xxx"

做的事（全部是 GET，不提交任何任务、不生成图）：
    1. /system_stats        版本与显存
    2. /object_info/CheckpointLoaderSimple   已安装的 checkpoint 列表
    3. /object_info          关键节点是否存在（CLIPLoader / VAELoader / KSampler 等）
    4. 与 skills/sdz-ptah-avatar/references/backends.v2.json 里 comfyui-* 条目比对
    5. 打印"要加进网页模型列表还缺什么"
"""
import argparse
import json
import os
import sys
from urllib import request, error

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REGISTRY = os.path.join(ROOT, "skills", "sdz-ptah-avatar", "references", "backends.v2.json")
SKILL = os.path.join(ROOT, "skills", "sdz-ptah-avatar")

# 截图里出现的 6 个 checkpoint（用于标注"是否在 ComfyUI 里找到"）
TARGETS = [
    "MiniMax-H3-Image",
    "Qwen-Image-2.1 Turbo",
    "MOSS-Image-SFT",
    "Qwen-Image-2.1",
    "Krea-2-Turbo",
    "Qwen-Image-Edit-2511",
]
KEY_NODES = [
    "CheckpointLoaderSimple", "CLIPLoader", "VAELoader", "UNETLoader",
    "CLIPTextEncode", "KSampler", "KSamplerAdvanced", "EmptyLatentImage",
    "VAEDecode", "SaveImage", "LoadImage", "ImageScale",
]


def get(url, timeout=5, headers=None):
    req = request.Request(url, headers=headers or {})
    with request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8188",
                    help="ComfyUI 地址，本地或魔搭创空间均可")
    ap.add_argument("--header", action="append", default=[],
                    help='自定义请求头，可重复，如 --header "Authorization: Bearer xxx"')
    ap.add_argument("--token", default=None,
                    help="便捷开关：等价于 --header \"Authorization: Bearer <token>\"")
    args = ap.parse_args()
    base = args.url.rstrip("/")

    headers = {}
    for h in args.header:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()
    if args.token:
        headers["Authorization"] = "Bearer " + args.token

    try:
        stats = get(base + "/system_stats", headers=headers)
    except (error.URLError, error.HTTPError, OSError) as exc:
        print("连不上 ComfyUI（%s）：%s" % (base, exc))
        if isinstance(exc, error.HTTPError) and exc.code in (401, 403):
            print("→ 该实例需要登录态，用 --header 或 --token 传入凭据后重试。")
        else:
            print("→ 确认地址与端口；本地实例需先启动，创空间实例需确认已开机。")
        return 2

    sysinfo = (stats.get("system") or {})
    print("ComfyUI 已连接：%s" % base)
    print("  版本: %s | python: %s" % (sysinfo.get("comfyui_version"), sysinfo.get("python_version")))
    for d in (stats.get("devices") or []):
        print("  设备: %s | 显存 total %.1f GB / free %.1f GB"
              % (d.get("name"), (d.get("vram_total") or 0) / 1e9, (d.get("vram_free") or 0) / 1e9))

    try:
        info = get(base + "/object_info/CheckpointLoaderSimple")
        ckpts = (((info.get("CheckpointLoaderSimple") or {}).get("input") or {})
                 .get("required") or {}).get("ckpt_name") or [[]]
        ckpts = ckpts[0] if ckpts and isinstance(ckpts[0], list) else []
    except (error.URLError, OSError) as exc:
        print("读 checkpoint 列表失败：%s" % exc)
        ckpts = []

    print("\n本地 checkpoint（%d 个）：" % len(ckpts))
    for c in ckpts:
        mark = " ★目标" if any(t.lower() in c.lower() for t in TARGETS) else ""
        print("  - %s%s" % (c, mark))

    print("\n截图中 6 个模型的落地情况：")
    for t in TARGETS:
        hit = [c for c in ckpts if t.lower() in c.lower() or c.lower() in t.lower()]
        print("  %-24s %s" % (t, ("找到: " + hit[0]) if hit else "★ 本地未找到同名 checkpoint"))

    try:
        allinfo = get(base + "/object_info", timeout=20)
        print("\n关键节点：")
        for n in KEY_NODES:
            print("  %-24s %s" % (n, "有" if n in allinfo else "缺"))
    except (error.URLError, OSError) as exc:
        print("\n读节点表失败：%s" % exc)

    # 与注册表比对
    print("\n注册表里的 comfyui 后端：")
    try:
        reg = json.load(open(REGISTRY, encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print("  读注册表失败：%s" % exc)
        return 0
    missing_wf = 0
    for x in reg.get("backends", []):
        if not str(x.get("id", "")).startswith("comfyui"):
            continue
        wf = (x.get("comfyui") or {}).get("workflow") or ""
        ok = os.path.isfile(os.path.join(SKILL, wf)) if wf else False
        if not ok:
            missing_wf += 1
        print("  %-26s available=%-5s 工作流%s  %s"
              % (x["id"], x.get("available"), "有" if ok else "★缺", wf))
    print("\n小结：%d 个 comfyui 后端缺工作流文件。" % missing_wf)
    print("要新增模型，需要：① 从 ComfyUI 导出该模型的 API 格式工作流 → workflows/ "
          "② 在 backends.v2.json 填 comfyui.bindings 与 capabilities ③ available=true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
