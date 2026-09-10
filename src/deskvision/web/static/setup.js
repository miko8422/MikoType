"use strict";

let profile = null;
let keys = [];
let selectedId = null;
let drag = null;
let anchorTimer = null;
let anchorObservePromise = null;
let actionBusy = false;
let captureCountdownController = null;
let setupState = null;
let cameraState = null;
let contactSession = null;
let anchorState = null;
let currentStep = null;
let tutorialMode = false;
let layoutDirty = false;
let statusPromise = null;
let statusTimer = null;
let navigationEpoch = 0;

const steps = [
  {id:"camera", label:"摄像头", title:"先确认摄像头和画面方向", description:"让键盘与手部完整出现在画面里。预览翻转仅改变显示，不影响已保存的校准。"},
  {id:"layout", label:"键位图", title:"确认你有哪些键", description:"已经有正确的键位图？可以直接跳过，沿用已保存的布局。这里不是触点精度校准。"},
  {id:"anchor", label:"Marker", title:"建立稳定的键盘参考坐标", description:"按键位图上的对应关系摆放 Marker，再主动开始观察。已有有效 Anchor 可以直接沿用。"},
  {id:"contact", label:"五次触点", title:"测量每个键的真实触点", description:"使用右手食指，对提示的每个键独立采样五次。已有完整校准可以直接跳过。"},
  {id:"apply", label:"应用 / 重启", title:"把完成的校准交给实时服务", description:"新草稿需要明确应用，然后重启服务。已经在使用的有效配置不需要重复应用。"},
  {id:"inspect", label:"检查效果", title:"最后，用真实键盘检查效果", description:"文件有效不等于精度验收通过。请亲自观察指尖、键位高亮与 3D 映射。"},
];

const actionButtonIds = [
  "save-layout",
  "start-anchor",
  "reset-anchor",
  "finalize-anchor",
  "start-contact",
  "capture-contact",
  "undo-contact",
  "reset-contact",
  "apply-bundle",
  "preview-mirror",
  "preview-flip-vertical",
];

const svg = document.getElementById("keyboard-svg");
const fields = Object.fromEntries(["x","y","w","h"].map(name => [name, document.getElementById(`${name}-input`)]));
const markerRoot = document.getElementById("markers");
const setupViewport = document.getElementById("setup-viewport");
const setupPreview = setupViewport.querySelector("img");
const setupPreviewUrl = setupPreview.getAttribute("src") || "/stream.mjpg";
const keyFieldNames = {x:"x_units", y:"y_units", w:"width_units", h:"height_units"};

function updatePreviewVisibility() {
  const visible = !document.hidden && document.getElementById("calibration-panel").classList.contains("active");
  if (visible && !setupPreview.hasAttribute("src")) setupPreview.src = setupPreviewUrl;
  else if (!visible) setupPreview.removeAttribute("src");
  if (!visible) { stopAnchorPolling(); cancelCaptureCountdown(); }
}
updatePreviewVisibility();

async function request(path, options = {}) {
  const response = await fetch(path, { cache:"no-store", headers:{"Content-Type":"application/json"}, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `${response.status} ${response.statusText}`);
  return payload;
}

function setActionBusy(busy) {
  actionBusy = busy;
  updateActionReadiness();
}

async function runAction(action) {
  if (actionBusy) return;
  setActionBusy(true);
  try {
    if (statusPromise) await statusPromise;
    if (anchorObservePromise) await anchorObservePromise;
    await action();
  } finally {
    setActionBusy(false);
  }
}

function stopAnchorPolling() {
  clearInterval(anchorTimer);
  anchorTimer = null;
}

function startAnchorPolling() {
  stopAnchorPolling();
  if (document.hidden || currentStep !== "anchor") return;
  anchorTimer = setInterval(() => { void observeAnchors(); }, 120);
}

function countdownDelay(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      const error = new Error("capture countdown cancelled");
      error.name = "AbortError";
      reject(error);
      return;
    }
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    function onAbort() {
      clearTimeout(timer);
      const error = new Error("capture countdown cancelled");
      error.name = "AbortError";
      reject(error);
    }
    signal.addEventListener("abort", onAbort, { once:true });
  });
}

