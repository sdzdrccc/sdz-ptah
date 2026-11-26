const $ = (id) => document.getElementById(id);
const state = {
  meta: null,
  models: [],
  modelId: null,
  schemeId: null,
  vaeId: null,
  loras: [],
  dlgKind: "checkpoint",
  dlgDraft: null,
  presets: [],
  ratio: "1:1",
  longEdge: 1024,
  sizeMode: "preset",
  count: 1,
  useRef: true,
  orders: [],
  current: null,
  detail: null,
  refFile: null,
  refUrl: null,
  busy: false,
  timer: null,
  started: 0,
  logged: false,
};

const MAX_PROMPT = 4000;
const HINTS = {
  order_busy: "同一名称正被另一个操作使用，稍后再试。",
  budget_exhausted: "本单预算不足，请减少数量。",
  backend_error: "远端服务调用失败。若出现“续跑未完成”，可点它重试（已花的调用已记账）。",
  not_confirmed: "订单未确认。",
  no_egress_consent: "需要勾选“同意把参考图发往远端服务”。",
  preset_unavailable: "所选模型当前不可用，请换一个。",
  pending_exists: "有未完成的生成，请先“续跑”或“放弃”。",
  capability: "模型能力不满足本次请求（例如参考图数量或尺寸）。",
};
const DEFAULT_REF_INFO = "点击选择参考图<br><small>超过 1 MB 或长边超过 1536 会自动压缩</small>";
const STATE_TEXT = { idle: "未生成", generated: "已完成", generating: "生成中", failed: "失败",
  interrupted: "已中断", confirmed: "已确认" };

// ---------------------------------------------------------------- 工具
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
const enc = encodeURIComponent;

function log(text) {
  const box = $("console");
  if (!state.logged) {
    box.textContent = "";
    state.logged = true;
  }
  box.textContent += `\n[${new Date().toLocaleTimeString()}] ${text}`;
  box.scrollTop = box.scrollHeight;
}

function notice(text, kind) {
  const el = $("formNotice");
  el.hidden = !text;
  if (!text) return;
  el.className = `notice ${kind || ""}`;
  el.textContent = text;
}

