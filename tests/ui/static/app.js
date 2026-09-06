"use strict";

const canvas = document.getElementById("frame-canvas");
const placeholder = document.getElementById("frame-placeholder");
const alertBox = document.getElementById("alert");
const previewState = document.getElementById("preview-state");

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
}

function setStatus(status) {
  setText("status-text", status);
  document.getElementById("status").dataset.state = status;
}

let displayFrames = 0;
let displayWindowStarted = performance.now();

function handleDisplayedFrame(frame) {
  displayFrames += 1;
  const elapsedMs = performance.now() - displayWindowStarted;
  if (elapsedMs >= 500) {
    setText("display-fps", ((displayFrames * 1000) / elapsedMs).toFixed(1) + " fps");
    displayFrames = 0;
    displayWindowStarted = performance.now();
  }
  setText("display-frame-id", frame.frame_id);
  setText("display-latency", frame.display_latency_ms.toFixed(1) + " ms");
  setText("transport-latency", frame.transport_ms.toFixed(1) + " ms");
  setText("dropped-display", frame.dropped_before_decode ? "yes" : "0");
}

function handlePreviewState(state) {
  setText("preview-state", state);
  if (state.startsWith("websocket")) {
    previewState.dataset.state = "ok";
  } else if (state.startsWith("http_fallback")) {
    previewState.dataset.state = "fallback";
  } else {
    previewState.dataset.state = "waiting";
  }
}

async function refreshMetrics() {
  try {
    const response = await fetch("/api/metrics", { cache: "no-store" });
    if (!response.ok) throw new Error("metrics HTTP " + response.status);
    const data = await response.json();
    setStatus(data.status);
    setText("source-label", data.source_mode);
    setText("frame-id", data.frame_id == null ? "—" : data.frame_id);
    setText(
      "capture-fps",
      data.capture_fps == null ? "—" : data.capture_fps.toFixed(1) + " fps",
    );
    setText(
      "frame-age",
      data.frame_age_ms == null ? "—" : data.frame_age_ms.toFixed(1) + " ms",
    );
    setText("frames-captured", data.frames_captured == null ? "—" : data.frames_captured);
    setText("overwritten", data.overwritten_frames == null ? "—" : data.overwritten_frames);
    setText("read-failures", data.read_failures == null ? "—" : data.read_failures);
    const errorText = data.startup_error || data.last_error || "";
    alertBox.hidden = !errorText;
    alertBox.textContent = errorText;
  } catch (error) {
    setStatus("ui_error");
    alertBox.hidden = false;
    alertBox.textContent = error.message;
  }
}

const preview = new LowLatencyPreview({
  canvas,
  placeholder,
  onFrame: handleDisplayedFrame,
  onStatus: handlePreviewState,
});

refreshMetrics();
preview.start();
window.setInterval(refreshMetrics, 500);
