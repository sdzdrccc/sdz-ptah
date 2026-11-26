# -*- coding: utf-8 -*-
"""MiniMax 官方开放平台适配器（同步接口）。

文档：https://platform.minimax.cn/docs/guides/image-generation

协议：
  文生图  POST {url}/v1/images/generations
  图片编辑 POST {url}/v1/images/edits      （参考图走 images[].image_url）
       头：Authorization: Bearer <api_key>
       体：model / prompt / size（"宽x高" 或 "auto"）/ n / response_format / output_format
           编辑另有 images[]（公网 URL、mm_file:// 或 data URI）
       返回（同步，无 task_id）：
            {"data": [{"url": ..., "size": "2720x1536", "revised_prompt": ...} | {"error": {...}}],
             "output_format": "png", "usage": {"generated_images": N}}

与 ModelScope 通道的关键差别（也是本后端存在的理由）：
- **面积上限 20,000,000 像素**，4096×4096（16.78M）合法；魔搭网关只到 2048。
- 无 seed / 无负向词 / 无 steps / 无 guidance —— 参数一律不下发，可复现恒为 false。
- 单张失败不影响整体（只要有成功的就返回 200），逐项检查 data[] 里的 error。

约束：
- api_key 只从环境变量读取（默认 MINIMAX_API_KEY），不写入 manifest / 日志。
- 参考图用 data URI 内联，不经第三方图床；单请求总量需 ≤ 64 MiB。
- 输出图按原样字节保存，扩展名跟随链接与 output_format。
"""

import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import (Adapter, AdapterError, CapabilityError, GeneratedImage,
                   GenerationResult, TransientError, image_size, output_name,
                   sha256_of)

TOKEN_ENVS = ("MINIMAX_API_KEY", "MINIMAX_TOKEN")