function cancelCaptureCountdown() {
  if (captureCountdownController) captureCountdownController.abort();
}

function message(id, text, kind = "") {
  const node = document.getElementById(id);
  node.textContent = text;
  node.className = `message ${kind}`;
}

function element(name, attributes = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key,value] of Object.entries(attributes)) node.setAttribute(key,String(value));
  return node;
}

function boardSize() {
  return {
    width: Math.max(1,...keys.map(key => Number(key.x_units)+Number(key.width_units))),
    height: Math.max(1,...keys.map(key => Number(key.y_units)+Number(key.height_units))),
  };
}

function svgPoint(event) {
  const point = svg.createSVGPoint(); point.x=event.clientX; point.y=event.clientY;
  return point.matrixTransform(svg.getScreenCTM().inverse());
}

function renderLayout() {
  const size = boardSize();
  svg.setAttribute("viewBox",`-.2 -.2 ${size.width+.4} ${size.height+.4}`);
  svg.replaceChildren();
  for (const key of keys) {
    const group=element("g",{class:`key-group ${key.key_id===selectedId?"selected":""}`});
    group.append(element("rect",{class:"key-rect",x:key.x_units,y:key.y_units,width:key.width_units,height:key.height_units,rx:.07}));
    const label=element("text",{class:"key-label",x:Number(key.x_units)+Number(key.width_units)/2,y:Number(key.y_units)+Number(key.height_units)/2}); label.textContent=key.label; group.append(label);
    const anchor=(profile.anchors||[]).find(item=>item.key_id===key.key_id);
    if(anchor) group.append(element("circle",{class:"anchor-dot",cx:Number(key.x_units)+Number(key.width_units)-.15,cy:Number(key.y_units)+.15,r:.1}));
    group.addEventListener("pointerdown",event=>{ event.preventDefault(); selectedId=key.key_id; const p=svgPoint(event); drag={id:key.key_id,pointer:event.pointerId,dx:p.x-Number(key.x_units),dy:p.y-Number(key.y_units)}; svg.setPointerCapture(event.pointerId); renderLayout(); renderSelected(); });
    svg.append(group);
  }
}

function selected() { return keys.find(key=>key.key_id===selectedId); }
function renderSelected(force = false) {
  const key=selected(); if(!key) return;
  document.getElementById("selected-title").textContent=key.label;
  document.getElementById("selected-id").textContent=key.key_id;
  for (const [name, input] of Object.entries(fields)) {
    // Do not reformat active edits, including spinner changes or incomplete decimals.
    if (!force && document.activeElement === input && input.dataset.keyId === key.key_id) continue;
    input.value = Number(key[keyFieldNames[name]]).toFixed(3);
    input.dataset.keyId = key.key_id;
    input.setCustomValidity("");
  }
}

function commitKeyField(name, input, normalize = false) {
  const key = selected();
  if (!key || input.dataset.keyId !== key.key_id) return;
  // Merely focusing/blurring a rounded display must not round stored precision.
  if (normalize && input.value === Number(key[keyFieldNames[name]]).toFixed(3)) return;
  const value = input.value.trim() === "" ? NaN : Number(input.value);
  if (!Number.isFinite(value)) {
    input.setCustomValidity("请输入有效数字；空白不会修改键位。");
    if (normalize) {
      input.value = Number(key[keyFieldNames[name]]).toFixed(3);
      input.setCustomValidity("");
      message("layout-message", "无效数字已恢复；键位没有被修改。", "error");
    }
    return;
  }
  key[keyFieldNames[name]] = name === "w" || name === "h" ? Math.max(.1, value) : Math.max(0, value);
  layoutDirty = true;
  input.setCustomValidity("");
  renderLayout();
  if (normalize) input.value = Number(key[keyFieldNames[name]]).toFixed(3);
  renderSelected();
}