async function api(url, options) {
  let res;
  try {
    res = await fetch(url, options);
  } catch (err) {
    return { ok: false, error: `无法连接本地服务：${err.message || err}` };
  }
  let data;
  try {
    data = await res.json();
  } catch (err) {
    data = { ok: false, error: `HTTP ${res.status}` };
  }
  data._status = res.status;
  return data;
}
const postJson = (url, body) => api(url, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function fmtTime(iso) {
  const m = String(iso || "").match(/(\d{4})-?(\d{2})-?(\d{2})[T -]?(\d{2}):?(\d{2})/);
  return m ? `${m[2]}-${m[3]} ${m[4]}:${m[5]}` : String(iso || "");
}

function badge(text, kind) {
  return `<span class="pill ${kind || ""}">${esc(text)}</span>`;
}

// ---------------------------------------------------------------- 模型
function model() {
  return state.models.find((m) => m.id === state.modelId) || null;
}
function caps() {
  const c = (model() || {}).caps || {};
  return Object.assign({ lora: false, vae: false }, c);
}

function shortName(m) {
  const name = m.model_name || m.label || m.id;
  return String(name).split("/").pop();
}

// ---- VAE / LoRA 目录（网页本地清单，后端尚未接入这两类参数）
const VAE_CATALOG = [
  { id: "whlingb/qwen_image_2.1_vae_bf16", name: "whlingb/qwen_image_2.1_vae_bf16", tags: ["VAE", "Qwen-Image-2.1"], cover: "" },
];
const LORA_CATALOG = [
  { id: "Dave29/Qwen-image-2.1:c1-st6000", name: "Dave29/Qwen-image-2.1: c1-st6000", trigger: "无需触发词", tags: ["LoRA", "Qwen-Image-2.1"], cover: "" },
];

function vaeById(id) {
  return VAE_CATALOG.find((v) => v.id === id) || null;
}

function schemeOf(m) {
  const raw = m.scheme || "其他";
  // 方案只按「接入方式」分：Qwen / MiniMax / 其他都归「魔搭在线」，不按厂商拆。
  return raw.indexOf("魔搭在线") === 0 ? "魔搭在线" : raw;
}

const SCHEME_ORDER = ["魔搭在线", "官方 API", "本机 ComfyUI", "宿主工具"];

function schemes() {
  const map = new Map();
  for (const m of state.models) {
    const k = schemeOf(m);
    if (!map.has(k)) map.set(k, { id: k, total: 0, ok: 0, reasons: [] });
    const s = map.get(k);
    s.total += 1;
    if (m.selectable) s.ok += 1;
    else for (const r of m.reasons || []) if (!s.reasons.includes(r)) s.reasons.push(r);
  }
  const rank = (id) => { const i = SCHEME_ORDER.indexOf(id); return i < 0 ? 99 : i; };
  return [...map.values()].sort((a, b) => rank(a.id) - rank(b.id));
}

function schemeSummary(s) {
  const items = state.models.filter((m) => schemeOf(m) === s.id);
  const ok = items.filter((m) => m.selectable).length;
  const cur = model();
  const curLine = cur && items.some((m) => m.id === cur.id) ? ` · 当前：${shortName(cur)}` : "";
  return `共 ${items.length} 个模型，可用 ${ok} 个${curLine}`;
}

function renderSchemes() {
  const box = $("schemeList");
  const list = schemes();
  if (!list.length) {
    box.innerHTML = '<p class="small">没有可用方案（常见原因：未设置魔搭 token）。</p>';
    renderSchemeDetail();
    return;
  }
  box.innerHTML = list.map((s) => `
    <button type="button" class="scheme ${s.id === state.schemeId ? "active" : ""}" data-scheme="${esc(s.id)}"
      title="${esc(schemeSummary(s))}" aria-pressed="${s.id === state.schemeId}">
      <span class="scheme-name">${esc(s.id)}</span>
      <span class="scheme-meta">${s.total} 个模型 · ${s.ok ? "可用 " + s.ok : "当前不可用"}</span>
    </button>`).join("");
  renderSchemeDetail();
}

function renderSchemeDetail() {
  const box = $("schemeDetail");
  const id = state.schemeId;
  if (!id) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  const s = schemes().find((x) => x.id === id);
  const notes = [];
  if (s && !s.ok) notes.push("这个方案下的模型当前都不可用。");
  if (id === "宿主工具") notes.push("宿主工具只能在 Agnes / TRAE 的 agent 会话里执行，网页只能生成调用单。");
  if (id === "本机 ComfyUI") notes.push("需要本机 ComfyUI 服务已启动，且工作流已导出。");
  // 模型数量与「当前：xxx」改为悬停在方案卡上看（title），这里只保留确实需要常驻的提示；
  // 没有提示就整块不占位，避免方案区多出空行。
  if (!notes.length) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.hidden = false;
  box.innerHTML = notes.map((x) => `<p class="scheme-note">${esc(x)}</p>`).join("");
}

function renderModelList() { /* 模型区改为 renderModelArea */ }

function renderModelArea() {
  const m = model();
  const cover = $("ckptCover");
  if (m && m.cover) {
    cover.src = m.cover;
    cover.hidden = false;
    $("ckptCoverFallback").hidden = true;
  } else {
    cover.hidden = true;
    $("ckptCoverFallback").hidden = false;
    $("ckptCoverFallback").textContent = m ? shortName(m).slice(0, 14) : "无模型";
  }
  $("ckptSlot").textContent = m ? (m.model_name || m.id) : "未选择";
  $("ckptSlot").className = `slot-val ${m ? "" : "empty"}`;
  const vae = vaeById(state.vaeId);
  $("vaeSlot").textContent = vae ? vae.name : "使用模型自带 VAE";
  $("vaeSlot").className = `slot-val ${vae ? "" : "empty"}`;

  const list = $("loraList");
  if (!state.loras.length) {
    // 空态不写占位文案：这一格只留下面的「＋ 添加 LoRA」按钮，省一行且不啰嗦
    list.innerHTML = "";
  } else {
    list.innerHTML = state.loras.map((x, k) => `
      <div class="lora-card">
        <div class="thumb"></div>
        <div><div class="nm">${esc(x.name)}</div><div class="sub">${esc(x.trigger || "无需触发词")}</div></div>
        <input type="number" step="0.05" min="0" max="2" value="${esc(x.weight)}" data-lora-w="${k}" aria-label="权重">
        <button type="button" class="icon rm" data-lora-rm="${k}" aria-label="移除">✕</button>
      </div>`).join("");
  }
  $("addLoraBtn").disabled = !caps().lora;
  $("addLoraBtn").title = caps().lora ? "" : "当前模型不支持 LoRA";
}

function pickModelForScheme(id) {
  const inScheme = state.models.filter((m) => schemeOf(m) === id);
  // 优先用配置里的默认模型（meta.default_model），而不是注册表里排第一的那个；
  // 否则切回「魔搭在线」会永远落在注册表第一项上，不跟配置走。
  const wanted = (state.meta || {}).default_model;
  const preferred = wanted && inScheme.find((m) => m.id === wanted && m.selectable);
  return preferred || inScheme.find((m) => m.selectable) || inScheme[0] || null;
}

function selectScheme(id) {
  state.schemeId = id;
  const cur = model();
  if (!cur || schemeOf(cur) !== id) {
    const pick = pickModelForScheme(id);
    if (pick) {
      state.modelId = pick.id;
      applyModelDefaults();
    }
  }
  renderAll();
}

function renderModelInfo() {
  // 模型摘要已并入"模型管理"区（renderModelArea），这里只更新能力说明与不可用原因
  const m = model();
  if (!m) {
    $("modelCaps").textContent = "当前没有可用模型（常见原因：未设置魔搭 token）。";
    $("modelReasons").hidden = true;
    return;
  }
  const c = m.caps || {};
  const yes = [];
  const no = [];
  (c.negative_prompt ? yes : no).push("负向提示词");
  (c.seed ? yes : no).push("随机种子");
  (c.steps ? yes : no).push("采样步数");
  (c.guidance ? yes : no).push("CFG");
  if (c.reference_max) yes.push(`参考图（最多 ${c.reference_max} 张）`);
  else no.push("参考图");
  no.push("LoRA", "VAE");
  const tags = [m.data_egress ? "外发" : "", m.commercial_use !== true ? "非商用/待确认" : "",
    m.verified ? "已实测" : "未实测", m.credits_per_image ? `${m.credits_per_image} 魔粒/次` : ""].filter(Boolean);
  $("modelCaps").textContent = `${m.model_name || m.id}（${tags.join(" · ")}）· 支持：${yes.join("、") || "无"}`
    + (no.length ? ` · 不支持：${no.join("、")}` : "");
  const reasons = m.reasons || [];
  $("modelReasons").hidden = !reasons.length;
  $("modelReasons").innerHTML = reasons.map((r) => `<li>${esc(r)}</li>`).join("");
}

function applyModelDefaults() {
  const m = model();
  if (!m) return;
  const d = m.defaults || {};
  const c = m.caps || {};
  if (c.steps) setPair("steps", d.steps ?? 30);
  if (c.guidance) setPair("guidance", d.guidance ?? 4);
  const sizes = c.sizes || [1024];
  if (!sizes.includes(state.longEdge)) state.longEdge = sizes[0];
  if (!c.reference_max) state.useRef = false;
}

function selectModel(id, { keepParams = false } = {}) {
  const m = state.models.find((x) => x.id === id);
  if (!m) return;
  state.modelId = id;
  state.schemeId = schemeOf(m);
  if (!keepParams) applyModelDefaults();
  renderAll();
}

async function refreshModels() {
  const res = await api("/api/models");
  state.models = res.models || [];
  const wanted = state.modelId || (state.meta || {}).default_model;
  const pick = state.models.find((m) => m.id === wanted && m.selectable)
    || state.models.find((m) => m.selectable) || null;
  state.modelId = pick ? pick.id : null;
  state.schemeId = pick ? schemeOf(pick) : (state.models[0] ? schemeOf(state.models[0]) : null);
  if (pick) state.useRef = !!pick.caps.reference_max;
  applyModelDefaults();
}

// ---------------------------------------------------------------- 参数
function setPair(name, value) {
  $(`${name}Input`).value = value;
  $(`${name}Range`).value = value;
}

function seedValue() {
  const v = parseInt($("seedInput").value, 10);
  return Number.isFinite(v) ? v : -1;
}

function round8(v) {
  return Math.max(512, Math.min(2048, Math.round(v / 8) * 8));
}

function currentSize() {
  if (state.sizeMode === "custom") {
    return [round8(Number($("customW").value) || 1024), round8(Number($("customH").value) || 1024)];
  }
  const r = ((state.meta || {}).ratios || []).find((x) => x.id === state.ratio) || { w: 1, h: 1 };
  const L = state.longEdge;
  return r.w >= r.h ? [round8(L), round8(L * r.h / r.w)] : [round8(L * r.w / r.h), round8(L)];
}

function renderParams() {
  const c = caps();
  $("guidanceRow").classList.toggle("disabled-row", !c.guidance);
  $("stepsRow").classList.toggle("disabled-row", !c.steps);
  for (const id of ["guidanceInput", "guidanceRange"]) $(id).disabled = !c.guidance;
  for (const id of ["stepsInput", "stepsRange"]) $(id).disabled = !c.steps;
  const off = [];
  if (!c.steps) off.push("采样步数");
  if (!c.guidance) off.push("CFG");
  $("paramNote").hidden = !off.length || !model();
  $("paramNote").textContent = `当前模型只能用默认的${off.join("、")}，不会发送这些参数。`;
  $("seedInput").disabled = !c.seed;
  $("seedDice").disabled = !c.seed;
  $("seedReset").disabled = !c.seed;
  $("seedNote").textContent = c.seed ? "-1 表示每次随机；固定数值可复现同一编号的图" : "当前模型不支持随机种子";

  const choices = (state.meta || {}).count_choices || [1, 2, 4];
  $("countSeg").innerHTML = choices.map((n) => `
    <button type="button" data-count="${n}" class="${n === state.count ? "active" : ""}"
      role="radio" aria-checked="${n === state.count}">${n}</button>`).join("");

  const neg = !!c.negative_prompt || !model();
  $("negativeInput").disabled = !neg;
  $("negativeNote").hidden = neg;
}

function renderSize() {
  const m = state.meta || {};
  const preset = state.sizeMode === "preset";
  $("sizeTabPreset").classList.toggle("active", preset);
  $("sizeTabCustom").classList.toggle("active", !preset);
  $("sizePreset").hidden = !preset;
  $("sizeCustom").hidden = preset;

  const verified = m.verified_sizes || ["1024x1024"];
  $("ratioSeg").innerHTML = (m.ratios || []).map((r) => `
    <button type="button" data-ratio="${esc(r.id)}" class="${r.id === state.ratio ? "active" : ""}"
      role="radio" aria-checked="${r.id === state.ratio}" title="${esc(r.label)}">${esc(r.id)}</button>`).join("");
  const edges = (caps().sizes && caps().sizes.length) ? caps().sizes : [1024];
  $("edgeSeg").innerHTML = edges.map((e) => `
    <button type="button" data-edge="${e}" class="${e === state.longEdge ? "active" : ""}"
      role="radio" aria-checked="${e === state.longEdge}">${e}</button>`).join("");

  const [w, h] = currentSize();
  const r = (m.ratios || []).find((x) => x.id === state.ratio) || { w: 1, h: 1 };
  const box = 76;
  const sw = r.w >= r.h ? box : Math.round(box * r.w / r.h);
  const sh = r.w >= r.h ? Math.round(box * r.h / r.w) : box;
  $("ratioShape").style.width = `${sw}px`;
  $("ratioShape").style.height = `${sh}px`;
  $("ratioText").textContent = state.ratio;
  $("sizeText").textContent = `${w}×${h}`;
  const id = `${w}x${h}`;
  const inCaps = edges.includes(Math.max(w, h));
  $("sizeNote").textContent = verified.includes(id)
    ? `${w}×${h} 已有真实出图记录`
    : `${w}×${h} 未实测${inCaps ? "" : "，且不在该模型常用尺寸内"}，远端可能不接受或自动改尺寸`;
  $("sizeNote").style.color = verified.includes(id) ? "" : "var(--amber)";
}

function renderRef() {
  const c = caps();
  const m = model();
  const can = !!c.reference_max;
  if (!can) state.useRef = false;
  $("useRefToggle").checked = state.useRef;
  $("useRefToggle").disabled = !can;
  $("useRefText").textContent = !can ? "该模型不支持" : (state.useRef ? "启用" : "关闭");
  $("refArea").hidden = !state.useRef;
  $("refOff").hidden = state.useRef;
  $("refOff").textContent = can
    ? "已关闭：本次按文生图生成，不上传参考图。"
    : "当前模型不接收参考图，只能文生图。";
  $("refLimit").textContent = can
    ? `该模型最多接收 ${c.reference_max} 张参考图；本页一次上传 1 张，作为身份参考。`
    : "";
  $("egressRow").hidden = !(m && m.data_egress);
}

function renderCost() {
  const m = model();
  const budget = (state.meta || {}).simple_budget || 10;
  const calls = state.count;
  const credits = m && m.credits_per_image ? ` · 约 ${calls * m.credits_per_image} 魔粒` : "";
  $("costLine").textContent = `预计 ${calls} 次调用${credits}`;
  $("costLine").style.color = calls > budget ? "#ffd2cc" : "";
  const ok = !!(m && m.selectable);
  $("generateButton").disabled = state.busy || !ok;
  $("dryRunButton").disabled = state.busy || !ok;
  $("goLabel").textContent = state.busy ? "处理中…" : "开始生图";
}

function renderCounters() {
  for (const [id, cid] of [["positiveInput", "positiveCount"], ["negativeInput", "negativeCount"]]) {
    const n = $(id).value.length;
    $(cid).textContent = `${n} / ${MAX_PROMPT}`;
    $(cid).classList.toggle("over", n > MAX_PROMPT);
  }
}

function renderAll() {
  renderSchemes();
  renderModelArea();
  renderModelInfo();
  renderParams();
  renderSize();
  renderRef();
  renderCost();
  renderCounters();
}

// ---------------------------------------------------------------- 顶栏 / 参考图
function renderMeta() {
  const m = state.meta || {};
  $("metaLine").textContent = `订单目录 ${m.orders_root || "-"}`;
  const pill = $("tokenPill");
  pill.className = `pill ${m.modelscope_token_set ? "ok" : "bad"}`;
  pill.textContent = m.modelscope_token_set ? "魔搭 token 已设置" : "未检测到魔搭 token";
}

async function refreshMeta() {
  const res = await api("/api/meta");
  if (!res.ok) throw new Error(res.error || "读取项目信息失败");
  state.meta = res.meta;
  renderMeta();
}

function setRefFile(file) {
  if (state.refUrl) URL.revokeObjectURL(state.refUrl);
  state.refFile = file || null;
  state.refUrl = file ? URL.createObjectURL(file) : null;
  $("refPreview").hidden = !file;
  if (file) $("refPreview").src = state.refUrl;
  $("refInfo").innerHTML = file
    ? `${esc(file.name)}<br><small>${(file.size / 1024).toFixed(0)} KB · 点击更换</small>`
    : DEFAULT_REF_INFO;
}

// 默认保存名称：按本地日期生成，避免每单都落进同一个写死的 avatar 目录
function defaultName() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `avatar-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}`;
}

