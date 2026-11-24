#!/usr/bin/env python3
"""只读探测 ModelScope 创空间（Gradio 应用）的接口说明，不生成图片、不消耗额度。

用法：
  py -3 probe_gradio_studio.py https://studio-liudef07-qwen-image-212.api-inference.modelscope.net/

做的事：
  1. GET <base>/gradio_api/info  —— 各接口的参数名、类型、可选值、默认值
  2. GET <base>/config           —— 界面组件（用来对照 ratio / model 下拉框的选项）
  3. 结果写到 references/studio-info/<host>.info.json 和 .config.json，并打印摘要

token：如果环境变量 MODELSCOPE_TOKEN 已设置，会带上 Authorization 头；
token 不会被打印或写入文件。只用标准库。
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(HERE), "references", "studio-info")


def fetch(url, token):
    headers = {"User-Agent": "sdz-ptah-probe/1.0", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:500]
    except Exception as e:  # noqa: BLE001
        return None, repr(e)


def summarize_info(info):
    eps = info.get("named_endpoints") or {}
    print("\n== named_endpoints：%d 个" % len(eps))
    for name, ep in eps.items():
        params = ep.get("parameters") or []
        if not params and name.startswith(("/_lambda", "/lambda")):
            continue
        print("\n%s" % name)
        for p in params:
            t = p.get("python_type", {}).get("type") or p.get("type", {})
            choices = (p.get("type") or {}).get("enum") if isinstance(p.get("type"), dict) else None
            line = "  - %s: %s" % (p.get("parameter_name") or p.get("label"), t)
            if p.get("parameter_has_default"):
                line += "  默认=%r" % (p.get("parameter_default"),)
            if choices:
                line += "  可选=%s" % choices
            print(line)
        rets = ep.get("returns") or []
        print("  返回：%s" % [r.get("python_type", {}).get("type") for r in rets])


def summarize_config(cfg):
    print("\n== 下拉 / 单选组件")
    for c in cfg.get("components") or []:
        props = c.get("props") or {}
        if c.get("type") in ("dropdown", "radio") and props.get("choices"):
            ch = [x[0] if isinstance(x, (list, tuple)) else x for x in props["choices"]]
            print("  [%s] %s  默认=%r  可选=%s" % (c.get("id"), props.get("label"), props.get("value"), ch))


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    base = sys.argv[1].rstrip("/")
    host = urllib.parse.urlparse(base).hostname or "studio"
    token = os.environ.get("MODELSCOPE_TOKEN") or os.environ.get("MODELSCOPE_API_KEY")
    print("token：%s" % ("已设置（不显示）" if token else "未设置，按匿名访问"))
    os.makedirs(OUT_DIR, exist_ok=True)
    rc = 0
    for path, kind in (("/gradio_api/info", "info"), ("/config", "config")):
        status, body = fetch(base + path, token)
        print("\nGET %s -> %s" % (path, status))
        if status != 200:
            print("  " + body[:300])
            rc = 8
            continue
        out = os.path.join(OUT_DIR, "%s.%s.json" % (host, kind))
        with open(out, "w", encoding="utf-8") as f:
            f.write(body)
        print("  已保存：%s" % out)
        try:
            data = json.loads(body)
        except ValueError:
            print("  不是 JSON")
            continue
        (summarize_info if kind == "info" else summarize_config)(data)
    return rc


if __name__ == "__main__":
    sys.exit(main())
