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
const sandbox = { document, window: { addEventListener() {} }, clearInterval() {} };
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


SETUP_HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function element(id) {
  const classes=new Set();
  return {id,value:'',checked:false,disabled:false,hidden:false,textContent:'',dataset:{},attributes:{},listeners:{},children:[],
    classList:{contains(name){return classes.has(name);},toggle(name,on){if(on)classes.add(name);else classes.delete(name);}},
    setCustomValidity(value){this.validationMessage=value;},
    setAttribute(name,value){this.attributes[name]=String(value);},getAttribute(name){return this.attributes[name]??null;},
    hasAttribute(name){return Object.hasOwn(this.attributes,name);},removeAttribute(name){delete this.attributes[name];},
    set src(value){this.attributes.src=value;},replaceChildren(){this.children=[];},append(...children){this.children.push(...children);},
    addEventListener(name,handler){this.listeners[name]=handler;},
  };
}
const nodes=new Map(); const preview=element('preview');
const docListeners={}, windowListeners={};
const document={hidden:false,activeElement:null,
  getElementById(id){if(!nodes.has(id))nodes.set(id,element(id));return nodes.get(id);},
  createElementNS(){return element('svg');},createElement(){return element('child');},
  querySelectorAll(){return [];},addEventListener(name,handler){docListeners[name]=handler;},
};
document.getElementById('setup-viewport').querySelector=()=>preview;
document.getElementById('overview-panel').classList.toggle('active',true);
const profile={layout_id:'mine',anchors:[{marker_id:1,key_id:'delete'}],keys:[{key_id:'a',label:'A',x_units:0,y_units:0,width_units:1,height_units:1}]};
const overview={schema_version:'mikotype-setup-status-0.1',active:{valid:true,
  layout:{layout_id:'mine',key_count:1,content_hash:'layout-hash',inventory_revision:'inventory',valid:true},
  anchor:{revision:'anchor',marker_count:1,valid:true},
  contact:{revision:'contact',completed_keys:1,total_keys:1,captured_samples:5,total_samples:5,valid:true},
  model:{revision:'model',key_count:1,valid:true}},staging:{},registration:null,
  camera_revalidation_required:false,restart_required:false,warnings:[],
  steps:{layout:{complete:true,available:true},anchor:{complete:true,available:true},contact:{complete:true,available:true},apply:{complete:true,available:false}}};
const camera={current:{device_index:0,backend:'avfoundation',running:true},view:{mirror_preview:false,flip_vertical_preview:false}};
const session={status:'not_started'};
const calls=[]; const intervals=new Map(), delays=new Map(); let timerId=0, allowConfirm=true;
const api={
 '/api/setup/status':overview,'/api/camera':camera,'/api/setup/contact':session,
 '/api/setup/layout':{profile,inventory_revision:'inventory',source:'active'},'/api/config':camera.view,
 '/api/setup/anchor/start':{ready:false,markers:[]},'/api/setup/anchor/observe':{ready:false,markers:[]},
 '/api/setup/contact/start':{status:'collecting',current_key:{key_id:'a',label:'A'},progress:{captured_samples:0,total_samples:5,completed_keys:0,total_keys:1}},
};
const sandbox={document,window:{addEventListener(name,handler){windowListeners[name]=handler;}},AbortController,
  confirm(){return allowConfirm;},
  setInterval(fn){const id=++timerId;intervals.set(id,fn);return id;},clearInterval(id){intervals.delete(id);},
  setTimeout(fn){const id=++timerId;delays.set(id,fn);return id;},clearTimeout(id){delays.delete(id);},
  async fetch(path,options={}){calls.push({path,method:options.method||'GET',body:options.body});
    if(!Object.hasOwn(api,path))return {ok:false,status:404,statusText:'Not Found',async json(){return {};}};
    if(path==='/api/camera/view'){Object.assign(camera.view,JSON.parse(options.body));api[path]=camera;}
    return {ok:true,async json(){return JSON.parse(JSON.stringify(api[path]));}};
  },
};
vm.createContext(sandbox);
const source=fs.readFileSync(process.argv[1],'utf8');
vm.runInContext(source,sandbox);
const run=code=>vm.runInContext(code,sandbox);
const flush=async()=>{for(let n=0;n<30;n++)await Promise.resolve();};
const writes=()=>calls.filter(call=>call.method!=='GET');
"""


def run_setup_js(body):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is optional; required for JavaScript setup tests")
    result = subprocess.run(
        [node, "-e", SETUP_HARNESS + "\n(async()=>{\n" + body + "\n})().catch(error=>{console.error(error);process.exitCode=1;});", str(STATIC_DIR / "setup.js")],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_overview_boot_and_skips_only_read_backend_without_writes():
    run_setup_js(r"""
