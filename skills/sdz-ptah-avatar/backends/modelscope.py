# -*- coding: utf-8 -*-
"""ModelScope 在线推理适配器（API-Inference，异步任务）。

协议：
  POST {url}/v1/images/generations
       头：Authorization: Bearer <token>、X-ModelScope-Async-Mode: true
       体：model / prompt / negative_prompt / size / seed / steps / guidance / image_url[]
       返回 {"task_id": ...}
  GET  {url}/v1/tasks/{task_id}
       头：X-ModelScope-Task-Type: image_generation
       返回 task_status（PENDING / RUNNING / SUCCEED / FAILED）与 output_images[]

约束：
- token 只从环境变量读取（默认 MODELSCOPE_TOKEN，兼容 MODELSCOPE_API_KEY / MODELSCOPE_SDK_TOKEN），
  不写入请求参数记录、manifest、日志。
- 参考图默认以 data URI（base64）直接放进 image_url，不经第三方图床；
  若服务不接受，可在注册表把 image_input 改为 "url" 并自行提供可访问链接（不推荐公开图床）。
- 单并发、两次提交之间至少间隔 min_interval_s，配合 policy.retry 退避，避免触发限流。
- 输出图按原样字节保存（保留服务写入的 AIGC 隐式标识元数据），扩展名跟随返回链接。
- 免费推理服务条款为非商用；在线模型无版本锁，结果标记为不可复现。
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

TOKEN_ENVS = ("MODELSCOPE_TOKEN", "MODELSCOPE_API_KEY", "MODELSCOPE_SDK_TOKEN")
_LAST_SUBMIT = [0.0]   # 进程内单并发节流


class ModelScopeAdapter(Adapter):
    kind = "executable"

    def __init__(self, spec, root=None):
        super().__init__(spec)
        ms = spec.get("modelscope") or {}
        self.base_url = (os.environ.get("MODELSCOPE_API_BASE") or ms.get("url")
                         or "https://api-inference.modelscope.cn").rstrip("/")
        self.model_id = ms.get("model") or (spec.get("model") or {}).get("name")
        self.image_input = ms.get("image_input") or "data_uri"
        self.poll_s = float(ms.get("poll_s", 5))
        self.timeout_s = float(ms.get("timeout_s", 600))
        self.min_interval_s = float(ms.get("min_interval_s", 2))
        self.size_param = ms.get("size_param", True)
        # 部分模型（如 MiniMax-H3-Image）要求 steps / guidance 只能取默认值，
        # 传非默认值会 HTTP 400。注册表置 false 即完全不发送该字段。
        # 默认 true 保持既有后端行为不变。
        self.steps_param = ms.get("steps_param", True)
        self.guidance_param = ms.get("guidance_param", True)
        # 负向词能力以 capabilities.negative_prompt 为准（与基类 check() 同源）。
        # 不支持负向词的模型（如 MiniMax-H3-Image）即使 brief 带了也不发送，否则 HTTP 400。
        self.negative_param = bool((spec.get("capabilities") or {}).get("negative_prompt", True))
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

    # ------------------------------------------------------------ HTTP
    def _http(self, method, path, payload=None, headers=None, timeout=60, auth=True):
        url = path if path.startswith("http") else self.base_url + path
        h = {"Accept": "application/json"}
        if payload is not None:
            h["Content-Type"] = "application/json"
        if auth:
            tok = self._token()
            if not tok:
                raise CapabilityError("缺少 ModelScope token：请设置环境变量 %s" % self._token_env_hint())
            h["Authorization"] = "Bearer " + tok
        h.update(headers or {})
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:600]
            if e.code in (401, 403):
                raise CapabilityError("ModelScope 鉴权失败（HTTP %d）：token 无效、未实名或未绑定阿里云。%s"
                                      % (e.code, body))
            if e.code == 429:
                raise TransientError("ModelScope 限流（HTTP 429），稍后重试：%s" % body)
            if e.code >= 500:
                raise TransientError("ModelScope 服务错误（HTTP %d）：%s" % (e.code, body))
            raise AdapterError("ModelScope 请求被拒（HTTP %d）：%s" % (e.code, body))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise TransientError("无法连接 ModelScope（%s）：%s" % (self.base_url, e))
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            raise AdapterError("ModelScope 返回非 JSON：%r" % raw[:200])

    def _download(self, url, dest_stem):
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=120) as r:
                data = r.read()
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise TransientError("下载生成图失败：%s" % e)
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp"):
            ext = mimetypes.guess_extension(ctype) or ".png"
            ext = ".jpg" if ext in (".jpe", ".jpeg") else ext
        dest = dest_stem + ext
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            f.write(data)          # 原样字节：保留服务写入的元数据（含 AIGC 隐式标识）
        os.replace(tmp, dest)
        return dest

    # ------------------------------------------------------------ 能力检测
    def probe(self):
        """不消耗额度：检查 token 是否已设置、服务是否可达、模型是否在公开列表里（列表不全，仅提示）。"""
        out = {"ok": False, "reachable": None, "env": {"engine": "modelscope",
                                                       "api_base": self.base_url,
                                                       "model": self.model_id},
               "detail": None, "warnings": []}
        try:
            models = self._http("GET", "/v1/models", auth=False, timeout=15)
            out["reachable"] = True
        except AdapterError as e:
            out["reachable"] = False
            out["detail"] = str(e)
            return out
        ids = [m.get("id") for m in (models.get("data") or []) if isinstance(m, dict)]
        if self.model_id and ids and self.model_id not in ids:
            out["warnings"].append("模型 %s 不在公开列表中（列表不完整，需实际调用确认）" % self.model_id)
        if not self._token():
            out["detail"] = "服务可达，但未设置 token（环境变量 %s）" % self._token_env_hint()
            return out
        out["ok"] = True
        out["detail"] = "服务可达，token 已设置（未验证有效性，首次调用时验证）"
        return out

    def reproducibility(self, seeds, env):
        if any(s is None for s in seeds):
            return False, "存在未记录 seed 的图"
        return False, "在线推理服务不保证模型版本固定（有 seed，但无版本锁）"

    # ------------------------------------------------------------ 生成
    def _image_ref(self, path):
        if self.image_input == "url":
            raise CapabilityError("image_input=url 需要可公开访问的参考图链接，适配器不负责上传；"
                                  "请改用 data_uri 或自行提供链接")
        mime = mimetypes.guess_type(path)[0] or "image/png"
        with open(path, "rb") as f:
            return "data:%s;base64,%s" % (mime, base64.b64encode(f.read()).decode("ascii"))

    def _payload(self, req, seed, image_refs):
        body = {"model": self.model_id,
                "prompt": req.prompt_zh or req.prompt_en}
        if self.negative_param and req.negative:
            body["negative_prompt"] = req.negative
        if self.size_param:
            body["size"] = "%dx%d" % (req.width, req.height)
        if seed is not None:
            body["seed"] = int(seed)
        if self.steps_param and req.steps is not None:
            body["steps"] = int(req.steps)
        if self.guidance_param and req.guidance is not None:
            body["guidance"] = float(req.guidance)
        if image_refs:
            body["image_url"] = image_refs
        return body

    def _throttle(self):
        wait = _LAST_SUBMIT[0] + self.min_interval_s - time.time()
        if wait > 0:
            time.sleep(wait)
        _LAST_SUBMIT[0] = time.time()

    def _run_once(self, body):
        self._throttle()
        resp = self._http("POST", "/v1/images/generations", body,
                          {"X-ModelScope-Async-Mode": "true"})
        task_id = resp.get("task_id")
        if not task_id:
            raise AdapterError("ModelScope 未返回 task_id：%s" % json.dumps(resp, ensure_ascii=False)[:300])
        deadline = time.time() + self.timeout_s
        while time.time() < deadline:
            st = self._http("GET", "/v1/tasks/%s" % task_id,
                            headers={"X-ModelScope-Task-Type": "image_generation"})
            status = (st.get("task_status") or "").upper()
            if status == "SUCCEED":
                imgs = st.get("output_images") or []
                if not imgs:
                    raise AdapterError("任务 %s 成功但没有返回图片" % task_id)
                return task_id, imgs
            if status == "FAILED":
                raise AdapterError("ModelScope 任务失败（%s）：%s"
                                   % (task_id, json.dumps(st.get("errors") or st, ensure_ascii=False)[:400]))
            time.sleep(self.poll_s)
        # 超时不自动重提：任务可能仍在跑，重提会重复消耗额度；交给 generate.py --resume
        raise AdapterError("ModelScope 任务超时（%ss）：task_id=%s；稍后 --resume 重新生成该张"
                           % (self.timeout_s, task_id))

    def generate(self, req, out_dir):
        warnings = self.check(req)
        probe = self.probe()
        if not probe["ok"]:
            if probe.get("reachable") is False:
                raise AdapterError("ModelScope 不可达：%s" % probe["detail"])
            raise CapabilityError("ModelScope 适配器 %s 不可用：%s" % (self.id, probe["detail"]))
        warnings += probe.get("warnings") or []
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
        params = {"model": self.model_id, "task_ids": tasks, "seeds": seeds,
                  "steps": req.steps if self.steps_param else None,
                  "guidance": req.guidance if self.guidance_param else None,
                  "size": "%dx%d" % (req.width, req.height) if self.size_param else None,
                  "negative_prompt": bool(req.negative) and self.negative_param,
                  "reference_count": len(image_refs), "image_input": self.image_input}
        ok, why = self.reproducibility(seeds, probe["env"])
        return GenerationResult(
            adapter_id=self.id, kind=self.kind, stage=req.stage, images=images,
            calls=calls, params=params, model=dict(self.model, name=self.model_id),
            reproducible=ok, reproducible_reason=why, license=self.license,
            env=probe["env"],
            timings={"total_s": round(time.time() - t0, 2),
                     "per_image_s": round((time.time() - t0) / max(calls, 1), 2)},
            warnings=warnings)
