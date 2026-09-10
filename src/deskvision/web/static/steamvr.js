"use strict";

// Local observability only: this browser never runs or authenticates the native bridge.
const byId = (id) => document.getElementById(id);
const POSE_FIELDS = [
  { id: "x", label: "X · 左右", unit: "m", min: -10, max: 10, step: .01, hint: "+X 向右" },
  { id: "y", label: "Y · 高度", unit: "m", min: -2, max: 10, step: .01, hint: "+Y 向上" },
  { id: "z", label: "Z · 前后", unit: "m", min: -10, max: 10, step: .01, hint: "−Z 向前" },
  { id: "yaw", label: "Yaw · 朝向", unit: "°", min: -180, max: 180, step: 1, hint: "围绕 Y 轴" },
  { id: "pitch", label: "Pitch · 倾斜", unit: "°", min: -180, max: 180, step: 1, hint: "围绕 X 轴 · 默认 −90° 放平" },
  { id: "roll", label: "Roll · 侧倾", unit: "°", min: -180, max: 180, step: 1, hint: "围绕 Z 轴" },
];
let savedSettings = null;
let settingsDirty = false;
let settingsBusy = false;
let settingsGeneration = 0;
let pollInFlight = false;
let pollTimer = null;
let logsPaused = false;
let lastLogFingerprint = null;
let pendingLogs = [];
let previewInFlight = false;
let previewStartedAt = 0;
let runtimeLogsBusy = false;
let supportedHost = false;

function setMessage(id, text, type = "") {
  const element = byId(id);
  element.textContent = text;
  element.className = `message ${type}`;
}

function setBadge(id, text, state) {
  const element = byId(id);
  element.textContent = text;
  element.className = `badge ${state}`;
}

async function requestJSON(path, options = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(path, { cache: "no-store", credentials: "same-origin", ...options, signal: controller.signal });
    const payload = await response.json();
    if (!response.ok) {
      const detail = typeof payload.detail === "string" ? payload.detail : `请求失败（HTTP ${response.status}）`;
      throw new Error(detail);
    }
    return payload;
  } finally {
    window.clearTimeout(timeout);
  }
}

function markPoseDirty() {
  settingsDirty = true;
  setBadge("alignment-status", "位置草稿 · 尚未应用", "warning");
  setMessage("settings-message", "编辑不会立即改变 VR；保存会隐藏显示，确认后才重新显示。", "pending");
}

function validPoseNumber(field, value) {
  if (String(value).trim() === "") return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= field.min && number <= field.max ? number : null;
}

function createPoseControls() {
  const container = byId("pose-controls");
  for (const field of POSE_FIELDS) {
    const wrapper = document.createElement("div");
    wrapper.className = "pose-field";
    const label = document.createElement("label");
    label.htmlFor = `pose-${field.id}`;
    label.textContent = `${field.label} (${field.unit})`;
    const number = document.createElement("input");
    number.id = `pose-${field.id}`;
    number.type = "number";
    number.min = String(field.min);
    number.max = String(field.max);
    number.step = String(field.step);
    number.disabled = true;
    const hint = document.createElement("span");
    hint.className = "pose-direction";
    hint.textContent = field.hint;
    const range = document.createElement("input");
    range.id = `range-${field.id}`;
    range.type = "range";
    range.min = String(field.min);
    range.max = String(field.max);
    range.step = String(field.step);
    range.setAttribute("aria-label", `${field.label} 滑块`);
    range.disabled = true;
    number.addEventListener("input", () => {
      markPoseDirty();
      const value = validPoseNumber(field, number.value);
      number.setCustomValidity(value === null ? `请输入 ${field.min} 至 ${field.max} 的数字。` : "");
      if (value !== null) range.value = String(value);
    });
    range.addEventListener("input", () => {
      number.value = range.value;
      number.setCustomValidity("");
      markPoseDirty();
    });
    wrapper.append(label, number, hint, range);
    container.append(wrapper);
  }
}