class MiniMaxAdapter(Adapter):
    kind = "executable"

    def __init__(self, spec, root=None):
        super().__init__(spec)
        mm = spec.get("minimax") or {}
        self.base_url = (os.environ.get("MINIMAX_API_BASE") or mm.get("url")
                         or "https://api.minimax.cn").rstrip("/")
        self.model_id = mm.get("model") or (spec.get("model") or {}).get("name")
        self.timeout_s = float(mm.get("timeout_s", 600))
        # url：链接 24 小时有效，我们自己下载；b64_json 有总面积上限，故默认 url
        self.response_format = mm.get("response_format") or "url"
        self.output_format = mm.get("output_format") or "png"
        # 关掉 Prompt 增强，保证我们编译好的提示词不被二次改写（默认开）
        self.disable_prompt_enhanced = mm.get("disable_prompt_enhanced", True)
        self.max_area = int(mm.get("max_area") or 20000000)
        envs = (spec.get("secrets") or {}).get("token_env")
        self.token_envs = ((envs,) if isinstance(envs, str) else tuple(envs or ())) + TOKEN_ENVS

    # ------------------------------------------------------------ 凭据
    def _token(self):
        for name in self.token_envs:
            v = os.environ.get(name)
            if v:
                return v.strip()
        return None

    def _token_env_hint(self):
        return " / ".join(dict.fromkeys(self.token_envs))

    def _require_token(self):
        tok = self._token()
        if not tok:
            raise CapabilityError("缺少 MiniMax API Key：请设置环境变量 %s" % self._token_env_hint())
        return tok

    # ------------------------------------------------------------ HTTP
    def _http(self, method, path, payload=None, timeout=60):
        url = path if path.startswith("http") else self.base_url + path
        h = {"Accept": "application/json", "Authorization": "Bearer " + self._require_token()}
        data = None
        if payload is not None:
            h["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:600]
            if e.code in (401, 403):
                raise CapabilityError("MiniMax 鉴权失败（HTTP %d）：API Key 无效或无权限。%s"
                                      % (e.code, body))
            if e.code == 402:
                raise CapabilityError("MiniMax 余额不足（HTTP 402）：%s" % body)
            if e.code == 429:
                raise TransientError("MiniMax 限流（HTTP 429）：%s" % body)
            if e.code >= 500:
                raise TransientError("MiniMax 服务错误（HTTP %d）：%s" % (e.code, body))
            raise AdapterError("MiniMax 请求被拒（HTTP %d）：%s" % (e.code, body))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise TransientError("无法连接 MiniMax（%s）：%s" % (self.base_url, e))
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            raise AdapterError("MiniMax 返回非 JSON：%r" % raw[:200])

    def _download(self, url, dest_stem):
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=180) as r:
                data = r.read()
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise TransientError("下载生成图失败：%s" % e)
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp"):
            ext = "." + self.output_format.lower().lstrip(".")
            if ext in (".jpe", ".jpeg"):
                ext = ".jpg"
            if ext not in (".png", ".jpg", ".webp"):
                ext = mimetypes.guess_extension(ctype) or ".png"
        dest = dest_stem + ext
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        return dest

    # ------------------------------------------------------------ 能力检测
    def probe(self):
        """不消耗额度：官方平台没有公开的模型列表接口，只校验 Key 是否已设置。"""
        out = {"ok": False, "reachable": None,
               "env": {"engine": "minimax", "api_base": self.base_url, "model": self.model_id},
               "detail": None, "warnings": []}
        if not self._token():
            out["detail"] = "未设置 API Key（环境变量 %s）" % self._token_env_hint()
            return out
        out["ok"] = True
        out["detail"] = "API Key 已设置（未验证有效性，首次调用时验证）"
        return out

    def reproducibility(self, seeds, env):
        return False, "官方 API 无 seed 参数，同 prompt 也不保证复现"

    # ------------------------------------------------------------ 生成
    def _image_ref(self, path):
        mime = mimetypes.guess_type(path)[0] or "image/png"
        with open(path, "rb") as f:
            return "data:%s;base64,%s" % (mime, base64.b64encode(f.read()).decode("ascii"))

    def _size(self, req):
        w, h = int(req.width), int(req.height)
        if w * h > self.max_area:
            raise CapabilityError("尺寸 %dx%d 面积 %.2f MP 超过官方上限 %.2f MP"
                                  % (w, h, w * h / 1e6, self.max_area / 1e6))
        return "%dx%d" % (w, h)

    def _payload(self, req, image_refs):
        body = {"model": self.model_id,
                "prompt": req.prompt_zh or req.prompt_en,
                "n": 1,                              # 逐张调用，便于按调用数记账
                "size": self._size(req),
                "response_format": self.response_format,
                "output_format": self.output_format}
        if self.disable_prompt_enhanced and not image_refs:
            # 只有文生图可关；图片编辑「prompts are always enhanced」，
            # 带上这个字段会被直接拒绝：
            # HTTP 400 invalid params, disable_prompt_enhanced is not supported
            # for image edits, prompts are always enhanced (2013)
            body["disable_prompt_enhanced"] = True
        if image_refs:
            body["images"] = [{"image_url": u} for u in image_refs]
        return body

    def _run_once(self, body, has_refs):
        path = "/v1/images/edits" if has_refs else "/v1/images/generations"
        resp = self._http("POST", path, body, timeout=self.timeout_s)
        data = resp.get("data") or []
        if not data:
            raise AdapterError("MiniMax 未返回 data：%s" % json.dumps(resp, ensure_ascii=False)[:300])
        first = data[0]
        if first.get("error"):
            err = first["error"]
            raise AdapterError("MiniMax 单张失败（%s）：%s"
                               % (err.get("code"), err.get("message")))
        item = {"url": first.get("url"), "b64": first.get("b64_json"),
                "size": first.get("size"), "revised_prompt": first.get("revised_prompt")}
        if not item["url"] and not item["b64"]:
            raise AdapterError("MiniMax 返回项既无 url 也无 b64_json：%s"
                               % json.dumps(first, ensure_ascii=False)[:300])
        return item, resp

    def generate(self, req, out_dir):
        warnings = self.check(req)
        probe = self.probe()
        if not probe["ok"]:
            raise CapabilityError("MiniMax 官方 API 不可用：%s" % probe["detail"])
        warnings += probe.get("warnings") or []
        refs = [r.path for r in req.references]
        if req.init_image:
            refs = [req.init_image] + refs
        image_refs = [self._image_ref(p) for p in refs]
        if image_refs and self.disable_prompt_enhanced:
            # 编辑通道强制增强，我们的提示词会被官方二次改写，必须如实告知
            warnings.append("官方图片编辑通道强制开启 Prompt 增强（不接受 disable_prompt_enhanced），"
                            "本张提示词经服务端改写；实际用词已记入 params.revised_prompts")
        seeds = self.pick_seeds(req)
        images, calls, t0, sizes, revised = [], 0, time.time(), [], []
        try:
            for i, _seed in enumerate(seeds):
                body = self._payload(req, image_refs)
                item, _resp = self.with_retry(self._run_once, body, bool(image_refs))
                calls += 1
                idx = req.start_index + i
                stem = os.path.join(out_dir, os.path.splitext(output_name(req.prefix, idx))[0])
                if item["url"]:
                    dest = self.with_retry(self._download, item["url"], stem)
                else:
                    # 未启用 b64 时不会走到这里；保留以便将来切 response_format
                    dest = stem + "." + self.output_format.lower()
                    with open(dest, "wb") as f:
                        f.write(base64.b64decode(item["b64"]))
                w, h = image_size(dest)
                images.append(GeneratedImage(dest, idx, None, w, h, sha256_of(dest)))
                sizes.append(item["size"])
                revised.append(item["revised_prompt"])
        except AdapterError as e:
            e.calls_used = calls
            raise
        params = {"model": self.model_id, "size": self._size(req), "n": 1,
                  "response_format": self.response_format,
                  "output_format": self.output_format,
                  "disable_prompt_enhanced": bool(self.disable_prompt_enhanced),
                  "reference_count": len(image_refs),
                  "image_input": "data_uri" if image_refs else None,
                  "returned_sizes": sizes,
                  "revised_prompts": revised,
                  "seed": None, "negative_prompt": False, "steps": None, "guidance": None}
        ok, why = self.reproducibility(seeds, probe["env"])
        return GenerationResult(
            adapter_id=self.id, kind=self.kind, stage=req.stage, images=images,
            calls=calls, params=params, model=dict(self.model, name=self.model_id),
            reproducible=ok, reproducible_reason=why, license=self.license,
            env=probe["env"],
            timings={"total_s": round(time.time() - t0, 2),
                     "per_image_s": round((time.time() - t0) / max(calls, 1), 2)},
            warnings=warnings)