await flush();
assert.equal(nodes.get('overview-panel').classList.contains('active'),true);
assert.equal(preview.hasAttribute('src'),false);
assert.equal(writes().length,0);
sandbox.showStep('camera',true);await flush();assert.equal(sandbox.readyForNext(),true);
sandbox.nextStep();await flush();assert.equal(run('currentStep'),'layout');
run('layoutDirty=true;updateActionReadiness();');
assert.equal(sandbox.readyForNext(),false); // unsaved editor must not silently count as saved
assert.equal(sandbox.maySkip(),true);
for(const target of ['anchor','contact','apply','inspect']){
 sandbox.nextStep(true);await flush();assert.equal(run('currentStep'),target);
}
assert.equal(writes().length,0); // no start/save/reset/apply on navigation or skip
assert.equal(preview.hasAttribute('src'),false);
sandbox.showOverview();await flush();assert.equal(nodes.get('overview-panel').classList.contains('active'),true);
assert.equal(nodes.get('overview-cards').children[1].children[1].textContent,'1 个键');
""")


def test_next_and_skip_are_gated_by_actual_backend_readiness():
    run_setup_js(r"""
await flush();camera.current.running=false;sandbox.showStep('camera',true);await flush();
assert.equal(sandbox.readyForNext(),false);sandbox.nextStep();assert.equal(run('currentStep'),'camera');
camera.current.running=true;await sandbox.refreshStatus();assert.equal(sandbox.readyForNext(),true);
overview.steps.anchor.complete=false;sandbox.showStep('anchor',true);await flush();
assert.equal(sandbox.readyForNext(),false);assert.equal(sandbox.maySkip(),false);
assert.equal(nodes.get('guide-next').disabled,true);sandbox.nextStep(true);assert.equal(run('currentStep'),'anchor');
overview.steps.anchor.complete=true;overview.camera_revalidation_required=true;await sandbox.refreshStatus();assert.equal(sandbox.maySkip(),false);
overview.camera_revalidation_required=false;overview.restart_required=true;sandbox.showStep('apply',true);await flush();
assert.equal(sandbox.readyForNext(),false);assert.equal(sandbox.maySkip(),false);
assert.equal(writes().length,0);
""")


def test_anchor_progress_saturates_and_explains_unstable_observations():
    run_setup_js(r"""
await flush();sandbox.renderAnchor({ready:false,hint:'保持键盘不动',markers:[{marker_id:1,key_id:'delete',sample_count:48,required_samples:5,completion_count:5,ready:false,stable:false,status:'unstable',hint:'数量已足够，但位置仍不稳定'}]});
const row=nodes.get('anchor-progress').children[0];
assert.equal(row.children[1].textContent,'5 / 5');
assert.match(row.children[2].textContent,/48 次.*不稳定/);
assert.equal(nodes.get('finalize-anchor').disabled,true);
sandbox.renderAnchor({ready:false,markers:[{marker_id:1,key_id:'delete',sample_count:48,required_samples:5,ready:false}]});
assert.equal(nodes.get('anchor-progress').children[0].children[1].textContent,'5 / 5');
""")


def test_registration_requires_confirmation_and_polling_stops_on_exit_or_hidden():
    run_setup_js(r"""
await flush();sandbox.showStep('anchor',true);await flush();allowConfirm=false;sandbox.beginAnchor(false);await flush();assert.equal(writes().length,0);
allowConfirm=true;sandbox.beginAnchor(false);sandbox.beginAnchor(false);await flush();
assert.equal(writes().filter(c=>c.path==='/api/setup/anchor/start').length,1);
assert.notEqual(run('anchorTimer'),null);
sandbox.showStep('layout',true);await flush();assert.equal(run('anchorTimer'),null);
const before=writes().length;await sandbox.observeAnchors();assert.equal(writes().length,before);
sandbox.showStep('anchor',true);await flush();sandbox.startAnchorPolling();document.hidden=true;docListeners.visibilitychange();
assert.equal(run('anchorTimer'),null);assert.equal(preview.hasAttribute('src'),false);
await sandbox.observeAnchors();assert.equal(writes().length,before);
document.hidden=false;docListeners.visibilitychange();await flush();assert.equal(run('anchorTimer'),null); // resuming tab must not auto-calibrate
windowListeners.pagehide();assert.equal(preview.hasAttribute('src'),false);assert.equal(intervals.size,0);
""")


def test_capture_countdown_aborts_on_step_change_without_capture_post():
    run_setup_js(r"""