function syncPoseFields(settings, force = false) {
  if (!force && settingsDirty) return;
  for (const field of POSE_FIELDS) {
    const number = byId(`pose-${field.id}`);
    const range = byId(`range-${field.id}`);
    if (!force && (document.activeElement === number || document.activeElement === range)) continue;
    number.value = String(settings[field.id]);
    range.value = String(settings[field.id]);
    number.setCustomValidity("");
  }
}

function setSettingsBusy(busy) {
  settingsBusy = busy;
  for (const id of ["hide-display", "save-pose", "confirm-pose"]) byId(id).disabled = busy || savedSettings === null;
  for (const field of POSE_FIELDS) {
    byId(`pose-${field.id}`).disabled = busy || savedSettings === null;
    byId(`range-${field.id}`).disabled = busy || savedSettings === null;
  }
}

function readPoseDraft() {
  const result = {};
  for (const field of POSE_FIELDS) {
    const input = byId(`pose-${field.id}`);
    const value = validPoseNumber(field, input.value);
    if (value === null) {
      input.setCustomValidity(`请输入 ${field.min} 至 ${field.max} 的数字。`);
      input.reportValidity();
      throw new Error(`${field.label} 必须在 ${field.min} 至 ${field.max} 之间，不能留空。`);
    }
    result[field.id] = value;
  }
  return result;
}

async function savePose(mode) {
  if (settingsBusy || savedSettings === null) return;
  try {
    // Hiding must work even when an unfinished numeric draft is invalid.
    const pose = mode === "hide" ? Object.fromEntries(POSE_FIELDS.map((field) => [field.id, savedSettings[field.id]])) : readPoseDraft();
    const request = { ...pose, enabled: mode === "confirm", pose_confirmed: mode === "confirm" };
    settingsGeneration += 1;
    setSettingsBusy(true);
    await requestJSON("/api/steamvr/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) });
    savedSettings = request;
    if (mode !== "hide") {
      settingsDirty = false;
      syncPoseFields(request, true);
    }
    setMessage("settings-message", mode === "confirm" ? "位置已确认并允许输出。仍需在头显中确认真实可见性和对齐。" : mode === "hide" ? "已关闭 VR 显示。位置草稿保留。" : "位置已保存，VR 显示保持关闭。准备好后点击确认显示。", "success");
    if (!settingsDirty) setBadge("alignment-status", request.pose_confirmed ? "空间位置已确认" : "等待位置确认", request.pose_confirmed ? "ready" : "waiting");
  } catch (error) {
    setMessage("settings-message", error.name === "AbortError" ? "请求超时；请查看状态后再操作，避免误判保存结果。" : error.message, "error");
  } finally {
    setSettingsBusy(false);
  }
}

function logRow(event) {
  const row = document.createElement("article");
  const level = String(event.level || "info").toLowerCase();
  row.className = `log-row ${["error", "critical"].includes(level) ? "error" : ["warn", "warning"].includes(level) ? "warning" : "info"}`;
  const meta = document.createElement("div");
  meta.className = "log-meta";
  for (const [value, className] of [[event.time || "—", ""], [level.toUpperCase(), "log-level"], [event.source || "—", ""], [event.event || "—", ""]]) {
    const span = document.createElement("span");
    span.className = className;
    span.textContent = String(value);
    meta.append(span);
  }
  const message = document.createElement("p");
  message.className = "log-message";
  message.textContent = String(event.message || "");
  row.append(meta, message);
  if (event.details && Object.keys(event.details).length) {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "事件详情";
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(event.details, null, 2);
    details.append(summary, pre);
    row.append(details);
  }
  return row;
}

function renderLogs(logs, force = false) {
  pendingLogs = Array.isArray(logs) ? logs.slice(-200) : [];
  if (logsPaused && !force) return;
  const fingerprint = pendingLogs.map((event) => event.id).join(",");
  if (!force && fingerprint === lastLogFingerprint) return;
  lastLogFingerprint = fingerprint;
  const container = byId("vr-logs");
  const wasAtBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 45;
  const rows = pendingLogs.map(logRow);
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "log-empty";
    empty.textContent = "暂无事件；等待 Windows Bridge 连接或记录头显反馈。";
    rows.push(empty);
  }
  container.replaceChildren(...rows);
  if (wasAtBottom) container.scrollTop = container.scrollHeight;
  byId("logs-caption").textContent = `显示最近 ${pendingLogs.length} 条事件（页面上限 200 条）。诊断导出包含后端当前保留的事件。`;
}