function resetForm() {
  state.current = null;
  state.detail = null;
  $("nameInput").value = defaultName();
  $("positiveInput").value = "";
  $("negativeInput").value = "";
  $("seedInput").value = -1;
  $("egressConsent").checked = false;
  state.count = 1;
  state.ratio = "1:1";
  state.sizeMode = "preset";
  state.useRef = !!caps().reference_max;
  applyModelDefaults();
  setRefFile(null);
  notice("");
  renderOrders();
  renderAll();
  renderDetail();
  $("positiveInput").focus();
}

// ---------------------------------------------------------------- 历史
async function refreshOrders() {
  const res = await api("/api/orders");
  state.orders = (res.orders || []).slice().sort((a, b) =>
    String(b.order_id || "").localeCompare(String(a.order_id || "")));
  renderOrders();
}

function renderOrders() {
  const box = $("orderList");
  if (!state.orders.length) {
    box.innerHTML = '<p class="small">还没有订单。</p>';
    return;
  }
  box.innerHTML = state.orders.map((o) => `
    <button type="button" class="order-item ${o.name === state.current ? "active" : ""}" data-name="${esc(o.name)}">
      ${esc(o.character_id || o.name)}
      <small>${esc(fmtTime(o.order_id))} · ${esc(o.files)} 张 · ${esc(STATE_TEXT[o.state] || o.state || "")}</small>
    </button>`).join("");
}

