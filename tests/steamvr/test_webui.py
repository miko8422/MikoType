"""SteamVR console contracts and client behavior, without a headset or server."""

import shutil
import subprocess

import pytest

from deskvision.web.app import STATIC_DIR


pytestmark = pytest.mark.unit


def test_console_navigation_downloads_and_honest_visibility_boundaries():
    page = (STATIC_DIR / "steamvr.html").read_text(encoding="utf-8")
    script = (STATIC_DIR / "steamvr.js").read_text(encoding="utf-8")
    for route in (
        "/api/steamvr/diagnostics",
        "/api/steamvr/driver-assets.zip",
        "/api/steamvr/bridge-token",
    ):
        assert f'href="{route}"' in page
    assert 'download="mikotype-steamvr-token.txt"' in page
    assert "/api/steamvr/bridge-token" not in script
    assert "连接成功不等于 SteamVR Home 已显示成功" in page
    assert "不是头显截图" in page
    assert "不代表已恢复手部 3D 深度" in page
    assert "不会随现实键盘自动移动" in page
    assert "不是 SteamVR / Pimax 全部系统日志" in page
    assert 'id="collect-runtime-logs"' in page
    assert "innerHTML" not in script
    assert "https://" not in page
    for name in ("index.html", "settings.html", "setup.html"):
        assert 'href="/steamvr"' in (STATIC_DIR / name).read_text(encoding="utf-8")


def test_console_drafts_safe_logs_and_non_overlapping_polling():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is optional; required for JavaScript console unit test")
    harness = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = new Map();
function element(name) {
  return { name, value: '', hidden: false, disabled: false, textContent: '',
    children: [], listeners: {}, attributes: {}, scrollTop: 0, scrollHeight: 100, clientHeight: 100,
    set id(value) { this._id = value; nodes.set(value, this); }, get id() { return this._id; },
    setCustomValidity(value) { this.validationMessage = value; }, reportValidity() {},
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, handler) { this.listeners[name] = handler; },
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
  };
}
const document = { hidden: false, activeElement: null,
  getElementById(id) { if (!nodes.has(id)) { const item = element(id); item.id = id; } return nodes.get(id); },
  createElement(name) { return element(name); }, addEventListener() {},
};
const requests = [];
let pendingResolve = null;
let deferFetch = false;
const status = {
  schema_version:'mikotype-steamvr-0.1', supported_host:false, platform:'darwin',
  bridge:{connected:false,status:'waiting',last_seen_age_ms:null},
  settings:{enabled:false,pose_confirmed:false,x:0,y:.75,z:-.6,yaw:0,pitch:-90,roll:0},
  scene:{fresh:false,pose_usable:false,key_count:82,highlights:0,fingertips:0,model_revision:'r1'},
  logs:[], limitations:['Windows headset verification pending'],
};
const sandbox = { document, AbortController, Date,
  window:{setTimeout(){return 1;},clearTimeout(){},addEventListener(){}},
  fetch: async (url, options) => {
    requests.push({url, options});
    if (deferFetch && url === '/api/steamvr/status') await new Promise((resolve)=>{pendingResolve=resolve;});
    return {ok:true,json:async()=>status};
  },
};
vm.createContext(sandbox);
const source = fs.readFileSync(process.argv[1], 'utf8').replace(/\nstartSteamVRPage\(\);\s*$/, '\n');
vm.runInContext(source,sandbox);
async function main() {
  sandbox.createPoseControls();
  sandbox.renderStatus(status);
  assert.equal(nodes.get('host-warning').hidden,false);
  assert.equal(nodes.get('collect-runtime-logs').disabled,true);
  sandbox.renderStatus({...status, scene:{...status.scene,age_ms:null,revisions_match:false}});
  assert.match(nodes.get('scene-status').textContent,/等待有效场景 \/ 版本验证/);
  assert.doesNotMatch(nodes.get('scene-status').textContent,/模型版本不匹配/);
  sandbox.renderStatus({...status, scene:{...status.scene,age_ms:20,revisions_match:false}});
  assert.match(nodes.get('scene-status').textContent,/模型版本不匹配/);
  assert.equal(nodes.get('pose-pitch').value,'-90');
  const x = nodes.get('pose-x');
  document.activeElement=x;
  x.value='1.'; x.listeners.input();
  sandbox.renderStatus(status);
  assert.equal(x.value,'1.'); // polling never discards focused partial decimals
  document.activeElement=null;
  sandbox.renderStatus(status);
  assert.equal(x.value,'1.'); // drafts also survive blur
  x.value=''; x.listeners.input();
  assert.throws(()=>sandbox.readPoseDraft()); // blank is not zero
  await sandbox.savePose('confirm');
  assert.equal(requests.length,0); // invalid input cannot enable the display
  await sandbox.savePose('hide');
  assert.equal(requests.length,1); // stop still works with an invalid draft
  const hide = JSON.parse(requests[0].options.body);
  assert.equal(hide.enabled,false);
  assert.equal(hide.pose_confirmed,false);
  assert.equal(hide.x,0); // hide uses saved, not broken draft values
  assert.equal(x.value,'');
  x.value='1.25'; x.listeners.input();
  await sandbox.savePose('confirm');
  const confirm = JSON.parse(requests[1].options.body);
  assert.equal(confirm.x,1.25);
  assert.equal(confirm.enabled,true);
  assert.equal(confirm.pose_confirmed,true);
  assert.equal(x.value,'1.25');
  const manyLogs=Array.from({length:205},(_,id)=>({id,time:'now',source:'bridge',level:'info',event:'test',message:'<img src=x onerror=bad()>',details:{html:'<script>bad()</script>'}}));
  sandbox.renderLogs(manyLogs);
  const list=nodes.get('vr-logs');
  assert.equal(list.children.length,200);
  assert.equal(list.children[0].children[1].textContent,'<img src=x onerror=bad()>');
  assert.equal(list.children[0].children[2].children[1].textContent,JSON.stringify({html:'<script>bad()</script>'},null,2));
  vm.runInContext('logsPaused=true',sandbox);
  sandbox.renderLogs([]);
  assert.equal(list.children.length,200); // paused view is stable
  vm.runInContext('logsPaused=false;renderLogs(pendingLogs,true)',sandbox);
  assert.equal(list.children.length,1);
  assert.equal(list.children[0].className,'log-empty');
  deferFetch=true;
  const first=sandbox.pollStatus();
  const before=requests.length;
  await sandbox.pollStatus();
  assert.equal(requests.length,before); // no overlapping polling requests
  x.value='2.5'; x.listeners.input();
  await sandbox.savePose('confirm');
  pendingResolve(); await first;
  assert.equal(x.value,'2.5'); // an older pending GET cannot undo a successful PUT
  assert.equal(requests.some(({url})=>url.includes('bridge-token')),false);
  assert.equal(requests.some(({url})=>url.includes('collect-logs')),false); // never automatic
  sandbox.renderStatus({...status,supported_host:true,platform:'win32'});
  assert.equal(nodes.get('collect-runtime-logs').disabled,false);
  await sandbox.collectRuntimeLogs();
  assert.equal(requests.at(-1).url,'/api/steamvr/collect-logs');
  assert.equal(requests.at(-1).options.body,'{}');
  assert.equal(nodes.get('runtime-logs-details').open,true);
}
main().catch(error=>{process.stderr.write(error.stack);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", harness, str(STATIC_DIR / "steamvr.js")],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
