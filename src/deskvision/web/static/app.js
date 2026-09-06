"use strict";

const ui = {
  viewport: document.getElementById("viewport"),
  camera: document.getElementById("camera"),
  overlay: document.getElementById("overlay"),
  empty: document.getElementById("empty-message"),
  keyboard: document.getElementById("keyboard-plane"),
  overall: document.getElementById("overall-status"),
  pose: document.getElementById("pose-status"),
  frame: document.getElementById("frame-id"),
  error: document.getElementById("runtime-error"),
};

let config = { mirror_preview: true };
let keyElements = new Map();
let socket = null;
let pendingBundle = null;
let activeImageUrl = null;
let decodeSequence = 0;
let staleTimer = null;
let reconnectTimer = null;
let shuttingDown = false;

function shortRevision(value) {
  return value ? `${String(value).slice(0, 9)}…` : "—";
}

function number(value, digits = 1) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : "—";
}

function setText(id, value) {
  document.getElementById(id).textContent = value;
}

function configureLayout(profile) {
  keyElements.clear();
  ui.keyboard.replaceChildren();
  const keys = Array.isArray(profile.keys) ? profile.keys : [];
  const width = Math.max(Number(profile.keyboard_width_units) || 0, ...keys.map(key => Number(key.x_units) + Number(key.width_units)));
  const height = Math.max(Number(profile.keyboard_height_units) || 0, ...keys.map(key => Number(key.y_units) + Number(key.height_units)));
  ui.keyboard.style.setProperty("--keyboard-aspect", String(width / Math.max(height, 1)));
  for (const key of keys) {
    const element = document.createElement("div");
    element.className = "key";
    element.dataset.keyId = key.physical_key_id || key.key_id;
    element.textContent = key.label || key.key_id;
    element.title = `${key.label || key.key_id} · ${element.dataset.keyId}`;
    element.style.left = `${Number(key.x_units) / width * 100}%`;
    element.style.top = `${Number(key.y_units) / height * 100}%`;
    element.style.width = `${Number(key.width_units) / width * 100}%`;
    element.style.height = `${Number(key.height_units) / height * 100}%`;
    ui.keyboard.append(element);
    keyElements.set(element.dataset.keyId, element);
  }
}

function drawFingertips(state) {
  const canvas = ui.overlay;
  const bounds = ui.camera.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(bounds.width * scale));
  canvas.height = Math.max(1, Math.round(bounds.height * scale));
  const context = canvas.getContext("2d");
  context.scale(scale, scale);
  context.clearRect(0, 0, bounds.width, bounds.height);
  for (const tip of state.fingertips || []) {
    const x = (config.mirror_preview ? 1 - Number(tip.image_x) : Number(tip.image_x)) * bounds.width;
    const y = Number(tip.image_y) * bounds.height;
    const confidence = Math.max(0, Math.min(1, Number(tip.confidence) || 0));
    const radius = 9 + confidence * 7;
    const gradient = context.createRadialGradient(x, y, 2, x, y, radius * 2.2);
    gradient.addColorStop(0, "rgba(190,255,216,.95)");
    gradient.addColorStop(.35, "rgba(90,255,156,.55)");
    gradient.addColorStop(1, "rgba(90,255,156,0)");
    context.fillStyle = gradient;
    context.beginPath();
    context.arc(x, y, radius * 2.2, 0, Math.PI * 2);
    context.fill();
    context.strokeStyle = "rgba(220,255,233,.88)";
    context.lineWidth = 1.5;
    context.beginPath();
    context.arc(x, y, radius, 0, Math.PI * 2);
    context.stroke();
  }
}

function renderHighlights(state) {
  for (const element of keyElements.values()) {
    element.classList.remove("active", "direct");
    element.style.setProperty("--intensity", "0");
  }
  for (const highlight of state.key_highlights || []) {
    const element = keyElements.get(highlight.physical_key_id);
    if (!element) continue;
    const intensity = Math.max(0, Math.min(1, Number(highlight.intensity) || 0));
    element.style.setProperty("--intensity", String(intensity));
    element.classList.toggle("active", intensity > .01);
    element.classList.toggle("direct", Boolean(highlight.direct));
  }
}

function clearLiveDisplay(reason) {
  decodeSequence += 1;
  pendingBundle = null;
  window.clearTimeout(staleTimer);
  staleTimer = null;
  if (activeImageUrl) URL.revokeObjectURL(activeImageUrl);
  activeImageUrl = null;
  ui.camera.removeAttribute("src");
  const context = ui.overlay.getContext("2d");
  context.clearRect(0, 0, ui.overlay.width, ui.overlay.height);
  ui.overlay.width = 1;
  ui.overlay.height = 1;
  renderHighlights({});
  ui.empty.hidden = false;
  ui.empty.textContent = reason;
  ui.overall.textContent = "等待同帧状态";
  ui.overall.className = "badge degraded";
  ui.frame.textContent = "Frame —";
  ui.pose.textContent = "键盘未定位";
  ui.pose.className = "badge waiting";
  setText("hand-count", "0");
  setText("fingertip-count", "0");
  setText("highlight-count", "0");
  setText("diagnostic-status", "stale");
}

function armStaleWatchdog() {
  window.clearTimeout(staleTimer);
  const timeout = Math.max(100, Number(config.bundle_stale_after_ms) || 1000);
  staleTimer = window.setTimeout(
    () => clearLiveDisplay("同帧状态已过期，等待新帧…"),
    timeout,
  );
}