await flush();sandbox.showStep('contact',true);await flush();sandbox.renderContact(api['/api/setup/contact/start']);
nodes.get('capture-contact').listeners.click({currentTarget:nodes.get('capture-contact')});await flush();
assert.equal(delays.size,1);
sandbox.showOverview();await flush();
assert.equal(delays.size,0);assert.equal(writes().filter(c=>c.path==='/api/setup/contact/capture').length,0);
assert.equal(run('actionBusy'),false);
""")


def test_view_direction_synchronizes_both_axes_without_touching_calibration():
    run_setup_js(r"""
await flush();api['/api/camera/view']=camera;
camera.view.mirror_preview=true;camera.view.flip_vertical_preview=true;await sandbox.refreshStatus();
assert.equal(nodes.get('setup-viewport').classList.contains('mirrored'),true);
assert.equal(nodes.get('setup-viewport').classList.contains('flipped-vertical'),true);
nodes.get('preview-mirror').checked=false;nodes.get('preview-mirror').listeners.change();await flush();
assert.equal(writes().length,1);assert.equal(writes()[0].path,'/api/camera/view');
assert.deepEqual(JSON.parse(writes()[0].body),{mirror_preview:false,flip_vertical_preview:true});
assert.equal(nodes.get('setup-viewport').classList.contains('mirrored'),false);
assert.equal(nodes.get('setup-viewport').classList.contains('flipped-vertical'),true);
""")


def test_pending_old_read_is_serialized_before_view_write_and_new_refresh():
    run_setup_js(r"""
await flush();api['/api/camera/view']=camera;
const originalFetch=sandbox.fetch;let releaseOld;let held=false;
sandbox.fetch=async(path,options)=>{
 if(path==='/api/camera'&&!held){held=true;const snapshot=JSON.parse(JSON.stringify(camera));await new Promise(resolve=>{releaseOld=resolve;});return {ok:true,async json(){return snapshot;}};}
 return originalFetch(path,options);
};
const pending=sandbox.refreshStatus();await flush();
nodes.get('preview-mirror').checked=true;nodes.get('preview-mirror').listeners.change();await flush();
assert.equal(writes().length,0); // mutation waits for any old read first
const countBefore=calls.length;await sandbox.refreshStatus();assert.equal(calls.length,countBefore); // manual competing refresh suppressed
releaseOld();await pending;await flush();
assert.equal(writes().length,1);assert.equal(JSON.parse(writes()[0].body).mirror_preview,true);
assert.equal(camera.view.mirror_preview,true);
assert.equal(nodes.get('preview-mirror').checked,true);
assert.equal(nodes.get('setup-viewport').classList.contains('mirrored'),true);
""")


def test_unknown_or_invalid_files_do_not_become_fake_zeroes_or_complete_drafts():
    run_setup_js(r"""
await flush();overview.active={layout:{valid:false},anchor:{valid:false},contact:{valid:false},model:{valid:false}};
overview.staging={contact:{status:'collecting',captured_samples:2,total_samples:5,completed_keys:0,total_keys:1,valid:true,resume_available:true}};
await sandbox.refreshStatus();
const text=JSON.stringify(nodes.get('overview-cards').children);
assert.equal(text.includes('undefined'),false);assert.equal(text.includes('NaN'),false);
assert.equal(nodes.get('overview-cards').children[1].children[1].textContent,'文件不可用');
assert.equal(nodes.get('current-key').textContent,'已有采样草稿 · 点击恢复');
assert.equal(nodes.get('contact-progress').textContent,'2 / 5 次 · 0 / 1 键');
assert.equal(writes().length,0);
""")


def test_overview_hides_missing_artifacts_but_keeps_camera_and_invalid_files():
    run_setup_js(r"""
await flush();
assert.equal(nodes.get('overview-cards').children.length,5); // camera + existing active artifacts, no staging placeholder
overview.active={valid:false};overview.staging={valid:false};await sandbox.refreshStatus();
assert.equal(nodes.get('overview-cards').children.length,1);
assert.equal(nodes.get('overview-cards').children[0].children[0].textContent,'摄像头');
overview.active={layout:{valid:false}};await sandbox.refreshStatus();
assert.equal(nodes.get('overview-cards').children.length,2);
assert.equal(nodes.get('overview-cards').children[1].children[1].textContent,'文件不可用');
assert.equal(writes().length,0);
""")


def test_applied_disk_artifacts_are_not_called_running_before_restart():
    run_setup_js(r"""
await flush();overview.restart_required=true;await sandbox.refreshStatus();
for(const card of nodes.get('overview-cards').children.slice(1)){
 assert.match(card.children[0].textContent,/^已应用（待重启）的/);
 assert.equal(card.children[0].textContent.includes('正在使用'),false);
}
overview.restart_required=false;await sandbox.refreshStatus();
assert.match(nodes.get('overview-cards').children[1].children[0].textContent,/^正在使用的/);
""")