function sizeFromRender(text) {
  const m = String(text || "").match(/^(\d+)x(\d+)$/);
  if (!m) return;
  const w = +m[1];
  const h = +m[2];
  const L = Math.max(w, h);
  const ratios = (state.meta || {}).ratios || [];
  const hit = ratios.find((r) => {
    const sw = r.w >= r.h ? round8(L) : round8(L * r.w / r.h);
    const sh = r.w >= r.h ? round8(L * r.h / r.w) : round8(L);
    return sw === w && sh === h;
  });
  if (hit && (caps().sizes || [1024]).includes(L)) {
    state.sizeMode = "preset";
    state.ratio = hit.id;
    state.longEdge = L;
  } else {
    state.sizeMode = "custom";
    $("customW").value = w;
    $("customH").value = h;
  }
}

async function fillFormFromOrder(d) {
  const b = d.brief || {};
  const gen = b.generation || {};
  const ui = b.ui_settings || {};
  $("nameInput").value = b.character_id || d.name;
  const backend = ui.model || ((gen.stages || [])[0] || {}).backend || gen.backend;
  if (backend && state.models.some((m) => m.id === backend && m.selectable)) {
    state.modelId = backend;
  }
  const params = ((gen.stages || [])[0] || {});
  if (caps().steps) setPair("steps", ui.steps ?? params.steps ?? (model() || {}).defaults?.steps ?? 30);
  if (caps().guidance) setPair("guidance", ui.guidance ?? params.guidance ?? (model() || {}).defaults?.guidance ?? 4);
  $("seedInput").value = gen.seed ?? ui.seed ?? -1;
  const pi = b.prompts_input;
  if (pi) {
    $("positiveInput").value = pi.positive || "";
    $("negativeInput").value = pi.negative || "";
  }
  if (b.count) state.count = b.count;
  if (b.render_size) sizeFromRender(b.render_size);
  state.useRef = gen.mode !== "text2img" && !!caps().reference_max;
  renderAll();
  const ref = (gen.reference_images || [])[0];
  if (pi && ref && ref.path) {
    try {
      const res = await fetch(`/api/orders/${enc(d.name)}/file/${enc(ref.path)}`);
      if (res.ok) {
        const blob = await res.blob();
        setRefFile(new File([blob], ref.path, { type: blob.type || "image/png" }));
      }
    } catch (err) {
      log(`参考图读取失败：${err.message || err}`);
    }
  }
}

