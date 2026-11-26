# -*- coding: utf-8 -*-
"""后端适配器：统一数据契约与基类。

只依赖标准库（+ 可选 Pillow 计算尺寸），不依赖 torch / numpy。
设计说明见 design/review/AI动漫头像-后端适配器接口设计.md。
"""

import hashlib
import os
import random
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Optional

SCHEMA = "generation-backends/2.0"
INTERFACE_VERSION = "0.1.0"

# 退出码：4 与 avatar_pipeline.py / confirm_order.py 一致；
# 8、9 避开 rounds.py 已用的 5（预算）/ 6（重复轮次）/ 7（满意度待回填）
EXIT_CAPABILITY = 4      # 能力不满足 / 未同意 / 不得静默回退
EXIT_BUDGET = 5          # 预算不足（与 rounds.py 相同含义）
EXIT_BACKEND = 8         # 后端调用失败（重试后仍失败）
EXIT_NEEDS_AGENT = 9     # 宿主侧适配器：已写出待执行调用，等待宿主执行


# ---------------------------------------------------------------- 数据契约
@dataclass
class ReferenceImage:
    path: str
    role: str = "identity"            # identity / style / outfit / pose
    sha256: Optional[str] = None


@dataclass
class GenerationRequest:
    order_id: str
    prefix: str
    start_index: int                  # 本次第一张图的编号（跨订单连续）
    prompt_zh: str = ""               # original_prompt
    prompt_en: str = ""               # enhanced_prompt / tag 串
    negative: Optional[str] = None
    references: list = field(default_factory=list)   # [ReferenceImage]
    init_image: Optional[str] = None  # 两段式第二段的输入
    denoise: Optional[float] = None   # img2img 强度（旧字段 strength）
    seed: Optional[int] = None
    steps: Optional[int] = None
    guidance: Optional[float] = None
    width: int = 1024
    height: int = 1024
    count: int = 1
    stage: str = "single"             # single / identity / style
    egress_consent: bool = False      # 是否同意把参考图发往远端服务
    extra: dict = field(default_factory=dict)


@dataclass
class GeneratedImage:
    path: str
    index: int
    seed: Optional[int]
    width: Optional[int]
    height: Optional[int]
    sha256: str


@dataclass
class GenerationResult:
    adapter_id: str
    kind: str
    stage: str
    images: list                      # [GeneratedImage]
    calls: int
    params: dict                      # 实际发给后端的参数（不含密钥）
    model: dict                       # {"name","version","quant","sha256"}
    reproducible: bool
    reproducible_reason: str
    license: dict
    env: dict
    timings: dict
    warnings: list = field(default_factory=list)

    def to_manifest(self):
        d = asdict(self)
        d["interface_version"] = INTERFACE_VERSION
        return d


# ---------------------------------------------------------------- 错误
class AdapterError(Exception):
    exit_code = EXIT_BACKEND
    retryable = False
    calls_used = 0        # 失败前本次已花掉的调用数（用于记账）


class CapabilityError(AdapterError):
    exit_code = EXIT_CAPABILITY


class ConsentError(CapabilityError):
    pass


class TransientError(AdapterError):
    retryable = True


class NeedsAgent(AdapterError):
    """宿主侧适配器：Python 不能直接调宿主工具，写出调用单后交还宿主。"""
    exit_code = EXIT_NEEDS_AGENT

    def __init__(self, call_files):
        super().__init__("等待宿主执行：%s" % ", ".join(call_files))
        self.call_files = call_files


# ---------------------------------------------------------------- 能力
@dataclass
class Capabilities:
    seed: bool = False
    negative_prompt: bool = False
    steps: bool = False
    guidance: bool = False
    reference_max: int = 0
    reference_roles: tuple = ()
    init_image: bool = False
    denoise_range: Optional[tuple] = None
    batch_max: int = 1
    sizes: tuple = (1024,)
    stages: tuple = ("single",)
    prompt_style: str = "natural_zh"   # natural_zh / natural_en / booru_tags

    @classmethod
    def from_spec(cls, spec):
        c = spec.get("capabilities") or {}
        rng = c.get("denoise_range")
        return cls(
            seed=bool(c.get("seed")),
            negative_prompt=bool(c.get("negative_prompt")),
            steps=bool(c.get("steps")),
            guidance=bool(c.get("guidance")),
            reference_max=int(c.get("reference_max") or 0),
            reference_roles=tuple(c.get("reference_roles") or ()),
            init_image=bool(c.get("init_image")),
            denoise_range=tuple(rng) if rng else None,
            batch_max=int(c.get("batch_max") or 1),
            sizes=tuple(c.get("sizes") or (1024,)),
            stages=tuple(c.get("stages") or ("single",)),
            prompt_style=c.get("prompt_style") or "natural_zh",
        )


