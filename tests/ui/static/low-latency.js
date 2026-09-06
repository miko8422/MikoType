"use strict";

// Test-only preview transport. It keeps one latest packet and never builds a
// browser-side frame queue, so a slow decode drops old frames instead of
// increasing visible latency.
(function attachLowLatencyPreview(global) {
  const HEADER_BYTES = 4;

  class LowLatencyPreview {
    constructor({ canvas, placeholder, onFrame, onStatus }) {
      this.canvas = canvas;
      this.placeholder = placeholder;
      this.context = canvas.getContext("2d", { alpha: false, desynchronized: true });
      this.onFrame = onFrame || (() => {});
      this.onStatus = onStatus || (() => {});
      this.socket = null;
      this.latestPacket = null;
      this.rendering = false;
      this.stopped = false;
      this.mode = "websocket";
      this.fallbackRunning = false;
    }

    start() {
      this.stopped = false;
      if (!global.WebSocket) {
        this.startFallback("websocket_unavailable");
        return;
      }
      const protocol = global.location.protocol === "https:" ? "wss:" : "ws:";
      this.onStatus("connecting");
      this.socket = new global.WebSocket(protocol + "//" + global.location.host + "/api/stream");
      this.socket.binaryType = "arraybuffer";
      this.socket.onopen = () => this.onStatus("websocket");
      this.socket.onmessage = (event) => this.receive(event.data);
      this.socket.onerror = () => this.startFallback("websocket_error");
      this.socket.onclose = () => {
        if (!this.stopped && this.mode === "websocket") {
          this.startFallback("websocket_closed");
        }
      };
    }

    stop() {
      this.stopped = true;
      if (this.socket) this.socket.close();
    }

    async receive(data) {
      try {
        const buffer = data instanceof ArrayBuffer ? data : await data.arrayBuffer();
        const packet = this.parsePacket(buffer);
        if (this.latestPacket !== null) packet.dropped_before_decode = true;
        this.latestPacket = packet;
        this.pumpRender();
      } catch (error) {
        this.onStatus("decode_error");
      }
    }

    parsePacket(buffer) {
      const view = new DataView(buffer);
      const headerLength = view.getUint32(0);
      const headerBytes = new Uint8Array(buffer, HEADER_BYTES, headerLength);
      const header = JSON.parse(new TextDecoder().decode(headerBytes));
      return {
        header,
        jpeg: buffer.slice(HEADER_BYTES + headerLength),
        received_at_ns: BigInt(Date.now()) * 1000000n,
        dropped_before_decode: false,
      };
    }

    async pumpRender() {
      if (this.rendering || this.latestPacket === null || this.stopped) return;
      this.rendering = true;
      const packet = this.latestPacket;
      this.latestPacket = null;
      try {
        await this.renderPacket(packet);
      } finally {
        this.rendering = false;
        if (this.latestPacket !== null) this.pumpRender();
      }
    }

    async renderPacket(packet) {
      const blob = new Blob([packet.jpeg], { type: "image/jpeg" });
      if (global.createImageBitmap) {
        const bitmap = await global.createImageBitmap(blob);
        this.resizeCanvas(bitmap.width, bitmap.height);
        this.context.drawImage(bitmap, 0, 0);
        bitmap.close();
      } else {
        await this.renderWithImage(blob);
      }
      this.canvas.hidden = false;
      this.placeholder.hidden = true;
      const displayedAtNs = BigInt(Date.now()) * 1000000n;
      const acquiredAtNs = BigInt(packet.header.acquired_at_ns);
      const sentAtNs = BigInt(packet.header.server_sent_at_ns);
      this.onFrame({
        frame_id: packet.header.frame_id,
        source_id: packet.header.source_id,
        acquired_at_ns: acquiredAtNs,
        server_sent_at_ns: sentAtNs,
        displayed_at_ns: displayedAtNs,
        display_latency_ms: Math.max(0, Number(displayedAtNs - acquiredAtNs) / 1000000),
        transport_ms: Math.max(0, Number(sentAtNs - acquiredAtNs) / 1000000),
        dropped_before_decode: packet.dropped_before_decode,
        source_width: Number(packet.header.width) || this.canvas.width,
        source_height: Number(packet.header.height) || this.canvas.height,
      });
    }

    resizeCanvas(width, height) {
      if (this.canvas.width !== width || this.canvas.height !== height) {
        this.canvas.width = width;
        this.canvas.height = height;
      }
    }

    renderWithImage(blob) {
      return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(blob);
        const image = new Image();
        image.onload = () => {
          this.resizeCanvas(image.naturalWidth, image.naturalHeight);
          this.context.drawImage(image, 0, 0);
          URL.revokeObjectURL(url);
          resolve();
        };
        image.onerror = () => {
          URL.revokeObjectURL(url);
          reject(new Error("preview image decode failed"));
        };
        image.src = url;
      });
    }

    startFallback(reason) {
      if (this.stopped || this.fallbackRunning) return;
      this.mode = "fallback";
      if (this.socket) this.socket.close();
      this.onStatus("http_fallback:" + reason);
      this.fallbackRunning = true;
      this.fallbackLoop();
    }

    async fallbackLoop() {
      while (!this.stopped && this.mode === "fallback") {
        try {
          const response = await fetch("/api/frame.jpg?ts=" + Date.now(), { cache: "no-store" });
          if (!response.ok) throw new Error("frame HTTP " + response.status);
          const blob = await response.blob();
          const packet = {
            header: {
              frame_id: Number(response.headers.get("X-Frame-Id")),
              source_id: "http-fallback",
              acquired_at_ns: response.headers.get("X-Frame-Acquired-At-Ns"),
              server_sent_at_ns:
                response.headers.get("X-Frame-Server-Sent-At-Ns") || String(Date.now() * 1000000),
              width: Number(response.headers.get("X-Frame-Width")),
              height: Number(response.headers.get("X-Frame-Height")),
            },
            jpeg: await blob.arrayBuffer(),
            dropped_before_decode: false,
          };
          if (this.latestPacket !== null) packet.dropped_before_decode = true;
          this.latestPacket = packet;
          this.pumpRender();
          await new Promise((resolve) => setTimeout(resolve, 0));
        } catch (error) {
          this.onStatus("http_error");
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
      }
      this.fallbackRunning = false;
    }
  }

  global.LowLatencyPreview = LowLatencyPreview;
})(window);
