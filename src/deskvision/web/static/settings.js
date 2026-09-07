"use strict";

const ui = {
  form: document.getElementById("settings-form"),
  groups: document.getElementById("settings-groups"),
  save: document.getElementById("save-settings"),
  reset: document.getElementById("reset-settings"),
  reload: document.getElementById("reload-settings"),
  message: document.getElementById("settings-message"),
  serviceStatus: document.getElementById("service-status"),
  restartStatus: document.getElementById("restart-status"),
  restartNotice: document.getElementById("restart-notice"),
};

let settings = null;
let service = null;
let busy = false;
let dirty = false;

async function request(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { cache: "no-store", ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `${response.status} ${response.statusText}`);
  return payload;
}

function setText(id, value, fallback = "—") {
  document.getElementById(id).textContent = value === null || value === undefined || value === "" ? fallback : String(value);
}

function showMessage(text = "", kind = "") {
  ui.message.textContent = text;
  ui.message.className = `message ${kind}`.trim();
}

function setBusy(nextBusy) {
  busy = nextBusy;
  ui.save.disabled = nextBusy;
  ui.reset.disabled = nextBusy;
  ui.reload.disabled = nextBusy;
  for (const control of ui.form.querySelectorAll("input, select")) control.disabled = nextBusy;
}

function renderRestartState() {
  const restartRequired = Boolean(settings?.restart_required || service?.restart_required);
  ui.restartStatus.textContent = restartRequired ? "重启后生效" : "已与保存配置同步";
  ui.restartStatus.className = `badge ${restartRequired ? "warning" : "ready"}`;
  ui.restartNotice.hidden = !restartRequired;
}

function renderService(payload) {
  service = payload;
  const serviceName = payload.service || "MikoType";
  const mode = payload.mode || "local";
  ui.serviceStatus.textContent = `${serviceName} 运行中`;
  ui.serviceStatus.className = "badge ready";
  const url = payload.url || `${location.protocol}//${location.host}`;
  const urlNode = document.getElementById("service-url");
  urlNode.textContent = url;
  urlNode.href = url;
  setText("service-mode", mode);
  const actualPort = payload.port ?? location.port;
  const configuredPort = payload.configured_port;
  setText(
    "service-port",
    configuredPort !== undefined && Number(configuredPort) !== Number(actualPort)
      ? `${actualPort} (配置 ${configuredPort})`
      : actualPort,
  );
  setText("auto-port", payload.auto_selected ? "端口占用，已自动选择" : "使用配置端口");
  setText("config-path", payload.config_path || settings?.base_config_path);
  setText("override-config-path", payload.override_config_path || settings?.override_config_path, "尚未生成");
  setText("setup-workspace-path", payload.setup_workspace_path);
  renderRestartState();
}

function fieldId(section, name) {
  return `setting-${section}-${name}`.replace(/[^a-zA-Z0-9_-]/g, "-");
}

function normalizeOption(option) {
  if (option && typeof option === "object" && !Array.isArray(option)) {
    return { value: option.value, label: option.label ?? option.value };
  }
  return { value: option, label: option };
}

function applyFieldMetadata(control, field) {
  control.id = fieldId(control.dataset.section, control.dataset.field);
  control.name = `${control.dataset.section}.${control.dataset.field}`;
  control.dataset.valueType = field.type || "string";
  if (field.min !== undefined) control.min = String(field.min);
  if (field.max !== undefined) control.max = String(field.max);
  if (field.step !== undefined) control.step = String(field.step);
  if (field.type !== "boolean" && field.required !== false) control.required = true;
}

function buildControl(section, field) {
  const options = Array.isArray(field.options) ? field.options.map(normalizeOption) : [];
  let control;
  if (options.length > 0) {
    control = document.createElement("select");
    for (const item of options) {
      const option = document.createElement("option");
      option.value = String(item.value);
      option.textContent = String(item.label);
      option.selected = item.value === field.value || String(item.value) === String(field.value);
      control.append(option);
    }
  } else {
    control = document.createElement("input");
    if (field.type === "boolean") {
      control.type = "checkbox";
      control.checked = Boolean(field.value);
    } else if (field.type === "integer" || field.type === "number") {
      control.type = "number";
      control.value = field.value ?? "";
      if (field.type === "integer" && field.step === undefined) control.step = "1";
    } else {
      control.type = "text";
      control.value = field.value ?? "";
    }
  }
  control.dataset.section = section;
  control.dataset.field = field.name;
  applyFieldMetadata(control, field);
  control.addEventListener("input", () => {
    dirty = true;
    showMessage("有未保存的参数变更。", "pending");
  });
  return control;
}