function refreshPreview() {
  if (previewInFlight && Date.now() - previewStartedAt < 5000) return;
  previewInFlight = true;
  previewStartedAt = Date.now();
  byId("vr-preview").src = `/api/steamvr/preview.png?t=${previewStartedAt}`;
}

function renderStatus(status, applySettings = true) {
  if (status.schema_version !== "mikotype-steamvr-0.1") throw new Error("SteamVR 接口版本不匹配，请刷新页面并确认服务版本。");
  const bridge = status.bridge || {};
  const scene = status.scene || {};
  const settings = applySettings ? status.settings || {} : savedSettings || status.settings || {};
  supportedHost = status.supported_host === true;
  byId("platform").textContent = String(status.platform || "—");
  byId("host-warning").hidden = supportedHost;
  byId("collect-runtime-logs").disabled = !supportedHost || runtimeLogsBusy;
  if (status.runtime_logs) byId("runtime-logs-output").textContent = JSON.stringify(status.runtime_logs, null, 2);
  byId("bridge-status").textContent = `${bridge.connected ? "心跳在线" : "未连接 / 已断开"} · ${bridge.status || "等待 Bridge"}`;
  byId("bridge-details").textContent = bridge.details && Object.keys(bridge.details).length ? JSON.stringify(bridge.details, null, 2) : "尚无 Bridge 状态详情。";
  byId("heartbeat-age").textContent = Number.isFinite(bridge.last_seen_age_ms) ? `${Math.round(bridge.last_seen_age_ms)} ms` : "尚无心跳";
  byId("display-status").textContent = settings.enabled && settings.pose_confirmed ? "允许输出 · 仍需头显确认" : "关闭 / 等待位置确认";
  const awaitingScene = scene.age_ms == null || scene.generation === 0;
  const revisionStatus = scene.revisions_match === false ? (awaitingScene ? " · 等待有效场景 / 版本验证" : " · 模型版本不匹配") : "";
  byId("scene-status").textContent = `${scene.fresh ? "数据新鲜" : "数据过期 / 暂无数据"} · ${scene.pose_usable ? "Marker 可用" : "Marker 不可用"}${revisionStatus}`;
  byId("scene-counts").textContent = `${scene.key_count || 0} / ${scene.highlights || 0} / ${scene.fingertips || 0}`;
  byId("model-revision").textContent = scene.model_revision || "—";
  setBadge("connection-status", bridge.connected ? "服务在线 · Bridge 有心跳" : "服务在线 · 等待 Bridge", bridge.connected ? "ready" : "waiting");
  if (!settingsBusy && applySettings) {
    savedSettings = settings;
    syncPoseFields(settings);
    setSettingsBusy(false);
    if (!settingsDirty) setBadge("alignment-status", settings.pose_confirmed ? "空间位置已确认" : "等待位置确认", settings.pose_confirmed ? "ready" : "waiting");
  }
  const limitations = Array.isArray(status.limitations) ? status.limitations : [];
  if (limitations.length) byId("runtime-limitations").replaceChildren(...limitations.map((text) => {
    const item = document.createElement("li");
    item.textContent = String(text);
    return item;
  }));
  setMessage("service-error", "");
  renderLogs(status.logs);
  refreshPreview();
}

