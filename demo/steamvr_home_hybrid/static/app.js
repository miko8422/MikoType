(() => {
  const byId = (id) => document.getElementById(id);
  const capabilitiesRoot = byId("capabilities");
  const exportButton = byId("export-button");
  const exportResult = byId("export-result");
  const manifestLink = byId("manifest-link");
  const bundleLink = byId("bundle-link");
  let viewer = null;
  let manifest = null;
  let patternIndex = -1;
  let animationTimer = null;

  const capabilityLabels = {
    asset_export: "Render Model 资产转换",
    state_replay: "SceneState 离线回放",
    mock_compositor: "3D / Overlay 模拟合成",
    driver_build: "SteamVR Driver 构建",
    steamvr_runtime: "SteamVR Runtime / Home",
  };

  function shortHash(value) {
    return value ? `${String(value).slice(0, 10)}…${String(value).slice(-6)}` : "—";
  }

  function setGate(id, status, note) {
    const element = document.querySelector(`[data-gate="${id}"]`);
    if (!element) return;
    element.dataset.state = status;
    const copy = element.querySelector("small");
    if (copy && note) copy.textContent = note;
  }

  function setExportResult(state, titleCopy, detailCopy) {
    exportResult.dataset.state = state;
    exportResult.replaceChildren();
    const title = document.createElement("b");
    const detail = document.createElement("small");
    title.textContent = titleCopy;
    detail.textContent = detailCopy;
    exportResult.append(title, detail);
  }

  function renderCapabilities(status) {
    const platform = status.platform || {};
    byId("platform-pill").textContent = `${platform.system || "unknown"} / ${platform.machine || "unknown"}`;
    const capabilities = status.capabilities || {};
    capabilitiesRoot.replaceChildren();
    for (const [id, label] of Object.entries(capabilityLabels)) {
      const raw = capabilities[id];
      const available = typeof raw === "boolean" ? raw : Boolean(raw?.available);
      const reason = typeof raw === "object" && raw ? raw.reason : "";
      const item = document.createElement("div");
      item.className = "capability";
      item.dataset.state = available ? "available" : "unavailable";
      item.innerHTML = `<span class="dot"></span><div><b>${label}</b><small></small></div><em>${available ? "READY" : "BLOCKED"}</em>`;
      item.querySelector("small").textContent = reason || (available ? "可在本机执行" : "需要 Windows + SteamVR");
      capabilitiesRoot.append(item);
    }
    const runtime = typeof capabilities.steamvr_runtime === "boolean"
      ? capabilities.steamvr_runtime : Boolean(capabilities.steamvr_runtime?.available);
    const system = platform.system || "本机";
    byId("truth-title").textContent = runtime ? "检测到 SteamVR Runtime" : `${system} 离线验证模式`;
    byId("truth-copy").textContent = runtime
      ? "仍需在头显内完成 Home 呈现验收"
      : "SteamVR Home 不在本机运行；页面结果不会冒充实机验证";

    manifestLink.setAttribute("aria-disabled", status.export_current ? "false" : "true");
    bundleLink.setAttribute("aria-disabled", status.windows_source_bundle_current ? "false" : "true");
    if (status.windows_source_bundle_current) {
      setExportResult("ok", "资产与源码包已验证", "可重新生成或直接下载；Windows DLL 仍未构建");
    }

    for (const gate of status.gates || []) setGate(gate.id, gate.status, gate.note || gate.label);
  }

  function centerFor(key) {
    const center = key.center || [0, 0, 0];
    return [Number(center[0]) || 0, Number(center[2]) || 0];
  }

  function highlightPattern() {
    if (!viewer || !manifest?.keys?.length) return;
    const preferred = ["key_a", "space", "f12", "delete_top_right", "arrow_up", "right_control"];
    const candidates = preferred
      .map((id) => manifest.keys.find((key) => key.key_id === id))
      .filter(Boolean);
    const source = candidates.length ? candidates : manifest.keys;
    patternIndex = (patternIndex + 1) % source.length;
    const directKey = source[patternIndex];
    const [x, z] = centerFor(directKey);
    const neighbors = manifest.keys
      .filter((key) => key.key_id !== directKey.key_id)
      .map((key) => {
        const [kx, kz] = centerFor(key);
        return {key, distance: Math.hypot(kx - x, kz - z)};
      })
      .sort((left, right) => left.distance - right.distance)
      .slice(0, 4);
    const states = new Map([[directKey.key_id, {intensity: 1, direct: true}]]);
    neighbors.forEach(({key}, index) => states.set(key.key_id, {
      intensity: Math.max(0.2, 0.53 - index * 0.08),
      direct: false,
    }));
    viewer.setHighlights(states);
    byId("active-highlight").textContent = `${directKey.label} · ${directKey.key_id} / neighbor glow`;
  }

  async function loadViewer() {
    manifest = await fetch("/api/model/manifest", {cache: "no-store"}).then((response) => {
      if (!response.ok) throw new Error(`manifest HTTP ${response.status}`);
      return response.json();
    });
    byId("key-count").textContent = String(manifest.key_count);
    const minimum = manifest.geometry?.bounds?.min || [];
    const maximum = manifest.geometry?.bounds?.max || [];
    byId("model-size").textContent = minimum.length === 3 && maximum.length === 3
      ? `${((maximum[0] - minimum[0]) * 1000).toFixed(1)} × ${((maximum[2] - minimum[2]) * 1000).toFixed(1)} × ${((maximum[1] - minimum[1]) * 1000).toFixed(1)} mm`
      : "—";
    byId("model-revision").textContent = shortHash(manifest.model_revision);
    byId("model-sha").textContent = shortHash(manifest.model?.sha256);
    viewer = await window.AdaptiveKeyboard3DViewer.mount({
      container: byId("viewer"),
      modelUrl: `/api/model/keyboard.glb?sha=${manifest.model.sha256}`,
      expectedRevision: manifest.model_revision,
      onInspect(detail) {
        const keyId = detail.key_id || "unknown";
        const key = manifest.keys.find((item) => item.key_id === keyId);
        byId("active-highlight").textContent = key ? `${key.label} · ${keyId}` : keyId;
      },
    });
    byId("viewer-status").textContent = `${viewer.keyCount} keys · GLB revision/hash verified · fixed demo pose only`;
    highlightPattern();
  }

  byId("outline-toggle").addEventListener("change", (event) => viewer?.setDebug(event.target.checked));
  byId("highlight-button").addEventListener("click", highlightPattern);
  byId("animate-button").addEventListener("click", (event) => {
    if (animationTimer) {
      clearInterval(animationTimer);
      animationTimer = null;
      event.currentTarget.setAttribute("aria-pressed", "false");
      event.currentTarget.textContent = "自动巡检";
    } else {
      highlightPattern();
      animationTimer = setInterval(highlightPattern, 760);
      event.currentTarget.setAttribute("aria-pressed", "true");
      event.currentTarget.textContent = "停止巡检";
    }
  });

  exportButton.addEventListener("click", async () => {
    exportButton.disabled = true;
    exportButton.textContent = "正在校验与转换…";
    setExportResult("idle", "正在处理", "只写入 Demo output，不修改生产 GLB");
    try {
      const response = await fetch("/api/export", {method: "POST"});
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      const files = payload.file_count ?? payload.outputs?.length ?? "—";
      setExportResult("ok", "资产与源码包已验证", `${files} files · 仍未构建 Windows DLL`);
      manifestLink.setAttribute("aria-disabled", "false");
      bundleLink.setAttribute("aria-disabled", "false");
      setGate("asset_bundle_validated", "passed", "已验证");
      setGate("windows_source_bundle_validated", "passed", "源码就绪");
    } catch (error) {
      manifestLink.setAttribute("aria-disabled", "true");
      bundleLink.setAttribute("aria-disabled", "true");
      setGate("asset_bundle_validated", "ready", "需重新验证");
      setGate("windows_source_bundle_validated", "ready", "待生成");
      setExportResult("error", "生成失败", String(error.message || error));
    } finally {
      exportButton.disabled = false;
      exportButton.textContent = "重新验证并生成源码包";
    }
  });

  for (const link of [manifestLink, bundleLink]) {
    link.addEventListener("click", (event) => {
      if (link.getAttribute("aria-disabled") === "true") event.preventDefault();
    });
  }

  Promise.all([
    fetch("/api/status", {cache: "no-store"}).then((response) => response.json()).then(renderCapabilities),
    loadViewer(),
  ]).catch((error) => {
    byId("viewer-status").textContent = `初始化失败：${error.message || error}`;
    byId("truth-title").textContent = "Demo 初始化失败";
  });
})();
