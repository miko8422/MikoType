"use strict";

const canvas = document.getElementById("frame-canvas");
const placeholder = document.getElementById("frame-placeholder");
let displayFrames = 0;
let displayWindowStarted = performance.now();

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
}

function handleFrame(frame) {
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

function handleState(state) {
  setText("preview-state", state);
  document.getElementById("preview-state").dataset.state = state.startsWith("websocket") ? "ok" : "fallback";
}

new LowLatencyPreview({
  canvas,
  placeholder,
  onFrame: handleFrame,
  onStatus: handleState,
}).start();