async function selectOrder(name, opts = {}) {
  state.current = name;
  renderOrders();
  const res = await api(`/api/orders/${enc(name)}`);
  if (!res.ok) {
    log(`读取订单失败：${res.error}`);
    return;
  }
  state.detail = res.detail;
  if (!opts.keepForm) await fillFormFromOrder(res.detail);
  renderDetail();
}

// ---------------------------------------------------------------- 结果
function modelLabel(id) {
  const m = state.models.find((x) => x.id === id);
  return m ? shortName(m) : (id || "-");
}

function isResultImage(name) {
  const low = name.toLowerCase();
  return !low.includes("-ref-") && !low.includes("-upload-") && !low.endsWith("-sheet.jpg")
    && low !== "contact_sheet.jpg";
}

function renderDetail() {
  const d = state.detail;
  if (!d) {
    $("resultHead").innerHTML = "<b>还没有结果</b><span>生成后图片显示在这里</span>";
    $("gallery").innerHTML = '<p class="empty">生成的图片会显示在这里</p>';
    $("facts").innerHTML = "";
    $("resumeButton").hidden = true;
    $("discardButton").hidden = true;
    return;
  }
  const b = d.brief || {};
  const gen = b.generation || {};
  const backend = ((gen.stages || [])[0] || {}).backend || gen.backend;
  const seed = gen.seed != null ? ` · seed ${gen.seed}` : "";
  $("resultHead").innerHTML = `<b>${esc(d.name)} · ${esc(d.order_id || "-")}</b>
    <span>${esc(modelLabel(backend))} · ${esc(b.render_size || "1024x1024")}${esc(seed)}</span>`;

  const files = (d.files || []).filter((f) => isResultImage(f.name));
  $("gallery").innerHTML = files.length ? files.map((f) => {
    const url = `/api/orders/${enc(state.current)}/file/${enc(f.name)}`;
    return `<figure><a href="${url}" target="_blank" rel="noopener" title="新窗口查看原图">
      <img src="${url}" alt="${esc(f.name)}" loading="lazy"></a>
      <figcaption>${esc(f.name)}</figcaption></figure>`;
  }).join("") : '<p class="empty">这个订单还没有生成图片</p>';

  const refs = gen.reference_images || [];
  const pi = b.prompts_input || {};
  const st = (gen.stages || [])[0] || {};
  const rows = [
    ["状态", `${STATE_TEXT[d.status.state] || d.status.state || "未生成"}${d.status.phase ? ` / ${d.status.phase}` : ""}`],
    ["调用次数", `${d.budget.used} / ${d.budget.limit}`],
    ["参数", [st.steps != null ? `步数 ${st.steps}` : "", st.guidance != null ? `CFG ${st.guidance}` : ""]
      .filter(Boolean).join(" · ") || "模型默认"],
    ["参考图", gen.mode === "text2img" ? "无（文生图）" : (refs.map((r) => r.path).join("、") || "无")],
    ["提示词", b.prompt_source === "manual"
      ? `手写 · 正向 ${(pi.positive || "").length} 字 · 负向 ${(pi.negative || "").length} 字`
      : "旧流程编译"],
  ];
  if (d.status.last_error) rows.push(["最近错误", d.status.last_error]);
  $("facts").innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
  $("resumeButton").hidden = !d.pending;
  $("discardButton").hidden = !d.pending;
}

