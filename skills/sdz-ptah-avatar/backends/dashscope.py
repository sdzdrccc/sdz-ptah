# -*- coding: utf-8 -*-
"""阿里云百炼（DashScope）适配器：Qwen-Image-2.1 官方 API。

文档：https://docs.bailian.console.aliyun.com/zh/model-studio/qwen-image-generation-and-editing-api-reference

协议（异步，本项目默认走这条）：
  POST {base}/api/v1/services/aigc/image-generation/generation
       头：Authorization: Bearer <key>、X-DashScope-Async: enable
       体：model / input.messages[0].content[{image}?..., {text}] / parameters
       返回 {"output": {"task_id": ...}}
  GET  {base}/api/v1/tasks/{task_id}
       返回 task_status（PENDING / RUNNING / SUCCEEDED / FAILED）与图片 URL

同步入口在 /api/v1/services/aigc/multimodal-generation/generation，多图或大尺寸时
容易占住连接，故默认异步；注册表 dashscope.async_mode=false 可切同步。

与魔搭同名模型的差别（也是接它的理由）：
- **有 seed**（0~2147483647）⇒ 本项目第一次有可能做到可复现。
- **支持透明通道**：由提示词决定，无需额外参数，直接产出带 alpha 的 PNG。
- 付费、合规授权明确；RPM 更高。
共同点：**尺寸上限 2048²**（512² ~ 2048²，宽高比 1:8~8:1），4096 不支持。

约束：
- Key 只从环境变量读（默认 DASHSCOPE_API_KEY），不进 manifest / 日志。
- 模型、Endpoint、API Key 必须同地域，跨地域必失败（官方文档强调）。
- DashScope 协议尺寸分隔符是星号 `2048*2048`，不是 x。
- 2.1 系列**不支持 b64_json**，只能拿 URL（24 小时有效），故拿到就立刻下载。
- 2.1 系列**不支持 negative_prompt**（仅 3.0），传了不生效，这里不下发。
- prompt_extend 默认关闭：我们希望自己编译的提示词不被二次改写。
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

TOKEN_ENVS = ("DASHSCOPE_API_KEY", "DASHSCOPE_TOKEN")
SUCCEED = ("SUCCEED", "SUCCEEDED", "SUCCESS")
FAILED = ("FAILED", "CANCELED", "CANCELLED", "UNKNOWN")
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")


class DashScopeAdapter(Adapter):
    kind = "executable"

    def __init__(self, spec, root=None):
        super().__init__(spec)
        ds = spec.get("dashscope") or {}
        self.base_url = (os.environ.get("DASHSCOPE_BASE_URL") or ds.get("url")
                         or "https://dashscope.aliyuncs.com").rstrip("/")
        self.model_id = ds.get("model") or (spec.get("model") or {}).get("name")
        self.async_mode = ds.get("async_mode", True)
        self.poll_s = float(ds.get("poll_s", 5))
        self.timeout_s = float(ds.get("timeout_s", 600))
        self.prompt_extend = bool(ds.get("prompt_extend", False))
        self.watermark = bool(ds.get("watermark", False))
        self.response_format = ds.get("response_format") or "url"
        self.max_area = int(ds.get("max_area") or 2048 * 2048)
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
            raise CapabilityError("缺少 DashScope Key：请设置环境变量 %s" % self._token_env_hint())
        return tok

    # ------------------------------------------------------------ HTTP
    def _http(self, method, path, payload=None, timeout=60, async_hdr=False):
        url = path if path.startswith("http") else self.base_url + path
        h = {"Accept": "application/json", "Authorization": "Bearer " + self._require_token()}
        if async_hdr:
            h["X-DashScope-Async"] = "enable"
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
                raise CapabilityError("DashScope 鉴权失败（HTTP %d）：Key 无效、地域不匹配或无权限。%s"
                                      % (e.code, body))
            if e.code == 429:
                raise TransientError("DashScope 限流（HTTP 429）：%s" % body)
            if e.code >= 500:
                raise TransientError("DashScope 服务错误（HTTP %d）：%s" % (e.code, body))
            raise AdapterError("DashScope 请求被拒（HTTP %d）：%s" % (e.code, body))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise TransientError("无法连接 DashScope（%s）：%s" % (self.base_url, e))
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            raise AdapterError("DashScope 返回非 JSON：%r" % raw[:200])

    def _download(self, url, dest_stem):
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=180) as r:
                data = r.read()
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise TransientError("下载生成图失败：%s" % e)
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in _IMAGE_EXT:
            ext = mimetypes.guess_extension(ctype) or ".png"
            if ext in (".jpe", ".jpeg"):
                ext = ".jpg"
        dest = dest_stem + ext
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        return dest

    # ------------------------------------------------------------ 能力检测
    def probe(self):
        out = {"ok": False, "reachable": None,
               "env": {"engine": "dashscope", "api_base": self.base_url,
                       "model": self.model_id, "async_mode": self.async_mode},
               "detail": None, "warnings": []}
        if not self._token():
            out["detail"] = "未设置 Key（环境变量 %s）" % self._token_env_hint()
            return out
        out["ok"] = True
        out["detail"] = "Key 已设置（未验证有效性，首次调用时验证）"
        return out

    def reproducibility(self, seeds, env):
        if not self.caps.seed:
            return False, "后端无 seed"
        if any(s is None for s in seeds):
            return False, "存在未记录 seed 的图"
        if not (self.model.get("version") or self.model.get("sha256")):
            return False, "模型未锁定版本 / sha256"
        return False, ("有 seed 且已记录，但在线服务不保证模型版本锁定；"
                       "官方未声明 seed 可完全复现，先按不可复现记账")

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
        return "%d*%d" % (w, h)     # DashScope 协议用星号

    def _payload(self, req, seed, image_refs):
        content = [{"image": u} for u in image_refs]
        content.append({"text": req.prompt_zh or req.prompt_en})
        params = {"size": self._size(req), "n": 1,
                  "prompt_extend": self.prompt_extend,
                  "watermark": self.watermark,
                  "response_format": self.response_format}
        if seed is not None:
            params["seed"] = int(seed)
        return {"model": self.model_id,
                "input": {"messages": [{"role": "user", "content": content}]},
                "parameters": params}

    @staticmethod
    def _extract_images(output):
        """兼容两种返回结构，返回图片 URL 列表。"""
        urls = []
        for r in (output.get("results") or []):
            if isinstance(r, dict) and r.get("url"):
                urls.append(r["url"])
        if urls:
            return urls
        for ch in (output.get("choices") or []):
            msg = (ch or {}).get("message") or {}
            for item in (msg.get("content") or []):
                if isinstance(item, dict) and item.get("image"):
                    urls.append(item["image"])
        if urls:
            return urls

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in ("url", "image") and isinstance(v, str) and v.startswith("http"):
                        urls.append(v)
                    else:
                        walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(output)
        return urls

    def _run_once(self, body):
        if not self.async_mode:
            resp = self._http("POST", "/api/v1/services/aigc/multimodal-generation/generation",
                              body, timeout=self.timeout_s)
            imgs = self._extract_images(resp.get("output") or {})
            if not imgs:
                raise AdapterError("DashScope 同步返回里找不到图片：%s"
                                   % json.dumps(resp, ensure_ascii=False)[:400])
            return None, imgs
        resp = self._http("POST", "/api/v1/services/aigc/image-generation/generation",
                          body, async_hdr=True)
        task_id = ((resp.get("output") or {}).get("task_id")) or resp.get("task_id")
        if not task_id:
            raise AdapterError("DashScope 未返回 task_id：%s"
                               % json.dumps(resp, ensure_ascii=False)[:400])
        deadline = time.time() + self.timeout_s
        while time.time() < deadline:
            st = self._http("GET", "/api/v1/tasks/%s" % task_id)
            out = st.get("output") or {}
            status = str(out.get("task_status") or st.get("task_status") or "").upper()
            if status in SUCCEED:
                imgs = self._extract_images(out)
                if not imgs:
                    raise AdapterError("任务 %s 成功但没有返回图片：%s"
                                       % (task_id, json.dumps(out, ensure_ascii=False)[:400]))
                return task_id, imgs
            if status in FAILED:
                err = out.get("message") or st.get("message") or json.dumps(st, ensure_ascii=False)[:400]
                raise AdapterError("DashScope 任务失败（%s）：%s" % (task_id, err))
            time.sleep(self.poll_s)
        raise AdapterError("DashScope 任务超时（%ss）：task_id=%s；稍后 --resume 取回"
                           % (self.timeout_s, task_id))

    def generate(self, req, out_dir):
        warnings = self.check(req)
        probe = self.probe()
        if not probe["ok"]:
            raise CapabilityError("DashScope 适配器 %s 不可用：%s" % (self.id, probe["detail"]))
        warnings += probe.get("warnings") or []
        if self.prompt_extend:
            warnings.append("prompt_extend=true：官方会二次改写提示词，实际用词由服务端决定")
        refs = [r.path for r in req.references]
        if req.init_image:
            refs = [req.init_image] + refs
        image_refs = [self._image_ref(p) for p in refs]
        seeds = self.pick_seeds(req)
        images, calls, t0, tasks = [], 0, time.time(), []
        try:
            for i, seed in enumerate(seeds):
                body = self._payload(req, seed, image_refs)
                task_id, urls = self.with_retry(self._run_once, body)
                calls += 1
                idx = req.start_index + i
                stem = os.path.join(out_dir, os.path.splitext(output_name(req.prefix, idx))[0])
                dest = self.with_retry(self._download, urls[0], stem)
                w, h = image_size(dest)
                images.append(GeneratedImage(dest, idx, seed, w, h, sha256_of(dest)))
                tasks.append(task_id)
        except AdapterError as e:
            e.calls_used = calls
            raise
        params = {"model": self.model_id, "size": self._size(req), "n": 1,
                  "seeds": seeds, "async_mode": self.async_mode,
                  "prompt_extend": self.prompt_extend, "watermark": self.watermark,
                  "response_format": self.response_format,
                  "reference_count": len(image_refs),
                  "image_input": "data_uri" if image_refs else None,
                  "task_ids": tasks,
                  "negative_prompt": False, "steps": None, "guidance": None}
        ok, why = self.reproducibility(seeds, probe["env"])
        return GenerationResult(
            adapter_id=self.id, kind=self.kind, stage=req.stage, images=images,
            calls=calls, params=params, model=dict(self.model, name=self.model_id),
            reproducible=ok, reproducible_reason=why, license=self.license,
            env=probe["env"],
            timings={"total_s": round(time.time() - t0, 2),
                     "per_image_s": round((time.time() - t0) / max(calls, 1), 2)},
            warnings=warnings)
