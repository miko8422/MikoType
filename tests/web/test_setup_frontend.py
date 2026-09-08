"""Exercise setup numeric editing/preview behavior without saved keyboard changes."""

import shutil
import subprocess

import pytest

from deskvision.web.app import STATIC_DIR


pytestmark = pytest.mark.unit


def test_numeric_edits_preserve_focus_and_do_not_turn_blank_into_zero():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is optional; required for JavaScript setup unit test")
    harness = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function element(id) {
  return { id, value: '', dataset: {}, attributes: {}, listeners: {},
    classList: { active: false, contains() { return this.active; }, toggle() {} },
    setCustomValidity(value) { this.validationMessage = value; },
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name] || null; },
    hasAttribute(name) { return Object.hasOwn(this.attributes, name); },
    removeAttribute(name) { delete this.attributes[name]; },
    set src(value) { this.attributes.src = value; },
    replaceChildren() {}, append() {},
    addEventListener(name, handler) { this.listeners[name] = handler; },
  };
}
const nodes = new Map();
const preview = element('preview');
preview.attributes.src = '/stream.mjpg';
const document = {
  hidden: false, activeElement: null,
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, element(id)); return nodes.get(id); },
  createElementNS() { return element('svg-child'); },
  querySelectorAll() { return []; }, addEventListener() {},
};
document.getElementById('setup-viewport').querySelector = () => preview;
const sandbox = { document, window: { addEventListener() {} } };
vm.createContext(sandbox);
const source = fs.readFileSync(process.argv[1], 'utf8');
vm.runInContext(source.slice(0, source.lastIndexOf('\nloadLayout().catch')), sandbox);
vm.runInContext(`profile={anchors:[]};keys=[{key_id:'a',label:'A',x_units:0,y_units:0,width_units:1,height_units:1},{key_id:'b',label:'B',x_units:1,y_units:0,width_units:2,height_units:1}];selectedId='a';renderSelected();`, sandbox);
const width = nodes.get('w-input');
document.activeElement = width;
width.value = '1.25';
width.listeners.change();
assert.equal(width.value, '1.25');
assert.equal(sandbox.selected().width_units, 1.25);
width.value = '1.';
sandbox.renderSelected();
assert.equal(width.value, '1.'); // unrelated rerender preserves incomplete decimal
width.value = '';
width.listeners.change();
assert.equal(sandbox.selected().width_units, 1.25);
assert.notEqual(width.validationMessage, '');
width.listeners.blur();
assert.equal(width.value, '1.250');
assert.equal(sandbox.selected().width_units, 1.25);
width.value = 'Infinity';
width.listeners.blur();
assert.equal(width.value, '1.250');
width.value = '1.26'; // spinner increment
width.listeners.change();
assert.equal(width.value, '1.26');
assert.equal(sandbox.selected().width_units, 1.26);
width.value = '0';
width.listeners.blur();
assert.equal(sandbox.selected().width_units, .1); // existing minimum-clamp semantics
vm.runInContext(`selectedId='b';renderSelected();`, sandbox);
assert.equal(width.value, '2.000'); // changing key must not retain previous focused draft
vm.runInContext(`keys[1].width_units=2.123456;renderSelected(true);`, sandbox);
width.listeners.blur();
assert.equal(sandbox.selected().width_units, 2.123456); // focus/blur does not round storage
assert.equal(preview.hasAttribute('src'), false); // layout tab is hidden preview
document.getElementById('calibration-panel').classList.active = true;
sandbox.updatePreviewVisibility();
assert.equal(preview.getAttribute('src'), '/stream.mjpg');
document.hidden = true;
sandbox.updatePreviewVisibility();
assert.equal(preview.hasAttribute('src'), false);
"""
    result = subprocess.run(
        [node, "-e", harness, str(STATIC_DIR / "setup.js")],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