function commitBundle(metadata, imageBlob) {
  const frame = metadata.frame || {};
  const state = metadata.state || {};
  const identityMatches = (
    frame.source_id === state.source_id
    && Number(frame.frame_id) === Number(state.source_frame_id)
    && Number(frame.acquired_at_ns) === Number(state.captured_at_ns)
  );
  if (!identityMatches || imageBlob.size !== Number(frame.byte_length)) {
    clearLiveDisplay("同帧 Bundle 校验失败，等待新帧…");
    return;
  }

  const sequence = ++decodeSequence;
  const imageUrl = URL.createObjectURL(imageBlob);
  const decoder = new Image();
  decoder.addEventListener("load", () => {
    if (sequence !== decodeSequence) {
      URL.revokeObjectURL(imageUrl);
      return;
    }
    const previousUrl = activeImageUrl;
    activeImageUrl = imageUrl;
    ui.viewport.style.aspectRatio = `${Number(frame.width)} / ${Number(frame.height)}`;
    ui.camera.src = imageUrl;
    renderState(state);
    armStaleWatchdog();
    if (previousUrl) URL.revokeObjectURL(previousUrl);
  }, { once: true });
  decoder.addEventListener("error", () => {
    URL.revokeObjectURL(imageUrl);
    if (sequence === decodeSequence) {
      clearLiveDisplay("同帧图像解码失败，等待新帧…");
    }
  }, { once: true });
  decoder.src = imageUrl;
}

function renderState(state) {
  ui.empty.hidden = true;
  ui.frame.textContent = `Frame ${state.source_frame_id ?? "—"}`;
  const diagnostics = state.diagnostics || {};
  const keyboard = state.keyboard || {};
  const pose = keyboard.pose || {};
  const ready = diagnostics.status === "ready" && pose.usable;
  ui.overall.textContent = ready ? "映射运行中" : (diagnostics.status || "等待状态");
  ui.overall.className = `badge ${ready ? "ready" : "degraded"}`;
  ui.pose.textContent = pose.usable ? `${pose.status} · ${pose.anchor_count} markers` : (pose.status || "键盘未定位");
  ui.pose.className = `badge ${pose.usable ? "ready" : "degraded"}`;
  setText("capture-fps", number(diagnostics.capture_fps));
  setText("frame-age", `${number(diagnostics.frame_age_ms)} ms`);
  setText("hand-ms", `${number(diagnostics.hand_inference_ms)} ms`);
  setText("keyboard-ms", `${number(diagnostics.keyboard_inference_ms)} ms`);
  setText("marker-count", String(pose.anchor_count ?? 0));
  setText("pose-confidence", number(pose.confidence, 2));
  setText("hand-count", String((state.hands || []).length));
  setText("fingertip-count", String((state.fingertips || []).length));
  setText("highlight-count", String((state.key_highlights || []).length));
  setText("diagnostic-status", diagnostics.status || "—");
  setText("model-revision", shortRevision(keyboard.artifacts?.model));
  setText("contact-revision", shortRevision(keyboard.artifacts?.contact_map));
  ui.error.hidden = !diagnostics.error;
  ui.error.textContent = diagnostics.error || "";
  drawFingertips(state);
  renderHighlights(state);
}

function connectBundle() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const connection = new WebSocket(`${protocol}//${location.host}/ws/bundle`);
  connection.binaryType = "blob";
  socket = connection;
  connection.addEventListener("message", event => {
    if (connection !== socket) return;
    if (typeof event.data === "string") {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "stale") {
          clearLiveDisplay("同帧状态已过期，等待新帧…");
          return;
        }
        if (payload.type !== "frame_state_bundle") {
          throw new Error(`unsupported WebSocket payload: ${payload.type}`);
        }
        pendingBundle = payload;
      } catch (error) {
        console.error(error);
        clearLiveDisplay("Bundle 协议错误，等待重连…");
        connection.close();
      }
      return;
    }
    if (!pendingBundle) {
      clearLiveDisplay("Bundle 缺少帧元数据，等待重连…");
      connection.close();
      return;
    }
    const metadata = pendingBundle;
    pendingBundle = null;
    commitBundle(metadata, event.data);
  });
  connection.addEventListener("close", () => {
    if (connection !== socket) return;
    socket = null;
    clearLiveDisplay("状态连接已断开，正在重连…");
    ui.overall.textContent = "状态连接重试中";
    ui.overall.className = "badge degraded";
    if (!shuttingDown) reconnectTimer = window.setTimeout(connectBundle, 700);
  });
  connection.addEventListener("error", () => connection.close());
}

async function refreshHealth() {
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    if (!response.ok) throw new Error(`health ${response.status}`);
    const health = await response.json();
    if (health.perception?.status !== "running" && socket) {
      clearLiveDisplay("感知线程不可用，等待恢复…");
    }
  } catch (_) {
    ui.overall.textContent = "服务不可用";
    ui.overall.className = "badge degraded";
  } finally {
    window.setTimeout(refreshHealth, Math.max(250, Number(config.health_interval_ms) || 1000));
  }
}

async function start() {
  try {
    const [configResponse, layoutResponse] = await Promise.all([
      fetch("/api/config", { cache: "no-store" }),
      fetch("/api/layout", { cache: "no-store" }),
    ]);
    if (!configResponse.ok || !layoutResponse.ok) {
      throw new Error(`config ${configResponse.status}, layout ${layoutResponse.status}`);
    }
    config = await configResponse.json();
    configureLayout(await layoutResponse.json());
    ui.viewport.classList.toggle("mirrored", Boolean(config.mirror_preview));
  } catch (error) {
    ui.error.hidden = false;
    ui.error.textContent = `初始化调试界面失败：${error}`;
  }
  connectBundle();
  refreshHealth();
}

window.addEventListener("beforeunload", () => {
  shuttingDown = true;
  window.clearTimeout(reconnectTimer);
  window.clearTimeout(staleTimer);
  if (socket) socket.close();
  if (activeImageUrl) URL.revokeObjectURL(activeImageUrl);
});

start();