// ---------------------------------------------------------------- 进度
function startProgress(label) {
  state.started = Date.now();
  const el = $("progress");
  el.hidden = false;
  const tick = () => {
    const sec = Math.floor((Date.now() - state.started) / 1000);
    el.textContent = `${label}中 · 已等待 ${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`
      + (label === "生成" ? " · 每张通常 30 秒到 3 分钟，请不要关闭页面" : "");
  };
  tick();
  clearInterval(state.timer);
  state.timer = setInterval(tick, 1000);
}

function stopProgress() {
  clearInterval(state.timer);
  state.timer = null;
  $("progress").hidden = true;
}

function setBusy(flag) {
  state.busy = flag;
  $("resumeButton").disabled = flag;
  $("discardButton").disabled = flag;
  renderCost();
}

const VAGUE_HINT = "提示词里有不允许的模糊肤色词（如“小麦色”“日晒黑”“古铜色”）。"
  + "请换成具体色值，例如：浅暖蜜桃色 #EBBDA0（色相 23°、饱和度 32%、明度 92%）。";

function explain(res) {
  const detail = res.error || (res.errors || []).join("；")
    || (res.stderr || res.stdout || "").trim().slice(-300);
  let hint = HINTS[res.code] || "";
  if (/模糊色词|提示词审计失败/.test(detail)) hint = VAGUE_HINT;
  return [hint, detail].filter(Boolean).join(" ");
}

function reportResult(res, label) {
  if (res.autofix && res.autofix.length) {
    log(`已自动修正提示词：${res.autofix.join("；")}`);
    if (res.positive) $("positiveInput").value = res.positive;
    if (res.negative != null) $("negativeInput").value = res.negative;
    renderCounters();
  }
  if (res.stdout) log(String(res.stdout).trim());
  if (res.stderr) log(String(res.stderr).trim());
  for (const w of res.warnings || []) log(`提示：${w}`);
  if (res.ok) {
    const fixed = res.autofix && res.autofix.length ? `（已自动修正 ${res.autofix.length} 处模糊肤色词）` : "";
    notice(`${label}完成。${fixed}`, "ok");
    log(`${label}完成`);
  } else {
    const text = explain(res) || "未知错误";
    notice(`${label}未成功：${text}`, "error");
    log(`${label}未成功（${res.code || "error"}）：${text}`);
  }
}

// ---------------------------------------------------------------- 提交
async function submit(dry) {
  if (state.busy) return;
  const m = model();
  const c = caps();
  const positive = $("positiveInput").value.trim();
  const problems = [];
  if (!m || !m.selectable) problems.push("请先选择一个可用模型");
  if (!positive) problems.push("请填写正向提示词");
  if (state.useRef && !state.refFile) problems.push("请选择参考图，或关闭“图片编辑 / 图生图”");
  if (state.useRef && m && m.data_egress && !$("egressConsent").checked) {
    problems.push("请勾选“同意把参考图发往远端服务”");
  }
  if (problems.length) {
    notice(problems.join("；"), "error");
    return;
  }
  notice("");
  const [w, h] = currentSize();
  const label = dry ? "校验" : "生成";
  setBusy(true);
  startProgress(label);
  log(`开始${label}…（${shortName(m)} · ${w}×${h} · ${state.count} 张）`);
  try {
    const payload = {
      name: $("nameInput").value.trim() || defaultName(),
      positive,
      negative: c.negative_prompt ? $("negativeInput").value.trim() : "",
      model: m.id,
      size: `${w}x${h}`,
      count: state.count,
      seed: c.seed ? seedValue() : -1,
      steps: c.steps ? $("stepsInput").value : "",
      guidance: c.guidance ? $("guidanceInput").value : "",
      use_reference: state.useRef,
      egress_consent: $("egressConsent").checked,
      dry_run: dry,
      channel: "web",
    };
    if (state.useRef && state.refFile) {
      payload.reference = { name: state.refFile.name, data: await fileToDataUrl(state.refFile) };
    }
    const res = await postJson("/api/generate", payload);
    reportResult(res, label);
    await refreshOrders();
    if (res.order) await selectOrder(res.order, { keepForm: true });
  } catch (err) {
    notice(`${label}出错：${err.message || err}`, "error");
    log(`${label}出错：${err.message || err}`);
  } finally {
    stopProgress();
    setBusy(false);
  }
}

async function continueRun(options, label) {
  if (!state.current || state.busy) return;
  setBusy(true);
  startProgress(label);
  log(`开始${label}…`);
  try {
    const res = await postJson(`/api/orders/${enc(state.current)}/generate`, options);
    reportResult(res, label);
    if (res.detail) state.detail = res.detail;
    renderDetail();
    await refreshOrders();
  } finally {
    stopProgress();
    setBusy(false);
  }
}

// ---------------------------------------------------------------- 事件
function bindPair(name) {
  $(`${name}Range`).addEventListener("input", (e) => { $(`${name}Input`).value = e.target.value; });
  $(`${name}Input`).addEventListener("input", (e) => { $(`${name}Range`).value = e.target.value; });
}

// ---------------------------------------------------------------- 选择弹窗
function openDialog() {
  state.dlgDraft = {
    checkpoint: state.modelId,
    vae: state.vaeId,
    loras: state.loras.map((x) => ({ ...x })),
  };
  state.dlgKind = "checkpoint";
  $("dlgScheme").innerHTML = '<option value="">全部方案</option>' + schemes().map((s) =>
    `<option value="${esc(s.id)}" ${s.id === state.schemeId ? "selected" : ""}>${esc(s.id)}（${s.total}，${s.ok ? "可用 " + s.ok : "当前不可用"}）</option>`).join("");
  renderSchemeNote();
  $("dlgSearch").value = "";
  $("pickDlg").hidden = false;
  renderDialog();
  $("dlgSearch").focus();
}