function renderMarkers(anchors) {
  markerRoot.replaceChildren();
  const markerAnchors = Array.isArray(anchors)
    ? [...anchors].sort((left, right) => Number(left.marker_id) - Number(right.marker_id))
    : [];
  if (markerAnchors.length === 0) {
    const empty = document.createElement("p");
    empty.className = "hint marker-empty";
    empty.textContent = "当前键位图没有配置 Marker。";
    markerRoot.append(empty);
    return;
  }
  for (const anchor of markerAnchors) {
    const markerId = Number(anchor.marker_id);
    if (!Number.isInteger(markerId) || markerId < 0 || markerId > 49) continue;
    const link = document.createElement("a");
    link.href = `/api/setup/marker/${markerId}.png`;
    link.download = `aruco_4x4_50_id_${markerId}.png`;
    const image = document.createElement("img");
    image.src = `/api/setup/marker/${markerId}.png?size=160`;
    image.alt = `Marker ${markerId}`;
    const label = document.createElement("span");
    label.textContent = `ID ${markerId} · ${anchor.key_id || "未指定键位"}`;
    link.append(image, label);
    markerRoot.append(link);
  }
}

svg.addEventListener("pointermove",event=>{ if(!drag||event.pointerId!==drag.pointer)return; const key=keys.find(item=>item.key_id===drag.id); const p=svgPoint(event); key.x_units=Math.max(0,Math.round((p.x-drag.dx)*100)/100); key.y_units=Math.max(0,Math.round((p.y-drag.dy)*100)/100); layoutDirty=true; renderLayout(); renderSelected(); });
svg.addEventListener("pointerup",()=>{drag=null;}); svg.addEventListener("pointercancel",()=>{drag=null;});

for (const [name,input] of Object.entries(fields)) {
  input.addEventListener("input", () => input.setCustomValidity(""));
  input.addEventListener("change", () => commitKeyField(name, input));
  input.addEventListener("blur", () => commitKeyField(name, input, true));
}

async function loadLayout() {
  const state=await request("/api/setup/layout"); profile=state.profile; keys=profile.keys.map(key=>({...key})); selectedId=keys[0]?.key_id; document.getElementById("layout-revision").textContent=`inventory ${state.inventory_revision.slice(0,10)}…`; renderLayout(); renderSelected(); renderMarkers(profile.anchors);
}

function savedComplete(step) { return setupState?.steps?.[step]?.complete === true; }
function readyForNext(step = currentStep) {
  if (!setupState || !statusFresh) return false;
  if (step === "camera") return cameraState?.current?.running === true;
  if (step === "layout") return savedComplete("layout") && !layoutDirty;
  if (step === "anchor") return savedComplete("anchor") && !setupState.camera_revalidation_required;
  if (step === "contact") return savedComplete("contact") && !setupState.camera_revalidation_required;
  if (step === "apply") return savedComplete("apply") && !setupState.restart_required;
  return step === "inspect";
}
function maySkip(step = currentStep) {
  return statusFresh && ["layout","anchor","contact","apply"].includes(step)
    && savedComplete(step) && (step === "layout" || !setupState?.camera_revalidation_required)
    && (step !== "apply" || !setupState?.restart_required);
}
function updateActionReadiness() {
  for (const id of actionButtonIds) document.getElementById(id).disabled = actionBusy;
  const disable = (id, condition) => { document.getElementById(id).disabled = actionBusy || condition; };
  disable("save-layout", !profile);
  disable("start-anchor", !statusFresh || setupState?.steps?.anchor?.available !== true);
  disable("reset-anchor", !statusFresh || setupState?.steps?.anchor?.available !== true);
  disable("finalize-anchor", !statusFresh || anchorState?.ready !== true);
  disable("start-contact", !statusFresh || setupState?.steps?.contact?.available !== true);
  disable("capture-contact", !contactSession?.current_key || !statusFresh || setupState?.camera_revalidation_required === true);
  disable("undo-contact", !(contactSession?.progress?.captured_samples > 0));
  disable("apply-bundle", !statusFresh || setupState?.steps?.apply?.available !== true);
  disable("preview-mirror", !cameraState?.view);
  disable("preview-flip-vertical", !cameraState?.view);
  const next = document.getElementById("guide-next");
  next.disabled = actionBusy || !readyForNext();
  next.textContent = currentStep === "inspect" ? "返回设置总览" : "下一步 →";
  document.getElementById("guide-back").disabled = actionBusy || currentStep === "camera";
  const skip = document.getElementById("guide-skip");
  skip.hidden = !maySkip();
  skip.disabled = actionBusy;
  skip.textContent = currentStep === "layout" ? "跳过，沿用已保存键位图" : "跳过，沿用已有效配置";
  const reasons = {
    camera:"先在高级参数中选择并启动摄像头，再检查画面。",
    layout:layoutDirty ? "有未保存的编辑：保存草稿，或明确跳过沿用已保存键位图。" : "需要服务端已有有效键位图。",
    anchor:"先锁定有效 Anchor，或沿用已有有效参考坐标。",
    contact:"完成全部键位的五次采样，或沿用已有完整触点。",
    apply:setupState?.restart_required ? "新文件已应用；重启相同配置的服务后，刷新状态再继续。" : "需要先完成并应用有效校准。",
  };
  document.getElementById("guide-gate").textContent = readyForNext() ? "已满足本步文件 / 服务条件，可继续。" : (!statusFresh ? "等待读取服务状态；暂不允许继续。" : reasons[currentStep] || "");
}

