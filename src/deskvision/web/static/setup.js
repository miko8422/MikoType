"use strict";

let profile = null;
let keys = [];
let selectedId = null;
let drag = null;
let anchorTimer = null;
let anchorObservePromise = null;
let actionBusy = false;
let captureCountdownController = null;

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
];

const svg = document.getElementById("keyboard-svg");
const fields = Object.fromEntries(["x","y","w","h"].map(name => [name, document.getElementById(`${name}-input`)]));
const markerRoot = document.getElementById("markers");
const setupViewport = document.getElementById("setup-viewport");

async function request(path, options = {}) {
  const response = await fetch(path, { cache:"no-store", headers:{"Content-Type":"application/json"}, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `${response.status} ${response.statusText}`);
  return payload;
}

function setActionBusy(busy) {
  actionBusy = busy;
  for (const id of actionButtonIds) document.getElementById(id).disabled = busy;
}

async function runAction(action) {
  if (actionBusy) return;
  setActionBusy(true);
  try {
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
function renderSelected() {
  const key=selected(); if(!key) return;
  document.getElementById("selected-title").textContent=key.label;
  document.getElementById("selected-id").textContent=key.key_id;
  fields.x.value=Number(key.x_units).toFixed(3); fields.y.value=Number(key.y_units).toFixed(3);
  fields.w.value=Number(key.width_units).toFixed(3); fields.h.value=Number(key.height_units).toFixed(3);
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

svg.addEventListener("pointermove",event=>{ if(!drag||event.pointerId!==drag.pointer)return; const key=keys.find(item=>item.key_id===drag.id); const p=svgPoint(event); key.x_units=Math.max(0,Math.round((p.x-drag.dx)*100)/100); key.y_units=Math.max(0,Math.round((p.y-drag.dy)*100)/100); renderLayout(); renderSelected(); });
svg.addEventListener("pointerup",()=>{drag=null;}); svg.addEventListener("pointercancel",()=>{drag=null;});

for (const [name,input] of Object.entries(fields)) input.addEventListener("change",()=>{ const key=selected(); if(!key)return; const value=Number(input.value); if(!Number.isFinite(value))return; const field={x:"x_units",y:"y_units",w:"width_units",h:"height_units"}[name]; key[field]=name==="w"||name==="h"?Math.max(.1,value):Math.max(0,value); renderLayout(); renderSelected(); });

async function loadLayout() {
  const state=await request("/api/setup/layout"); profile=state.profile; keys=profile.keys.map(key=>({...key})); selectedId=keys[0]?.key_id; document.getElementById("layout-revision").textContent=`inventory ${state.inventory_revision.slice(0,10)}…`; renderLayout(); renderSelected(); renderMarkers(profile.anchors);
}

async function loadPreviewConfig() {
  const config = await request("/api/config");
  setupViewport.classList.toggle("mirrored", Boolean(config.mirror_preview));
}

document.getElementById("save-layout").addEventListener("click",()=>{ stopAnchorPolling(); void runAction(async()=>{ try{ const saved=await request("/api/setup/layout",{method:"PUT",body:JSON.stringify({...profile,keys})}); profile=saved.profile; keys=profile.keys.map(key=>({...key})); message("layout-message","已保存。Anchor 和 Contact Map 必须重新验证。","success"); renderLayout(); renderMarkers(profile.anchors); }catch(error){message("layout-message",error.message,"error");} }); });

function renderAnchor(state) {
  const root=document.getElementById("anchor-progress"); root.replaceChildren();
  for(const marker of state.markers||[]){ const row=document.createElement("div"); row.className=`progress-item ${marker.ready?"ready":""}`; const label=document.createElement("span"); label.textContent=`ID ${marker.marker_id} · ${marker.key_id}`; const progress=document.createElement("strong"); progress.textContent=`${marker.sample_count}/${marker.required_samples}`; row.append(label,progress); root.append(row); }
  document.getElementById("calibration-status").textContent=state.ready?"Anchor 可锁定":state.reason||"采集中";
}

function observeAnchors(){
  if (actionBusy) return Promise.resolve();
  if (anchorObservePromise) return anchorObservePromise;
  anchorObservePromise = request("/api/setup/anchor/observe",{method:"POST",body:"{}"})
    .then(renderAnchor)
    .catch(error=>message("setup-message",error.message,"error"))
    .finally(()=>{ anchorObservePromise=null; });
  return anchorObservePromise;
}
document.getElementById("start-anchor").addEventListener("click",()=>{ stopAnchorPolling(); void runAction(async()=>{try{renderAnchor(await request("/api/setup/anchor/start",{method:"POST",body:"{}"}));startAnchorPolling();}catch(error){message("setup-message",error.message,"error");}});});
document.getElementById("reset-anchor").addEventListener("click",()=>{ stopAnchorPolling(); void runAction(async()=>{try{renderAnchor(await request("/api/setup/anchor/start",{method:"POST",body:JSON.stringify({reset:true})}));startAnchorPolling();}catch(error){message("setup-message",error.message,"error");}});});
document.getElementById("finalize-anchor").addEventListener("click",()=>{ stopAnchorPolling(); void runAction(async()=>{try{const result=await request("/api/setup/anchor/finalize",{method:"POST",body:"{}"});message("setup-message",`Anchor 已保存：${result.reference.revision.slice(0,10)}…`,"success");}catch(error){message("setup-message",error.message,"error");}});});

function renderContact(state){ const current=state.current_key; document.getElementById("current-key").textContent=current?`${current.label} · ${current.key_id}`:"完成/未开始"; const p=state.progress; document.getElementById("contact-progress").textContent=p?`${p.captured_samples}/${p.total_samples} samples · ${p.completed_keys}/${p.total_keys} keys`:state.status; }
document.getElementById("start-contact").addEventListener("click",()=>{void runAction(async()=>{try{renderContact(await request("/api/setup/contact/start",{method:"POST",body:"{}"}));message("setup-message","触点校准已开始/恢复。","success");}catch(error){message("setup-message",error.message,"error");}});});
document.getElementById("capture-contact").addEventListener("click",event=>{ if(actionBusy)return; const button=event.currentTarget; const controller=new AbortController(); captureCountdownController=controller; void runAction(async()=>{ try{for(let n=3;n>0;n--){button.textContent=`${n}…`;await countdownDelay(1000,controller.signal);}captureCountdownController=null;button.textContent="采样中…";const result=await request("/api/setup/contact/capture",{method:"POST",body:JSON.stringify({key_id:null})});renderContact(result.session);message("setup-message",`Frame ${result.frame_id} 采样成功。`,"success");}catch(error){if(error.name==="AbortError")message("setup-message","采样倒计时已取消。");else message("setup-message",error.message,"error");}finally{if(captureCountdownController===controller)captureCountdownController=null;button.textContent="3 秒后采样";}}); });
document.getElementById("undo-contact").addEventListener("click",()=>{void runAction(async()=>{try{const result=await request("/api/setup/contact/undo",{method:"POST",body:"{}"});renderContact(result.session);}catch(error){message("setup-message",error.message,"error");}});});
document.getElementById("reset-contact").addEventListener("click",()=>{if(!confirm("确定清空 staging 触点草稿？生产 Bundle 不受影响。"))return;void runAction(async()=>{try{await request("/api/setup/contact/reset",{method:"POST",body:"{}"});renderContact({status:"not_started"});message("setup-message","staging 草稿已清空。","success");}catch(error){message("setup-message",error.message,"error");}});});
document.getElementById("apply-bundle").addEventListener("click",()=>{if(!confirm("将已完成的 staging 校准应用到生产 Bundle？实时服务需要重启。"))return;void runAction(async()=>{try{const result=await request("/api/setup/apply",{method:"POST",body:"{}"});message("setup-message",`已应用 ${result.key_count} 键模型 ${result.model_revision.slice(0,10)}…，请重启运行服务。`,"success");}catch(error){message("setup-message",error.message,"error");}});});

window.addEventListener("keydown",event=>{if(event.key==="Escape")cancelCaptureCountdown();});
window.addEventListener("blur",cancelCaptureCountdown);
document.addEventListener("visibilitychange",()=>{if(document.hidden)cancelCaptureCountdown();});

document.querySelectorAll(".tab").forEach(tab=>tab.addEventListener("click",()=>{document.querySelectorAll(".tab").forEach(node=>node.classList.toggle("active",node===tab));document.querySelectorAll(".panel").forEach(panel=>panel.classList.toggle("active",panel.id===tab.dataset.panel));}));

loadLayout().catch(error=>message("layout-message",error.message,"error")); request("/api/setup/contact").then(renderContact).catch(()=>{});
loadPreviewConfig().catch(error=>message("setup-message",`无法读取预览镜像设置：${error.message}`,"error"));