function closeDialog(commit) {
  if (commit && state.dlgDraft) {
    const d = state.dlgDraft;
    if (d.checkpoint) selectModel(d.checkpoint, { keepParams: false });
    state.vaeId = d.vae;
    state.loras = d.loras;
    renderAll();
    log(`已更新模型：${shortName(model() || {})}${state.vaeId ? " · VAE" : ""}${state.loras.length ? ` · LoRA×${state.loras.length}` : ""}`);
  }
  state.dlgDraft = null;
  $("pickDlg").hidden = true;
}

function renderSchemeNote() {
  const id = $("dlgScheme").value;
  const note = $("dlgSchemeNote");
  if (!id) { note.hidden = true; return; }
  const s = schemes().find((x) => x.id === id);
  if (!s) { note.hidden = true; return; }
  const lines = [];
  if (!s.ok) lines.push("这个方案下的模型当前都不可用：");
  for (const r of s.reasons) lines.push(r);
  if (id === "宿主工具") lines.push("宿主工具只能在 Agnes / TRAE 的 agent 会话里执行，网页只能生成调用单。");
  if (id === "本机 ComfyUI") lines.push("需要本机 ComfyUI 已启动，且工作流已导出；两段式方案暂不支持单段页面。");
  note.hidden = !lines.length;
  note.innerHTML = lines.map((x) => `<span>${esc(x)}</span>`).join("<br>");
}

function dlgItems() {
  const kind = state.dlgKind;
  const scheme = kind === "checkpoint" ? $("dlgScheme").value : "";
  const q = $("dlgSearch").value.trim().toLowerCase();
  let items;
  if (kind === "checkpoint") {
    items = state.models.filter((m) => !scheme || schemeOf(m) === scheme).map((m) => ({
      key: m.id, name: m.model_name || m.id,
      tags: [m.selectable ? "" : "不可用", m.verified ? "已实测" : "未实测"].filter(Boolean),
      enabled: m.selectable, reason: (m.reasons || [])[0] || "", cover: m.cover || "", kind: "checkpoint",
    }));
  } else if (kind === "vae") {
    items = VAE_CATALOG.map((v) => ({ key: v.id, name: v.name, tags: v.tags, enabled: true, cover: v.cover, kind: "vae" }));
  } else {
    items = LORA_CATALOG.map((x) => ({ key: x.id, name: x.name, tags: x.tags, enabled: true, cover: x.cover, kind: "lora" }));
  }
  return items.filter((x) => !q || x.name.toLowerCase().includes(q));
}

function renderDialog() {
  const kind = state.dlgKind;
  document.querySelectorAll(".dlg-tabs .tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.kind === kind));
  $("dlgScheme").hidden = kind !== "checkpoint";
  const items = dlgItems();
  const d = state.dlgDraft || {};
  const picked = (x) => (kind === "checkpoint" ? d.checkpoint === x.key
    : kind === "vae" ? d.vae === x.key
    : (d.loras || []).some((l) => l.id === x.key));
  $("dlgGrid").innerHTML = items.length ? items.map((x) => `
    <button type="button" class="dlg-card ${picked(x) ? "picked" : ""}" data-key="${esc(x.key)}"
      ${x.enabled ? "" : "disabled"} title="${esc(x.reason || "")}">
      <div class="art"><span class="tg">${esc(x.kind === "checkpoint" ? "Checkpoint" : x.kind.toUpperCase())}</span></div>
      <div class="cap"><b>${esc(x.name)}</b>
        <span class="small">${esc(x.tags.join(" · "))}</span>
        ${x.reason ? `<span class="why">${esc(x.reason)}</span>` : ""}</div>
    </button>`).join("") : '<p class="small">没有匹配的模型。</p>';
  renderSelected();
}

function renderSelected() {
  const d = state.dlgDraft || {};
  const m = state.models.find((x) => x.id === d.checkpoint);
  const vae = vaeById(d.vae);
  const parts = [];
  parts.push(`<h3>Checkpoint</h3>${m ? `<div class="sel-item"><div class="thumb"></div>
    <div><div class="nm">${esc(m.model_name || m.id)}</div><div class="sub">${esc(schemeOf(m))}</div></div>
    <button type="button" class="icon rm" data-clear="checkpoint">✕</button></div>` : '<p class="small">未选择</p>'}`);
  parts.push(`<h3>VAE</h3>${vae ? `<div class="sel-item"><div class="thumb"></div>
    <div><div class="nm">${esc(vae.name)}</div><div class="sub">VAE</div></div>
    <button type="button" class="icon rm" data-clear="vae">✕</button></div>` : '<p class="small">未选择（使用模型自带 VAE）</p>'}`);
  parts.push(`<h3>LoRA</h3>${(d.loras || []).length ? d.loras.map((l, k) => `<div class="sel-item"><div class="thumb"></div>
    <div><div class="nm">${esc(l.name)}</div><div class="sub">${esc(l.trigger || "")}</div></div>
    <button type="button" class="icon rm" data-clear="lora" data-k="${k}">✕</button></div>`).join("") : '<p class="small">未选择</p>'}`);
  $("dlgSelected").innerHTML = parts.join("");
}

function bindDialog() {
  $("pickModelBtn").addEventListener("click", openDialog);
  $("dlgClose").addEventListener("click", () => closeDialog(false));
  $("dlgCancel").addEventListener("click", () => closeDialog(false));
  $("dlgDone").addEventListener("click", () => closeDialog(true));
  $("pickDlg").addEventListener("click", (e) => { if (e.target.id === "pickDlg") closeDialog(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("pickDlg").hidden) closeDialog(false); });
  document.querySelectorAll(".dlg-tabs .tab").forEach((b) => b.addEventListener("click", () => {
    state.dlgKind = b.dataset.kind;
    $("dlgSearch").value = "";
    renderDialog();
  }));
  $("dlgScheme").addEventListener("change", () => { renderSchemeNote(); renderDialog(); });
  $("dlgSearch").addEventListener("input", renderDialog);
  $("dlgGrid").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-key]");
    if (!btn || btn.disabled) return;
    const d = state.dlgDraft;
    const key = btn.dataset.key;
    if (state.dlgKind === "checkpoint") {
      d.checkpoint = key;
    } else if (state.dlgKind === "vae") {
      d.vae = d.vae === key ? null : key;
    } else {
      const k = d.loras.findIndex((l) => l.id === key);
      if (k >= 0) d.loras.splice(k, 1);
      else {
        const x = LORA_CATALOG.find((l) => l.id === key);
        if (d.loras.length >= 4) { notice("LoRA 最多选 4 个", "error"); return; }
        d.loras.push({ id: x.id, name: x.name, trigger: x.trigger, weight: 1.0 });
      }
    }
    renderDialog();
  });
  $("dlgSelected").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-clear]");
    if (!btn) return;
    const d = state.dlgDraft;
    const kind = btn.dataset.clear;
    if (kind === "checkpoint") d.checkpoint = null;
    else if (kind === "vae") d.vae = null;
    else d.loras.splice(Number(btn.dataset.k), 1);
    renderDialog();
  });
  $("ckptSlot").addEventListener("click", openDialog);
  $("loraList").addEventListener("click", (e) => {
    const rm = e.target.closest("[data-lora-rm]");
    if (rm) { state.loras.splice(Number(rm.dataset.loraRm), 1); renderAll(); }
  });
  $("loraList").addEventListener("change", (e) => {
    const w = e.target.closest("[data-lora-w]");
    if (w) state.loras[Number(w.dataset.loraW)].weight = Number(w.value) || 1;
  });
  $("addLoraBtn").addEventListener("click", () => {
    if (!caps().lora) return;
    openDialog();
    state.dlgKind = "lora";
    renderDialog();
  });
}