function stopActivity() {
  navigationEpoch += 1;
  stopAnchorPolling();
  cancelCaptureCountdown();
}
function showStep(step, guided = true) {
  if (!steps.some(item => item.id === step)) return;
  stopActivity();
  currentStep = step; tutorialMode = guided;
  renderNavigation();
  if (!document.hidden && !actionBusy) void refreshStatus();
}
function showOverview() {
  stopActivity(); currentStep = null; tutorialMode = false;
  renderNavigation();
  if (!document.hidden && !actionBusy) void refreshStatus();
}
function renderNavigation() {
  const panel = currentStep === null ? "overview-panel"
    : ["camera","anchor","contact"].includes(currentStep) ? "calibration-panel"
    : currentStep === "layout" ? "layout-panel" : currentStep + "-panel";
  for (const id of ["overview-panel","layout-panel","calibration-panel","apply-panel","inspect-panel"]) {
    document.getElementById(id).classList.toggle("active", id === panel);
  }
  document.getElementById("tutorial-shell").hidden = currentStep === null;
  document.getElementById("guide-controls").hidden = currentStep === null || !tutorialMode;
  for (const id of ["camera","anchor","contact"]) document.getElementById(id + "-actions").hidden = currentStep !== id;
  const index = steps.findIndex(item => item.id === currentStep);
  if (index >= 0) {
    document.getElementById("guide-title").textContent = steps[index].title;
    document.getElementById("guide-description").textContent = steps[index].description;
    document.getElementById("guide-position").textContent = tutorialMode ? "教程 · " + (index + 1) + " / " + steps.length : "按模块调整 · 不会自动开始采样";
  }
  const root = document.getElementById("guide-steps"); root.replaceChildren();
  for (const [position, step] of steps.entries()) {
    const node = document.createElement("li");
    node.className = step.id === currentStep ? "current" : savedComplete(step.id) ? "saved" : "";
    node.textContent = (position + 1) + " · " + step.label;
    if (step.id === currentStep) node.setAttribute("aria-current", "step");
    root.append(node);
  }
  updateActionReadiness(); updatePreviewVisibility();
}
function nextStep(skip = false) {
  if (actionBusy || !(skip ? maySkip() : readyForNext())) return;
  if (skip) message("setup-message", "已沿用服务端已保存的设置。本次跳过没有保存、采样或改写文件。");
  const index = steps.findIndex(step => step.id === currentStep);
  if (index >= steps.length - 1) showOverview();
  else showStep(steps[index + 1].id, true);
}
function shortRevision(value) { return typeof value === "string" ? value.slice(0,10) + "…" : "—"; }
function knownCount(value) { return Number.isFinite(value) && value >= 0 ? String(value) : "—"; }
function renderOverview() {
  const root = document.getElementById("overview-cards"); root.replaceChildren();
  function card(title, value, detail, step) {
    const node = document.createElement("article"); node.className = "card overview-card";
    const heading = document.createElement("h3"); heading.textContent = title;
    const main = document.createElement("strong"); main.textContent = value;
    const description = document.createElement("p"); description.textContent = detail; description.className = "hint";
    node.append(heading, main, description);
    if (step) { const button=document.createElement("button"); button.textContent="查看 / 调整"; button.addEventListener("click",()=>showStep(step,false)); node.append(button); }
    root.append(node);
  }
  const camera = cameraState?.current;
  card("摄像头", camera ? (camera.running ? "已运行" : "未运行") : "未读取",
    camera ? "设备 #" + camera.device_index + " · " + camera.backend : "请连接服务后选择摄像头。", "camera");
  const active = setupState?.active;
  const staging = setupState?.staging;
  const activePrefix=setupState?.restart_required ? "已应用（待重启）的" : "正在使用的";
  if(active?.layout)card(activePrefix + "键位图", active.layout.valid ? knownCount(active.layout.key_count) + " 个键" : "文件不可用",
    (active.layout.layout_id || "名称未读取") + " · " + shortRevision(active.layout.content_hash), "layout");
  if(active?.anchor)card(activePrefix + " Anchor", active.anchor.valid ? "文件有效" : "需要检查",
    knownCount(active.anchor.marker_count) + " 个 Marker · " + shortRevision(active.anchor.revision), "anchor");
  if(active?.contact)card(activePrefix + "触点", knownCount(active.contact.completed_keys) + " / " + knownCount(active.contact.total_keys) + " 键",
    knownCount(active.contact.captured_samples) + " / " + knownCount(active.contact.total_samples) + " 次采样 · " + (active.contact.valid ? "文件有效" : "需要检查"), "contact");
  if(active?.model)card(activePrefix + "3D 键盘模型", active.model.valid ? knownCount(active.model.key_count) + " 个键" : "文件不可用",
    "模型 " + shortRevision(active.model.revision) + (active.model.valid ? " · 文件有效" : " · 需要检查"), "inspect");
  const draft = [];
  if (staging?.layout) draft.push("键位图 " + knownCount(staging.layout.key_count) + " 键");
  if (staging?.anchor) draft.push("Anchor " + (staging.anchor.valid ? "有效" : "待检查"));
  if (staging?.contact) draft.push("触点 " + knownCount(staging.contact.captured_samples) + " / " + knownCount(staging.contact.total_samples) + " 次");
  if(draft.length)card("隔离的设置草稿", "已有 staging 文件", draft.join(" · "), "apply");
  const warnings = [...(setupState?.warnings || [])];
  if (!statusFresh) warnings.unshift("未能读取最新状态；已有内容仅代表上次成功读取，继续按钮已暂停。");
  if (setupState?.camera_revalidation_required) warnings.push("摄像头或输入方向已变化：需重新注册并锁定 Anchor，之后再采样 / 应用。");
  if (setupState?.restart_required) warnings.push("新文件已应用，实时服务尚需重启。");
  const warningRoot=document.getElementById("overview-warnings");
  warningRoot.hidden=!warnings.length; warningRoot.textContent=warnings.join(" ");
  const applyText = setupState?.restart_required ? "已应用新文件；请重启实时服务，再刷新本页。"
    : setupState?.steps?.apply?.available ? "staging 校准已满足应用条件。确认后才会替换正在使用的文件。"
    : savedComplete("apply") ? "当前已在使用有效配置。没有新草稿需要应用，可以直接继续检查效果。"
    : "还没有可应用的完整草稿。先完成键位图、Anchor 和全部触点。";
  document.getElementById("apply-summary").textContent=applyText;
  document.getElementById("inspect-summary").textContent=setupState?.restart_required ? "注意：先重启，运行页面才会使用新文件。" : "以下为人工观测步骤，不会自动宣布精度验收成功。";
}