function renderField(section, field) {
  const row = document.createElement("div");
  row.className = "field";
  const copy = document.createElement("div");
  copy.className = "field-copy";
  const title = document.createElement("label");
  title.className = "field-title";
  title.textContent = field.label || field.name;
  const description = document.createElement("p");
  description.className = "field-description";
  description.textContent = field.description || `${section}.${field.name}`;
  const control = buildControl(section, field);
  title.htmlFor = control.id;
  if (field.restart_required) {
    const restart = document.createElement("span");
    restart.className = "restart-pill";
    restart.textContent = "需重启";
    title.append(restart);
  }
  copy.append(title, description);
  if (field.type === "boolean" && control instanceof HTMLInputElement) {
    const wrapper = document.createElement("div");
    wrapper.className = "boolean-control";
    const state = document.createElement("span");
    const updateState = () => { state.textContent = control.checked ? "开启" : "关闭"; };
    control.addEventListener("change", updateState);
    updateState();
    wrapper.append(state, control);
    row.append(copy, wrapper);
  } else {
    row.append(copy, control);
  }
  return row;
}

function renderSettings(payload) {
  settings = payload;
  ui.groups.replaceChildren();
  const groups = Array.isArray(payload.groups) ? payload.groups : [];
  if (groups.length === 0) {
    const empty = document.createElement("section");
    empty.className = "card empty-card";
    empty.textContent = "后端未返回可配置参数。";
    ui.groups.append(empty);
  }
  for (const group of groups) {
    const section = document.createElement("section");
    section.className = "card settings-group";
    const heading = document.createElement("div");
    const eyebrow = document.createElement("p");
    eyebrow.className = "eyebrow";
    eyebrow.textContent = group.section || "SETTINGS";
    const title = document.createElement("h2");
    title.textContent = group.label || group.section;
    const description = document.createElement("p");
    description.className = "group-description";
    description.textContent = group.description || "";
    heading.append(eyebrow, title, description);
    const fields = document.createElement("div");
    fields.className = "fields";
    for (const field of group.fields || []) fields.append(renderField(group.section, field));
    section.append(heading, fields);
    ui.groups.append(section);
  }
  const protectedSections = Array.isArray(payload.protected_sections) ? payload.protected_sections : [];
  setText("protected-sections", protectedSections.length ? `受保护：${protectedSections.join("、")}` : "", "");
  setText("config-path", service?.config_path || payload.base_config_path);
  setText("override-config-path", service?.override_config_path || payload.override_config_path, "尚未生成");
  dirty = false;
  renderRestartState();
}

function readControl(control) {
  const type = control.dataset.valueType;
  if (type === "boolean" && control instanceof HTMLInputElement && control.type === "checkbox") return control.checked;
  if (type === "integer") return Number.parseInt(control.value, 10);
  if (type === "number") return Number(control.value);
  return control.value;
}

function collectValues() {
  const values = {};
  for (const control of ui.form.querySelectorAll("[data-section][data-field]")) {
    const section = control.dataset.section;
    const field = control.dataset.field;
    if (!values[section]) values[section] = {};
    values[section][field] = readControl(control);
  }
  return values;
}

async function load() {
  setBusy(true);
  showMessage("正在读取服务与参数…");
  try {
    const [nextSettings, nextService] = await Promise.all([
      request("/api/settings"),
      request("/api/service"),
    ]);
    renderSettings(nextSettings);
    renderService(nextService);
    showMessage("已读取当前服务和保存配置。", "success");
  } catch (error) {
    ui.serviceStatus.textContent = "配置服务不可用";
    ui.serviceStatus.className = "badge warning";
    showMessage(`读取失败：${error.message}`, "error");
  } finally {
    setBusy(false);
  }
}

ui.form.addEventListener("submit", event => {
  event.preventDefault();
  if (busy || !ui.form.reportValidity()) return;
  void (async () => {
    setBusy(true);
    showMessage("正在校验并保存…");
    try {
      const saved = await request("/api/settings", {
        method: "PUT",
        body: JSON.stringify({ values: collectValues() }),
      });
      renderSettings(saved);
      renderService(await request("/api/service"));
      showMessage(
        saved.restart_required
          ? "参数已保存。重启 MikoType 后生效；当前管线未被热替换。"
          : "参数已保存，当前运行配置无变更。",
        "success",
      );
    } catch (error) {
      showMessage(`保存失败：${error.message}`, "error");
    } finally {
      setBusy(false);
    }
  })();
});

ui.reset.addEventListener("click", () => {
  if (busy || !confirm("恢复 WebUI 可调参数的项目默认值？受保护配置不会被删除，当前运行管线在重启前不会变化。")) return;
  void (async () => {
    setBusy(true);
    showMessage("正在恢复可调参数默认值…");
    try {
      const reset = await request("/api/settings/reset", { method: "POST" });
      renderSettings(reset);
      renderService(await request("/api/service"));
      showMessage(
        reset.restart_required
          ? "已恢复可调参数默认值。重启 MikoType 后生效。"
          : "已恢复可调参数默认值，当前运行配置无变更。",
        "success",
      );
    } catch (error) {
      showMessage(`恢复失败：${error.message}`, "error");
    } finally {
      setBusy(false);
    }
  })();
});

ui.reload.addEventListener("click", () => { if (!busy) void load(); });

window.addEventListener("beforeunload", event => {
  if (!dirty) return;
  event.preventDefault();
});

void load();
