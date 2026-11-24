# -*- coding: utf-8 -*-
"""本地 ComfyUI 适配器：通过 ComfyUI HTTP API 执行 API 格式工作流。

一个适配器条目 = 一个「profile」：工作流文件 + 参数绑定 + 能力 + 模型/许可。
只用标准库 urllib，不依赖 websocket / requests。
"""

import copy
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .base import (Adapter, AdapterError, CapabilityError, GeneratedImage,
                   GenerationResult, TransientError, image_size, output_name,
                   sha256_of)


class ComfyUIAdapter(Adapter):
    kind = "executable"

    def __init__(self, spec, root=None):
        super().__init__(spec)
        eng = spec.get("comfyui") or {}
        self.base_url = (os.environ.get("COMFYUI_URL") or eng.get("url")
                         or "http://127.0.0.1:8188").rstrip("/")
        self.timeout_s = float(eng.get("timeout_s", 600))
        self.poll_s = float(eng.get("poll_s", 1.0))
        wf = eng.get("workflow")
        self.workflow_path = os.path.join(root, wf) if (root and wf) else wf
        self.bindings = eng.get("bindings") or {}
        self.output_node = eng.get("output_node")
        self.client_id = uuid.uuid4().hex

    # ------------------------------------------------------------ HTTP
    def _req(self, method, path, data=None, headers=None, timeout=30):
        url = self.base_url + path
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:800]
            if e.code >= 500:
                raise TransientError("ComfyUI %s %s -> %d：%s" % (method, path, e.code, body))
            raise AdapterError("ComfyUI %s %s -> %d：%s" % (method, path, e.code, body))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise TransientError("无法连接 ComfyUI（%s）：%s" % (self.base_url, e))

    def _json(self, method, path, payload=None, timeout=30):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        raw = self._req(method, path, data,
                        {"Content-Type": "application/json"} if data else None, timeout)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _upload(self, path):
        """POST /upload/image（multipart），返回 ComfyUI 输入目录中的文件名。"""
        boundary = "----sdz" + uuid.uuid4().hex
        name = "sdz_%s_%s" % (sha256_of(path)[:12], os.path.basename(path))
        with open(path, "rb") as f:
            content = f.read()
        parts = [
            ("--%s\r\nContent-Disposition: form-data; name=\"image\"; filename=\"%s\"\r\n"
             "Content-Type: application/octet-stream\r\n\r\n" % (boundary, name)).encode("utf-8"),
            content,
            ("\r\n--%s\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue"
             "\r\n--%s--\r\n" % (boundary, boundary)).encode("utf-8"),
        ]
        raw = self._req("POST", "/upload/image", b"".join(parts),
                        {"Content-Type": "multipart/form-data; boundary=%s" % boundary}, 120)
        info = json.loads(raw.decode("utf-8"))
        sub = info.get("subfolder") or ""
        return (sub + "/" if sub else "") + info["name"]

    # ------------------------------------------------------------ 能力检测
    def probe(self):
        out = {"ok": False, "reachable": None, "env": {}, "detail": None}
        if not self.workflow_path or not os.path.isfile(self.workflow_path):
            out["detail"] = "工作流文件不存在：%s（请从 ComfyUI 导出 API 格式）" % self.workflow_path
            return out
        try:
            stats = self._json("GET", "/system_stats", timeout=10)
        except AdapterError as e:
            out["reachable"] = False
            out["detail"] = str(e)
            return out
        out["reachable"] = True
        system = stats.get("system") or {}
        devices = stats.get("devices") or []
        dev = devices[0] if devices else {}
        out["env"] = {
            "engine": "comfyui",
            "comfyui_version": system.get("comfyui_version"),
            "pytorch_version": system.get("pytorch_version"),
            "python_version": system.get("python_version"),
            "os": system.get("os"),
            "gpu": dev.get("name"),
            "vram_total_gb": round((dev.get("vram_total") or 0) / 2 ** 30, 2) or None,
            "vram_free_gb": round((dev.get("vram_free") or 0) / 2 ** 30, 2) or None,
        }
        missing = self._missing_bindings()
        if missing:
            out["detail"] = "工作流中找不到绑定节点：%s" % ", ".join(missing)
            return out
        out["ok"] = True
        out["detail"] = "ComfyUI 可达，工作流与绑定一致"
        return out

    def _load_workflow(self):
        with open(self.workflow_path, encoding="utf-8") as f:
            wf = json.load(f)
        if "nodes" in wf and "links" in wf:
            raise CapabilityError("%s 是 UI 格式工作流；请在 ComfyUI 用「导出（API）」重新导出"
                                  % self.workflow_path)
        return wf

    def _targets(self, key):
        t = self.bindings.get(key)
        if not t:
            return []
        return [t] if isinstance(t[0], str) else t

    def _missing_bindings(self):
        try:
            wf = self._load_workflow()
        except (OSError, ValueError, AdapterError) as e:
            return [str(e)]
        miss = []
        for key in self.bindings:
            for node, inp in self._targets(key):
                if node not in wf or inp not in (wf[node].get("inputs") or {}):
                    miss.append("%s→%s.%s" % (key, node, inp))
        return miss

    def _bind(self, wf, key, value):
        for node, inp in self._targets(key):
            wf[node]["inputs"][inp] = value

    # ------------------------------------------------------------ 生成
    def _build(self, req, seed, uploaded):
        wf = copy.deepcopy(self._load_workflow())
        style = self.caps.prompt_style
        positive = req.prompt_en if style in ("booru_tags", "natural_en") else (req.prompt_zh or req.prompt_en)
        self._bind(wf, "positive", positive)
        if req.negative is not None:
            self._bind(wf, "negative", req.negative)
        if seed is not None:
            self._bind(wf, "seed", seed)
        for key, val in (("steps", req.steps), ("guidance", req.guidance),
                         ("denoise", req.denoise)):
            if val is not None:
                self._bind(wf, key, val)
        self._bind(wf, "width", req.width)
        self._bind(wf, "height", req.height)
        for i, name in enumerate(uploaded.get("references", [])):
            self._bind(wf, "reference_%d" % (i + 1), name)
        if uploaded.get("init_image"):
            self._bind(wf, "init_image", uploaded["init_image"])
        self._bind(wf, "filename_prefix", "sdz/%s" % req.prefix)
        return wf

    def _run_once(self, wf):
        resp = self._json("POST", "/prompt", {"prompt": wf, "client_id": self.client_id}, 60)
        if resp.get("node_errors"):
            raise AdapterError("工作流校验失败：%s" % json.dumps(resp["node_errors"], ensure_ascii=False)[:800])
        pid = resp["prompt_id"]
        deadline = time.time() + self.timeout_s
        while time.time() < deadline:
            hist = self._json("GET", "/history/%s" % pid, timeout=30).get(pid)
            if hist:
                status = hist.get("status") or {}
                if status.get("status_str") == "error":
                    msgs = [m for m in status.get("messages", []) if m and m[0] == "execution_error"]
                    raise AdapterError("ComfyUI 执行失败：%s" % json.dumps(msgs, ensure_ascii=False)[:800])
                if status.get("completed", True) and hist.get("outputs"):
                    return pid, hist["outputs"]
            time.sleep(self.poll_s)
        raise TransientError("ComfyUI 超时（%ss）：prompt_id=%s" % (self.timeout_s, pid))

    def _images_from(self, outputs):
        nodes = [self.output_node] if self.output_node else list(outputs)
        imgs = []
        for n in nodes:
            imgs += (outputs.get(n) or {}).get("images", [])
        return [i for i in imgs if i.get("type") == "output"] or imgs

    def _download(self, info, dest):
        q = urllib.parse.urlencode({"filename": info["filename"],
                                    "subfolder": info.get("subfolder", ""),
                                    "type": info.get("type", "output")})
        data = self._req("GET", "/view?" + q, timeout=120)
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)

    def generate(self, req, out_dir):
        warnings = self.check(req)
        probe = self.probe()
        if not probe["ok"]:
            if probe.get("reachable") is False:
                # 服务没启动 / 暂时不可达：属于后端故障，启动 ComfyUI 后 --resume 即可
                raise AdapterError("ComfyUI 未启动或不可达（%s）：%s"
                                   % (self.base_url, probe["detail"]))
            # 工作流缺失 / 绑定不一致：配置问题，不得继续
            raise CapabilityError("ComfyUI 适配器 %s 配置不可用：%s" % (self.id, probe["detail"]))
        uploaded = {"references": [self.with_retry(self._upload, r.path) for r in req.references]}
        if req.init_image:
            uploaded["init_image"] = self.with_retry(self._upload, req.init_image)
        seeds = self.pick_seeds(req)
        images, calls, t0, last_params = [], 0, time.time(), {}
        try:
            for i, seed in enumerate(seeds):
                wf = self._build(req, seed, uploaded)
                pid, outputs = self.with_retry(self._run_once, wf)
                calls += 1
                got = self._images_from(outputs)
                if not got:
                    raise AdapterError("ComfyUI 未返回图片（prompt_id=%s）" % pid)
                idx = req.start_index + i
                dest = os.path.join(out_dir, output_name(req.prefix, idx))
                self._download(got[0], dest)
                w, h = image_size(dest)
                images.append(GeneratedImage(dest, idx, seed, w, h, sha256_of(dest)))
                last_params = {"prompt_id": pid, "seed": seed, "steps": req.steps,
                               "guidance": req.guidance, "denoise": req.denoise,
                               "width": req.width, "height": req.height,
                               "references": uploaded["references"],
                               "init_image": uploaded.get("init_image")}
        except AdapterError as e:
            e.calls_used = calls
            raise
        ok, why = self.reproducibility(seeds, probe["env"])
        return GenerationResult(
            adapter_id=self.id, kind=self.kind, stage=req.stage, images=images,
            calls=calls, params=last_params, model=self.model, reproducible=ok,
            reproducible_reason=why, license=self.license, env=probe["env"],
            timings={"total_s": round(time.time() - t0, 2),
                     "per_image_s": round((time.time() - t0) / max(calls, 1), 2)},
            warnings=warnings)