function applyPreviewView(view) {
  if (!view) return;
  setupViewport.classList.toggle("mirrored", view.mirror_preview === true);
  setupViewport.classList.toggle("flipped-vertical", view.flip_vertical_preview === true);
  document.getElementById("preview-mirror").checked = view.mirror_preview === true;
  document.getElementById("preview-flip-vertical").checked = view.flip_vertical_preview === true;
}
async function loadPreviewConfig() {
  const config = await request("/api/config");
  // A slower initial config response must not overwrite a newer camera/view read.
  if (!cameraState?.view) applyPreviewView(config);
}
let statusFresh = false;
async function refreshStatus(afterAction = false) {
  // Serialize old GET snapshots before writes, and refuse competing refreshes
  // during a mutation. Only the operation's own post-write refresh is admitted.
  if (actionBusy && !afterAction) return;
  if (statusPromise) return statusPromise;
  statusPromise = (async()=>{
    const results = await Promise.allSettled([request("/api/setup/status"),request("/api/camera"),request("/api/setup/contact")]);
    statusFresh = results[0].status === "fulfilled";
    if (statusFresh) {
      setupState=results[0].value;
      anchorState=setupState.registration;
      renderAnchor(anchorState);
    } else message("setup-message","无法读取设置总览：" + results[0].reason.message,"error");
    if (results[1].status === "fulfilled") {
      cameraState=results[1].value; applyPreviewView(cameraState.view);
      const current=cameraState.current;
      document.getElementById("camera-summary").textContent=current ? "设备 #" + current.device_index + " · " + current.backend + " · " + (current.running ? "正在运行" : "尚未运行") : "摄像头状态未知";
      if (currentStep === "camera") document.getElementById("calibration-status").textContent=current?.running ? "摄像头运行中" : "等待摄像头";
    } else { cameraState=null; document.getElementById("camera-summary").textContent="摄像头接口不可用；请在高级参数检查运行服务。"; }
    if (results[2].status === "fulfilled") renderContact(results[2].value);
    renderOverview(); renderNavigation();
  })().finally(()=>{statusPromise=null;});
  return statusPromise;
}
function startStatusPolling() {
  clearInterval(statusTimer);
  if (document.hidden) return;
  statusTimer=setInterval(()=>{if(!actionBusy && !anchorObservePromise) void refreshStatus();},2500);
}
async function actionWithFeedback(action) {
  return runAction(async()=>{
    try { await action(); await refreshStatus(true); }
    catch(error) { message("setup-message",error.message,"error"); await refreshStatus(true); }
  });
}
function renderAnchor(state) {
  anchorState=state;
  const root=document.getElementById("anchor-progress"); root.replaceChildren();
  if (!state) {
    const text=savedComplete("anchor") ? "已有有效 Anchor，可沿用并跳过。只有主动开始新注册才会改写草稿。" : "尚未开始观察。点击开始后才会采集 Marker。";
    document.getElementById("anchor-explanation").textContent=text;
    if(currentStep==="anchor")document.getElementById("calibration-status").textContent=savedComplete("anchor")?"已有有效 Anchor":"未开始注册";
    updateActionReadiness(); return;
  }
  for (const marker of state.markers || []) {
    const row=document.createElement("div"); row.className="progress-item " + (marker.ready ? "ready" : "");
    const label=document.createElement("span"); label.textContent="ID " + marker.marker_id + " · " + marker.key_id;
    const count=document.createElement("strong");
    const required=Number(marker.required_samples);
    const raw=Number(marker.sample_count);
    const completed=Number(marker.completion_count ?? marker.sample_count);
    count.textContent=Number.isFinite(required) && required>0 && Number.isFinite(completed) ? Math.max(0,Math.min(completed,required)) + " / " + required : "待观测";
    const hint=document.createElement("small");
    const reason=marker.hint || marker.reason || (marker.ready ? "位置稳定，已就绪" : marker.stable===false || marker.status==="unstable" ? "数量可能已足够，但位置仍不稳定；保持键盘和摄像头不动。" : "继续观察对应定位块。");
    hint.textContent=(Number.isFinite(raw) ? "有效观测 " + raw + " 次 · " : "") + reason;
    row.append(label,count,hint);root.append(row);
  }
  document.getElementById("anchor-explanation").textContent=state.ready ? "定位块已满足当前稳定性要求，可以锁定 Anchor。" : state.hint || state.reason || "每个定位块都需要足够且稳定的观测。计数满额不等于已经可以锁定。";
  if(currentStep==="anchor")document.getElementById("calibration-status").textContent=state.ready?"Anchor 可锁定":"等待稳定观测";
  updateActionReadiness();
}
function observeAnchors() {
  if (actionBusy || document.hidden || currentStep !== "anchor") return Promise.resolve();
  if (anchorObservePromise) return anchorObservePromise;
  const epoch=navigationEpoch;
  anchorObservePromise = request("/api/setup/anchor/observe",{method:"POST",body:"{}"})
    .then(state=>{if(epoch===navigationEpoch && !document.hidden && currentStep==="anchor")renderAnchor(state);})
    .catch(error=>{if(epoch===navigationEpoch)message("setup-message",error.message,"error");})
    .finally(()=>{ anchorObservePromise=null; });
  return anchorObservePromise;
}
function beginAnchor(reset) {
  if (actionBusy) return;
  const newLineage=reset || !anchorState;
  if(newLineage && !confirm("开始新的 Marker 注册会清除 staging 中已有的 Anchor 和触点草稿。正在使用的配置不受影响。确定开始？"))return;
  stopAnchorPolling();
  const epoch=navigationEpoch;
  void actionWithFeedback(async()=>{
    renderAnchor(await request("/api/setup/anchor/start",{method:"POST",body:JSON.stringify({reset})}));
    if(epoch===navigationEpoch && currentStep==="anchor" && !document.hidden)startAnchorPolling();
  });
}
function renderContact(state) {
  contactSession=state;
  const current=state?.current_key;
  const fallback=setupState?.staging?.contact || setupState?.active?.contact;
  const complete=fallback?.valid && ["complete","draft_complete"].includes(fallback.status);
  document.getElementById("current-key").textContent=current ? current.label + " · " + current.key_id : complete ? "已有完整触点" : fallback?.resume_available ? "已有采样草稿 · 点击恢复" : "等待开始 / 恢复";
  const progress=state?.progress || fallback;
  document.getElementById("contact-progress").textContent=progress ? knownCount(progress.captured_samples) + " / " + knownCount(progress.total_samples) + " 次 · " + knownCount(progress.completed_keys) + " / " + knownCount(progress.total_keys) + " 键" : "尚未启动采样";
  updateActionReadiness();
}

