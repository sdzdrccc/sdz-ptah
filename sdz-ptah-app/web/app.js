const $ = (id) => document.getElementById(id);
const state = {
  meta: null,
  presets: [],
  orders: [],
  current: null,
  detail: null,
  selectedPreset: null,
  refs: [],
  uploads: [],
  logged: false,
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function log(text) {
  const box = $("console");
  if (!state.logged) {
    box.textContent = "";
    state.logged = true;
  }
  const stamp = new Date().toLocaleTimeString();
  box.textContent += `\n[${stamp}] ${text}`;
  box.scrollTop = box.scrollHeight;
}

async function api(url, options) {
  const res = await fetch(url, options);
  const data = await res.json();
  if (!res.ok && !data.error) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function enc(value) {
  return encodeURIComponent(value);
}

function fillDatalist(el, items) {
  el.innerHTML = items.map((x) =>
    `<option value="${esc(x.id)}" label="${esc(x.label)}"></option>`).join("");
}

function renderMeta() {
  const m = state.meta || {};
  $("metaLine").textContent =
    `项目 ${m.project_root || "-"} · 订单根 ${m.orders_root || "-"} · 方案 v${m.preset_version || "-"} · ` +
    `ModelScope token ${m.modelscope_token_set ? "已设置（不显示）" : "未检测到"}`;
}

async function refreshMeta() {
  const res = await api("/api/meta");
  state.meta = res.meta;
  const chars = res.meta.characters || [];
  const styles = res.meta.styles || [];
  fillDatalist($("characterOptions"), chars);
  fillDatalist($("styleOptions"), styles);
  const preferredChar = chars.find((x) => x.id === "satoshi") || chars[0];
  const preferredStyle = styles.find((x) => x.id === "tv-anime-cel") || styles[0];
  if (preferredChar && !$("characterInput").value) $("characterInput").value = preferredChar.id;
  if (preferredStyle && !$("styleInput").value) $("styleInput").value = preferredStyle.id;
  renderMeta();
}

async function refreshPresets() {
  const res = await api("/api/presets");
  state.presets = res.presets || [];
  renderPresets();
}

async function refreshOrders() {
  const res = await api("/api/orders");
  state.orders = res.orders || [];
  renderOrders();
}

function renderOrders() {
  const box = $("orderList");
  if (!state.orders.length) {
    box.innerHTML = '<p class="hint">暂无订单。先在上方新建，或把订单放进 orders_root。</p>';
    return;
  }
  box.innerHTML = state.orders.map((o) => `
    <button class="order-item ${o.name === state.current ? "active" : ""}" data-name="${esc(o.name)}">
      ${esc(o.character_id)} · ${esc(o.order_id || "无 order_id")}
      <small>${esc(o.state || "idle")} · ${o.confirmed ? "已确认" : "未确认"} · 预算 ${o.budget.used}/${o.budget.limit}</small>
    </button>`).join("");
}

function badge(text, kind) {
  return `<span class="badge ${kind || ""}">${esc(text)}</span>`;
}

function renderPresets() {
  const box = $("presetList");
  if (!state.presets.length) {
    box.innerHTML = '<p class="hint">没有可用方案，检查 generation-presets.json。</p>';
    return;
  }
  box.innerHTML = state.presets.map((p) => {
    const badges = [];
    badges.push(p.selectable ? badge("可选", "ok") : badge("不可选", "bad"));
    if (p.policy.data_egress) badges.push(badge("外发", "warn"));
    if (p.policy.commercial_use !== true) badges.push(badge("非商用/待确认", "warn"));
    if (!p.policy.verified) badges.push(badge("未实测", "warn"));
    if (p.requires && p.requires.comfyui) badges.push(badge("需 ComfyUI", "info"));
    if (p.requires && p.requires.host_tool) badges.push(badge("需宿主工具", "info"));
    if (p.mode === "text2img") badges.push(badge("无参考图", "info"));
    if (p.cost && p.cost.credits_per_image) badges.push(badge(`${p.cost.credits_per_image} 魔粒/次`, "info"));
    const issues = [...(p.reasons || []), ...(p.warnings || [])];
    return `
      <button class="preset-card ${state.selectedPreset && state.selectedPreset.id === p.id ? "active" : ""}"
        data-id="${esc(p.id)}" ${p.selectable ? "" : "disabled"}>
        <div class="title">${esc(p.label)}</div>
        <div class="scene">${esc(p.scene)}</div>
        <div class="stages">${esc(p.stages_arg)} · 每张 ${esc((p.cost || {}).calls_per_image || 1)} 次调用</div>
        <div class="badges">${badges.join("")}</div>
        ${issues.length ? `<ul class="warn-list">${issues.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      </button>`;
  }).join("");
}

async function selectOrder(name) {
  state.current = name;
  state.selectedPreset = null;
  state.refs = [];
  state.uploads = [];
  renderOrders();
  const res = await api(`/api/orders/${enc(name)}`);
  state.detail = res.detail;
  const refs = (res.detail.brief.generation || {}).reference_images || [];
  state.refs = refs.map((r) => r.path).filter(Boolean);
  renderDetail();
  log(`已载入订单 ${name}`);
}

function renderReferenceList() {
  const box = $("referenceList");
  const refRows = [];
  for (const it of (((state.detail || {}).brief || {}).generation || {}).reference_images || []) {
    refRows.push({ path: it.path, label: `${it.path}（已归档）` });
  }
  for (const it of state.uploads) {
    refRows.push({ path: it.path, label: `${it.name}（本次上传）` });
  }
  if (!refRows.length) {
    box.innerHTML = '<p class="hint">暂无参考图。参考图模式必须先上传；文生图方案可不传。</p>';
    return;
  }
  box.innerHTML = refRows.map((r) => `
    <label>
      <input type="checkbox" data-ref="${esc(r.path)}" ${state.refs.includes(r.path) ? "checked" : ""}>
      ${esc(r.label)}
    </label>`).join("");
}

function renderDetail() {
  const d = state.detail;
  if (!d) {
    $("orderHeader").textContent = "未选择订单";
    $("statusBox").innerHTML = "";
    $("gallery").innerHTML = "";
    return;
  }
  const brief = d.brief || {};
  const gen = brief.generation || {};
  $("orderHeader").textContent = `当前订单：${d.name} · ${d.order_id || "无 order_id"} · ${brief.character_id || ""}`;
  const rows = [
    ["确认状态", d.confirmed ? "已确认" : "未确认"],
    ["当前方案", gen.preset_id || gen.backend || "-"],
    ["分段", (gen.stages || []).map((s) => `${s.stage}:${s.backend}`).join(" → ") || (gen.backend || "-")],
    ["生成模式", gen.mode || "-"],
    ["预算", `${d.budget.used}/${d.budget.limit}`],
    ["状态/阶段", `${d.status.state || "idle"} / ${d.status.phase || "-"}`],
    ["提示词", `${d.prompt_count} 条记录`],
  ];
  $("statusBox").innerHTML = rows.map(([k, v]) =>
    `<div class="status-row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join("");
  renderReferenceList();
  renderGallery();

  const canGenerate = !!d.confirmed;
  const canPending = !!d.pending;
  $("dryRunButton").disabled = !canGenerate;
  $("generateButton").disabled = !canGenerate;
  $("resumeButton").disabled = !canPending;
  $("discardButton").disabled = !canPending;
  $("confirmButton").disabled = !state.selectedPreset;
  $("confirmHint").textContent = state.selectedPreset
    ? `已选：${state.selectedPreset.label}。点确认会写入新的 stages，并改变 params_hash。`
    : "先选择一张可用方案。";
}

function renderGallery() {
  const box = $("gallery");
  const files = ((state.detail || {}).files || []);
  if (!files.length) {
    box.innerHTML = '<p class="hint">订单目录暂无图片。</p>';
    return;
  }
  box.innerHTML = files.map((f) => `
    <figure>
      <img src="/api/orders/${enc(state.current)}/file/${enc(f.name)}" alt="${esc(f.name)}">
      <figcaption>${esc(f.name)}</figcaption>
    </figure>`).join("");
}

async function createOrder(event) {
  event.preventDefault();
  const payload = {
    character_id: $("characterInput").value.trim(),
    character_name: $("characterNameInput").value.trim(),
    character_desc: $("characterDescInput").value.trim(),
    character_desc_en: $("characterDescEnInput").value.trim(),
    style_id: $("styleInput").value.trim(),
    style_desc: $("styleDescInput").value.trim(),
    gender: $("genderSelect").value,
    angle: $("angleSelect").value,
    count: parseInt($("countInput").value || "1", 10),
    budget: parseInt($("budgetInput").value || "10", 10),
  };
  const res = await api("/api/orders", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    log(`新建订单失败：${res.error || res.stderr || res.stdout}`);
    return;
  }
  log(`新建订单 ${res.order}`);
  await refreshOrders();
  await selectOrder(res.order);
}

async function uploadReference(event) {
  const file = event.target.files && event.target.files[0];
  if (!file || !state.current) return;
  const reader = new FileReader();
  reader.onload = async () => {
    const res = await api(`/api/orders/${enc(state.current)}/upload-reference`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: file.name, data: reader.result }),
    });
    if (!res.ok) {
      log(`上传失败：${res.error}`);
      return;
    }
    state.uploads.push({ path: res.path, name: res.name });
    state.refs.push(res.path);
    renderReferenceList();
    log(`已上传参考图 ${res.name}`);
  };
  reader.readAsDataURL(file);
  event.target.value = "";
}

async function confirmPreset() {
  if (!state.current || !state.selectedPreset) return;
  const payload = {
    preset_id: state.selectedPreset.id,
    references: state.refs,
    egress_consent: $("egressConsent").checked,
    text2img_consent: $("text2imgConsent").checked,
    commercial: $("commercialIntent").checked,
    seed: $("seedInput").value,
    budget: parseInt($("budgetInput").value || "0", 10) || (state.detail.brief.budget || 10),
    confirm_text: "确认生成",
  };
  const res = await api(`/api/orders/${enc(state.current)}/confirm`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (res.stdout) log(res.stdout.trim());
  if (res.stderr) log(res.stderr.trim());
  for (const w of res.warnings || []) log(`提示：${w}`);
  if (!res.ok) {
    for (const e of res.errors || []) log(`失败：${e}`);
    return;
  }
  state.detail = res.detail;
  state.refs = (((res.detail.brief || {}).generation || {}).reference_images || []).map((r) => r.path);
  state.uploads = [];
  renderDetail();
  await refreshOrders();
  log("确认已写入 brief.json");
}

async function runGenerate(options) {
  if (!state.current) return;
  const label = options.dry_run ? "dry-run" : (options.resume ? "续跑" : (options.discard_pending ? "放弃" : "生成"));
  log(`开始 ${label}…`);
  const res = await api(`/api/orders/${enc(state.current)}/generate`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options),
  });
  if (res.stdout) log(res.stdout.trim());
  if (res.stderr) log(res.stderr.trim());
  if (res.detail) {
    state.detail = res.detail;
    renderDetail();
    await refreshOrders();
  }
  log(res.ok ? `${label} 完成（退出码 ${res.rc}）` : `${label} 未成功（退出码 ${res.rc}）`);
}

function bindEvents() {
  $("orderForm").addEventListener("submit", createOrder);
  $("orderList").addEventListener("click", (e) => {
    const btn = e.target.closest(".order-item");
    if (btn) selectOrder(btn.dataset.name);
  });
  $("presetList").addEventListener("click", (e) => {
    const btn = e.target.closest(".preset-card");
    if (!btn || btn.disabled) return;
    state.selectedPreset = state.presets.find((p) => p.id === btn.dataset.id) || null;
    renderPresets();
    renderDetail();
    if (state.selectedPreset) log(`已选方案：${state.selectedPreset.label}`);
  });
  $("referenceList").addEventListener("change", (e) => {
    const box = e.target.closest("[data-ref]");
    if (!box) return;
    if (box.checked && !state.refs.includes(box.dataset.ref)) state.refs.push(box.dataset.ref);
    if (!box.checked) state.refs = state.refs.filter((x) => x !== box.dataset.ref);
  });
  $("referenceFile").addEventListener("change", uploadReference);
  $("confirmButton").addEventListener("click", confirmPreset);
  $("dryRunButton").addEventListener("click", () => runGenerate({ dry_run: true }));
  $("generateButton").addEventListener("click", () => runGenerate({}));
  $("resumeButton").addEventListener("click", () => runGenerate({ resume: true }));
  $("discardButton").addEventListener("click", () => runGenerate({ discard_pending: true }));
}

async function init() {
  bindEvents();
  try {
    await refreshMeta();
    await refreshPresets();
    await refreshOrders();
    if (state.orders.length) await selectOrder(state.orders[0].name);
    log("控制台已就绪");
  } catch (err) {
    log(`初始化失败：${err.message || err}`);
  }
}

init();
