"""Static and JavaScript checks; no camera, browser or production data required."""

import shutil
import subprocess

import pytest

from deskvision.web.app import STATIC_DIR


pytestmark = pytest.mark.unit


def test_waiting_overlay_respects_hidden_and_labels_distinguish_raw_tracking():
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "[hidden] { display: none !important; }" in css
    assert 'id="raw-fingertip-count"' in page
    assert "已映射指尖（绿色）" in page
    assert "WINDOWS-LOCAL" not in page


def test_raw_hands_render_without_marker_pose_or_invented_highlights():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is optional; required for JavaScript rendering unit test")
    harness = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const arcs = [];
const segments = [];
const ctx = {
  scale() {}, clearRect() {}, beginPath() {}, stroke() {}, fill() {},
  moveTo(x, y) { segments.push([x, y]); }, lineTo() {},
  arc(x, y, radius) { arcs.push([x, y, radius]); },
  createRadialGradient() { return { addColorStop() {} }; },
};
const nodes = new Map();
const document = { getElementById(id) {
  if (!nodes.has(id)) nodes.set(id, {
    textContent: '', hidden: false, style: { setProperty() {} },
    getContext() { return ctx; },
    getBoundingClientRect() { return { width: 100, height: 50 }; },
    removeAttribute() {},
  });
  return nodes.get(id);
}};
const sandbox = { document, window: { devicePixelRatio: 1, addEventListener() {}, clearTimeout() {} }, URL: {} };
vm.createContext(sandbox);
const source = fs.readFileSync(process.argv[1], 'utf8').replace(/\nstart\(\);\s*$/, '\n');
vm.runInContext(source, sandbox);
const state = {
  hands: [{ landmarks: Array.from({length: 21}, () => ({x: .2, y: .4})) }],
  fingertips: [], key_highlights: [], keyboard: { pose: { usable: false } },
  diagnostics: { status: 'degraded' },
};
sandbox.renderState(state);
assert.equal(nodes.get('empty-message').hidden, true);
assert.equal(nodes.get('raw-fingertip-count').textContent, '5');
assert.equal(nodes.get('fingertip-count').textContent, '0');
assert.equal(nodes.get('highlight-count').textContent, '0');
assert.equal(arcs.length, 5);
assert.equal(segments.length, 21);
assert.deepEqual(arcs[0], [80, 20, 4.5]); // mirror matches video, not key-map coordinates
assert.equal(sandbox.rawFingertips({hands:[{landmarks:[{x:null,y:0}]}]}).length, 0);
sandbox.clearLiveDisplay('stale');
assert.equal(nodes.get('raw-fingertip-count').textContent, '0');
assert.equal(nodes.get('empty-message').hidden, false);
"""
    result = subprocess.run(
        [node, "-e", harness, str(STATIC_DIR / "app.js")],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