function bindEvents() {
  bindDialog();
  bindSchemes();
  $("genForm").addEventListener("submit", (e) => {
    e.preventDefault();
    submit(false);
  });
  $("dryRunButton").addEventListener("click", () => submit(true));
  $("newButton").addEventListener("click", resetForm);
  $("historyButton").addEventListener("click", () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
  $("resumeButton").addEventListener("click", () => continueRun({ resume: true }, "续跑"));
  $("discardButton").addEventListener("click", () => continueRun({ discard_pending: true }, "放弃"));


  $("positiveInput").addEventListener("input", renderCounters);
  $("negativeInput").addEventListener("input", renderCounters);
  bindPair("steps");
  bindPair("guidance");
  $("seedDice").addEventListener("click", () => {
    $("seedInput").value = Math.floor(Math.random() * 2147483647);
  });
  $("seedReset").addEventListener("click", () => { $("seedInput").value = -1; });
  $("countSeg").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-count]");
    if (!btn) return;
    state.count = Number(btn.dataset.count);
    renderParams();
    renderCost();
  });
  $("sizeTabPreset").addEventListener("click", () => { state.sizeMode = "preset"; renderSize(); });
  $("sizeTabCustom").addEventListener("click", () => {
    const [w, h] = currentSize();
    $("customW").value = w;
    $("customH").value = h;
    state.sizeMode = "custom";
    renderSize();
  });
  $("ratioSeg").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-ratio]");
    if (!btn) return;
    state.ratio = btn.dataset.ratio;
    renderSize();
  });
  $("edgeSeg").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-edge]");
    if (!btn) return;
    state.longEdge = Number(btn.dataset.edge);
    renderSize();
  });
  $("customW").addEventListener("change", renderSize);
  $("customH").addEventListener("change", renderSize);
  $("useRefToggle").addEventListener("change", (e) => {
    state.useRef = e.target.checked;
    renderRef();
  });
  $("referenceFile").addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    if (file) setRefFile(file);
    e.target.value = "";
  });
  $("orderList").addEventListener("click", (e) => {
    const btn = e.target.closest(".order-item");
    if (btn) selectOrder(btn.dataset.name);
  });
}

async function init() {
  bindEvents();
  $("nameInput").value = defaultName();
  try {
    await refreshMeta();
    await refreshModels();
    renderAll();
    renderDetail();
    await refreshOrders();
    log("已就绪");
  } catch (err) {
    notice(`初始化失败：${err.message || err}`, "error");
    log(`初始化失败：${err.message || err}`);
  }
}

init();

function bindSchemes() {
  $("schemeList").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-scheme]");
    if (!btn) return;
    selectScheme(btn.dataset.scheme);
    log(`已选方案：${btn.dataset.scheme}`);
  });
  $("schemeDetail").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-model]");
    if (!btn || btn.disabled) return;
    selectModel(btn.dataset.model);
    log(`已选模型：${shortName(model() || {})}`);
  });
}