# ---------------------------------------------------------------- 工具
def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def image_size(path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (None, None)


def output_name(prefix, index, ext=".png"):
    """与 SKILL.md 平铺命名一致：<prefix>-avatar-<N>-src.<ext>。"""
    return "%s-avatar-%d-src%s" % (prefix, index, ext)


# ---------------------------------------------------------------- 基类
class Adapter(ABC):
    kind = "executable"   # executable：Python 直接调用；agent_side：交给宿主执行

    def __init__(self, spec):
        self.spec = spec
        self.id = spec["id"]
        self.caps = Capabilities.from_spec(spec)
        self.license = spec.get("license") or {}
        self.model = spec.get("model") or {}
        self.egress = bool(spec.get("data_egress"))
        self.retry = int((spec.get("policy") or {}).get("retry", 1))
        self.backoff_s = float((spec.get("policy") or {}).get("backoff_s", 3))

    # -- 校验：调用前拦截，不得静默降级
    def validate(self, req):
        errors, warnings = [], []
        if self.spec.get("available") is False:
            errors.append("后端 %s 未接入（available=false）" % self.id)
        if req.stage not in self.caps.stages:
            errors.append("后端 %s 不支持阶段 %s（支持 %s）"
                          % (self.id, req.stage, list(self.caps.stages)))
        n = len(req.references)
        if n > self.caps.reference_max:
            errors.append("参考图 %d 张超过后端上限 %d 张" % (n, self.caps.reference_max))
        for r in req.references:
            if self.caps.reference_roles and r.role not in self.caps.reference_roles:
                errors.append("参考图角色 %s 不受支持（支持 %s）"
                              % (r.role, list(self.caps.reference_roles)))
            if not os.path.isfile(r.path):
                errors.append("参考图不存在：%s" % r.path)
            elif r.sha256 and sha256_of(r.path) != r.sha256:
                errors.append("参考图 sha256 与 brief 记录不一致：%s" % r.path)
        if req.init_image and not self.caps.init_image:
            errors.append("后端 %s 不支持 init_image（两段式第二段需要 img2img）" % self.id)
        if req.denoise is not None:
            rng = self.caps.denoise_range
            if rng is None:
                errors.append("后端 %s 没有 denoise 入参，denoise 必须为 null" % self.id)
            elif not (rng[0] <= req.denoise <= rng[1]):
                errors.append("denoise=%s 超出范围 %s" % (req.denoise, list(rng)))
        if req.seed is not None and not self.caps.seed:
            warnings.append("后端 %s 无 seed，传入的 seed 将被忽略，结果不可复现" % self.id)
        if req.negative and not self.caps.negative_prompt:
            warnings.append("后端 %s 无负向通道，排除项需由提示词编译层改写成描述句" % self.id)
        if self.egress and req.references and not req.egress_consent:
            errors.append("后端 %s 会把参考图发往远端服务，需 generation.egress_consent=true"
                          % self.id)
        if max(req.width, req.height) not in self.caps.sizes:
            warnings.append("尺寸 %dx%d 不在后端常用尺寸 %s 内"
                            % (req.width, req.height, list(self.caps.sizes)))
        return errors, warnings

    def check(self, req):
        errors, warnings = self.validate(req)
        if errors:
            raise CapabilityError("；".join(errors))
        return warnings

    # -- 可复现判定：seed + 模型版本 + 环境版本齐全才为 true
    def reproducibility(self, seeds, env):
        if not self.caps.seed:
            return False, "后端无 seed"
        if any(s is None for s in seeds):
            return False, "存在未记录 seed 的图"
        if not (self.model.get("version") or self.model.get("sha256")):
            return False, "模型未锁定版本 / sha256"
        missing = [k for k in ("comfyui_version", "pytorch_version") if not env.get(k)]
        if self.kind == "executable" and self.spec.get("engine") == "comfyui" and missing:
            return False, "环境版本缺失：%s" % ", ".join(missing)
        return True, "seed + 模型版本 + 环境版本齐全"

    def pick_seeds(self, req):
        if not self.caps.seed:
            return [None] * req.count
        base = req.seed if req.seed is not None else random.randint(0, 2 ** 32 - 1)
        return [(base + i) % (2 ** 32) for i in range(req.count)]

    def with_retry(self, fn, *a, **kw):
        last = None
        for attempt in range(self.retry + 1):
            try:
                return fn(*a, **kw)
            except AdapterError as e:
                last = e
                if not e.retryable or attempt == self.retry:
                    raise
                time.sleep(self.backoff_s * (attempt + 1))
        raise last

    # -- 子类实现
    def probe(self):
        """能力 / 可用性检测，返回 {"ok": bool, "detail": ..., "env": {...}}。"""
        return {"ok": self.spec.get("available") is not False, "env": {}}

    @abstractmethod
    def generate(self, req, out_dir):
        """生成 req.count 张图到 out_dir，返回 GenerationResult。"""