async function pollStatus() {
  if (pollInFlight) return;
  window.clearTimeout(pollTimer);
  if (document.hidden) {
    pollTimer = window.setTimeout(pollStatus, 1000);
    return;
  }
  pollInFlight = true;
  const generationAtRequest = settingsGeneration;
  try {
    const status = await requestJSON("/api/steamvr/status");
    renderStatus(status, generationAtRequest === settingsGeneration);
  } catch (error) {
    setBadge("connection-status", "无法读取本机服务", "warning");
    setMessage("service-error", error.name === "AbortError" ? "状态请求超时。请检查当前终端打印的服务地址。" : error.message, "error");
    byId("bridge-status").textContent = "状态未知 · 本机接口不可达";
    byId("scene-status").textContent = "状态未知 · 不沿用上次在线结论";
    byId("vr-preview").hidden = true;
    byId("preview-empty").hidden = false;
    byId("preview-empty").textContent = "服务不可达 · 已隐藏上次预览";
  } finally {
    pollInFlight = false;
    pollTimer = window.setTimeout(pollStatus, 1000);
  }
}

async function submitFeedback(event) {
  event.preventDefault();
  const button = byId("send-feedback");
  if (button.disabled) return;
  const input = byId("headset-feedback");
  const message = input.value.trim();
  if (!message) return;
  button.disabled = true;
  try {
    await requestJSON("/api/steamvr/feedback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message }) });
    if (input.value.trim() === message) input.value = "";
    setMessage("feedback-message", "已记录头显反馈。请导出诊断 JSON 后发给开发者。", "success");
  } catch (error) {
    setMessage("feedback-message", error.name === "AbortError" ? "反馈请求超时，请查看日志是否已记录。" : error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function collectRuntimeLogs() {
  if (!supportedHost || runtimeLogsBusy) return;
  runtimeLogsBusy = true;
  byId("collect-runtime-logs").disabled = true;
  setMessage("runtime-logs-message", "正在读取已知 SteamVR / Home 日志；不会修改文件。", "pending");
  try {
    const result = await requestJSON("/api/steamvr/collect-logs", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    byId("runtime-logs-output").textContent = JSON.stringify(result, null, 2);
    byId("runtime-logs-details").open = true;
    setMessage("runtime-logs-message", `收集结果：${result.status || "完成"}。请检查快照；导出诊断也会包含本次结果。`, "success");
  } catch (error) {
    setMessage("runtime-logs-message", error.name === "AbortError" ? "日志收集超时；请检查导出诊断中的收集状态。" : error.message, "error");
  } finally {
    runtimeLogsBusy = false;
    byId("collect-runtime-logs").disabled = !supportedHost;
  }
}

function startSteamVRPage() {
  createPoseControls();
  byId("alignment-form").addEventListener("submit", (event) => { event.preventDefault(); savePose("save"); });
  byId("confirm-pose").addEventListener("click", () => savePose("confirm"));
  byId("hide-display").addEventListener("click", () => savePose("hide"));
  byId("feedback-form").addEventListener("submit", submitFeedback);
  byId("collect-runtime-logs").addEventListener("click", collectRuntimeLogs);
  byId("pause-logs").addEventListener("click", () => {
    logsPaused = !logsPaused;
    byId("pause-logs").textContent = logsPaused ? "恢复实时日志" : "暂停日志滚动";
    byId("pause-logs").setAttribute("aria-pressed", String(logsPaused));
    if (!logsPaused) renderLogs(pendingLogs, true);
  });
  byId("download-token").addEventListener("click", () => setMessage("token-message", "仅将下载的凭证文件交给本机 Bridge。请勿将它附在反馈中。"));
  byId("vr-preview").addEventListener("load", () => {
    previewInFlight = false;
    byId("vr-preview").hidden = false;
    byId("preview-empty").hidden = true;
  });
  byId("vr-preview").addEventListener("error", () => {
    previewInFlight = false;
    byId("vr-preview").hidden = true;
    byId("preview-empty").hidden = false;
    byId("preview-empty").textContent = "预览暂不可用；请查看下方诊断日志。";
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) pollStatus(); });
  window.addEventListener("pagehide", () => window.clearTimeout(pollTimer));
  pollStatus();
}

startSteamVRPage();