document.getElementById("begin-tutorial").addEventListener("click",()=>showStep("camera",true));
document.getElementById("return-overview").addEventListener("click",showOverview);
document.getElementById("refresh-overview").addEventListener("click",()=>{void refreshStatus();});
document.querySelectorAll("[data-open-step]").forEach(button=>button.addEventListener("click",()=>showStep(button.dataset.openStep,false)));
document.getElementById("guide-back").addEventListener("click",()=>{if(!actionBusy){const index=steps.findIndex(item=>item.id===currentStep);if(index>0)showStep(steps[index-1].id,true);}});
document.getElementById("guide-next").addEventListener("click",()=>nextStep(false));
document.getElementById("guide-skip").addEventListener("click",()=>nextStep(true));

for(const id of ["preview-mirror","preview-flip-vertical"])document.getElementById(id).addEventListener("change",()=>{
  if(actionBusy)return;
  const view={mirror_preview:document.getElementById("preview-mirror").checked,flip_vertical_preview:document.getElementById("preview-flip-vertical").checked};
  void actionWithFeedback(async()=>{
  cameraState=await request("/api/camera/view",{method:"POST",body:JSON.stringify(view)});
  applyPreviewView(cameraState.view);message("setup-message","已保存预览方向，校准数据保持不变。","success");
});});
document.getElementById("save-layout").addEventListener("click",()=>{stopAnchorPolling();void actionWithFeedback(async()=>{
  if(!confirm("保存键位图草稿后，Anchor 与触点需要重新验证。确定保存？"))return;
  const saved=await request("/api/setup/layout",{method:"PUT",body:JSON.stringify({...profile,keys})});
  profile=saved.profile;keys=profile.keys.map(key=>({...key}));layoutDirty=false;
  message("layout-message","键位图草稿已保存。请按教程检查 Anchor 与触点。","success");
  renderLayout();renderSelected();renderMarkers(profile.anchors);
});});
document.getElementById("start-anchor").addEventListener("click",()=>beginAnchor(false));
document.getElementById("reset-anchor").addEventListener("click",()=>beginAnchor(true));
document.getElementById("finalize-anchor").addEventListener("click",()=>{
  if(actionBusy || !anchorState?.ready || !confirm("锁定新的 Anchor 会清空其旧坐标系下的 staging 触点。确定锁定？"))return;
  stopAnchorPolling();void actionWithFeedback(async()=>{const result=await request("/api/setup/anchor/finalize",{method:"POST",body:"{}"});message("setup-message","Anchor 已锁定：" + shortRevision(result.reference.revision),"success");});
});
document.getElementById("start-contact").addEventListener("click",()=>{stopAnchorPolling();void actionWithFeedback(async()=>{renderContact(await request("/api/setup/contact/start",{method:"POST",body:"{}"}));message("setup-message","已明确开始 / 恢复触点采样。","success");});});
document.getElementById("capture-contact").addEventListener("click",event=>{
  if(actionBusy)return;
  const button=event.currentTarget;const controller=new AbortController();const epoch=navigationEpoch;captureCountdownController=controller;
  void runAction(async()=>{
    try {
      for(let n=3;n>0;n--){button.textContent=n+"…";await countdownDelay(1000,controller.signal);}
      if(epoch!==navigationEpoch || document.hidden || currentStep!=="contact")return;
      captureCountdownController=null;button.textContent="采样中…";
      const result=await request("/api/setup/contact/capture",{method:"POST",body:JSON.stringify({key_id:null})});
      renderContact(result.session);message("setup-message","Frame "+result.frame_id+" 采样成功。","success");await refreshStatus(true);
    }catch(error){if(error.name==="AbortError")message("setup-message","采样倒计时已取消。");else message("setup-message",error.message,"error");}
    finally{if(captureCountdownController===controller)captureCountdownController=null;button.textContent="3 秒后采样";}
  });
});
document.getElementById("undo-contact").addEventListener("click",()=>{void actionWithFeedback(async()=>{const result=await request("/api/setup/contact/undo",{method:"POST",body:"{}"});renderContact(result.session);});});
document.getElementById("reset-contact").addEventListener("click",()=>{if(actionBusy || !confirm("确定清空 staging 触点草稿？正在使用的配置不受影响。"))return;void actionWithFeedback(async()=>{await request("/api/setup/contact/reset",{method:"POST",body:"{}"});renderContact({status:"not_started"});message("setup-message","触点草稿已清空。","success");});});
document.getElementById("apply-bundle").addEventListener("click",()=>{if(actionBusy || !confirm("将完整 staging 校准应用到运行配置？之后需要重启实时服务。"))return;void actionWithFeedback(async()=>{const result=await request("/api/setup/apply",{method:"POST",body:"{}"});message("setup-message","已应用 "+result.key_count+" 键。请重启实时服务后刷新本页。","success");});});

window.addEventListener("keydown",event=>{if(event.key==="Escape")cancelCaptureCountdown();});
window.addEventListener("blur",cancelCaptureCountdown);
document.addEventListener("visibilitychange",()=>{
  if(document.hidden){stopActivity();clearInterval(statusTimer);}
  else {void refreshStatus();startStatusPolling();}
  updatePreviewVisibility();
});
window.addEventListener("pagehide",()=>{stopActivity();clearInterval(statusTimer);setupPreview.removeAttribute("src");});
window.addEventListener("beforeunload",()=>{stopActivity();clearInterval(statusTimer);setupPreview.removeAttribute("src");});

loadLayout().catch(error=>message("layout-message",error.message,"error"));
void refreshStatus();
loadPreviewConfig().catch(error=>message("setup-message","无法读取预览设置："+error.message,"error"));
startStatusPolling();
