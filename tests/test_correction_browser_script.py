"""Execute the actual correction editor with controlled image/pointer event order."""
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from tula.domain.enums import DeclarationClass
from tula.domain.models import Analysis, PackageFacts, Scan

ROOT = Path(__file__).parents[1]
JS = ROOT / "src/tula/web/static/review.js"

HARNESS = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const mode=process.argv[2], supplied=JSON.parse(process.argv[3]||'null'), images=[], elements={};
let drawn=null,clearCount=0,disabledWrites=0;
class Element {
 constructor(){this._value='';this.attrs={};this.listeners={};this.observers=[];this.options=[];this.hidden=false;this.textContent='';this.width=300;this.height=150;this._disabled=false;this.validityMessage='';}
 set value(value){this._value=String(value);}get value(){return this._value;}
 set disabled(value){disabledWrites++;this._disabled=value;}get disabled(){return this._disabled;}
 setAttribute(name,value){this.attrs[name]=String(value);for(const obs of this.observers)if(obs.fields.includes(name))queueMicrotask(obs.callback);}
 getAttribute(name){return this.attrs[name]??null;}
 removeAttribute(name){delete this.attrs[name];for(const obs of this.observers)if(obs.fields.includes(name))queueMicrotask(obs.callback);}
 addEventListener(name,callback){this.listeners[name]=callback;}
 setCustomValidity(message){this.validityMessage=message;}
 reportValidity(){this.reported=true;return !this.validityMessage;}
 getContext(){return{clearRect(){drawn=null;clearCount++;},drawImage(value){drawn=value;},strokeRect(){},fillRect(){}};}
 getBoundingClientRect(){return{left:0,top:0,width:200,height:120};}
 setPointerCapture(id){this.capture=id;}
 hasPointerCapture(id){return this.capture===id;}
 releasePointerCapture(id){if(this.capture===id){this.capture=null;this.onlostpointercapture?.({pointerId:id});}}
}
const element=id=>elements[id]||(elements[id]=new Element());
const form=element('correction-form'),canvas=element('correction-canvas'),frame=element('correction-frame'),kind=element('correction-kind');
form.setAttribute('action','/inspections/CASE/review');
frame.options=[{value:'0'},{value:'1'}];frame.value='0';
kind.value='net_quantity';kind.selectedOptions=[{dataset:{frame:'0'}}];
const transcript={net_quantity:{raw:'Net wt 200 g',bbox:[10,10,80,50]},retail_sale_price:{raw:'MRP 180',bbox:[20,20,150,100]}};
element('declaration-data').textContent=JSON.stringify(transcript);
global.document={getElementById:id=>mode==='readonly'&&id==='correction-form'?null:element(id)};
global.MutationObserver=class{constructor(callback){this.callback=callback;}observe(target,options){target.observers.push({callback:this.callback,fields:options.attributeFilter});}};
global.Image=class{
 constructor(){this.naturalWidth=0;this.naturalHeight=0;images.push(this);}
 set src(value){this.url=value;}get src(){return this.url;}
 load(width=100,height=60){this.naturalWidth=width;this.naturalHeight=height;this.onload();}
 fail(){this.onerror();}
};
const box=()=>JSON.parse(element('correction-bbox').value);
const typeBounds=values=>{values.forEach((value,index)=>{element('bbox-'+index).value=value;});element('bbox-0').oninput();};
const submit=()=>{const event={preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}};form.listeners.submit(event);return event;};
const pointer=(x,y,id=1,extra={})=>({clientX:x,clientY:y,pointerId:id,button:0,isPrimary:true,preventDefault(){this.prevented=true;},...extra});
const flush=async()=>{for(let n=0;n<3;n++)await new Promise(setImmediate);};
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
(async()=>{
 if(mode==='readonly'){assert.equal(images.length,0);return;}
 assert.equal(element('correction-raw').value,'Net wt 200 g');
 assert.deepEqual(box(),[]);assert.equal(canvas.width,1);assert.equal(drawn,null);
 assert(element('correction-source-status').textContent.includes('Loading'));
 assert(submit().stopped);assert(frame.validityMessage);
 if(mode==='load-failure'){
   images[0].fail();assert(element('correction-source-status').textContent.includes('could not be loaded'));
   assert.equal(element('correction-retry-image').hidden,false);
   typeBounds([0,0,50,40]);assert.deepEqual(box(),[]);assert(submit().stopped);
   canvas.onpointerdown(pointer(20,20));canvas.onpointermove(pointer(80,80));assert.deepEqual(box(),[]);
   element('correction-retry-image').onclick();assert.equal(images.length,2);assert.equal(canvas.width,1);
   images[1].load();assert.deepEqual(box(),[10,10,80,50]);assert.equal(element('correction-retry-image').hidden,true);
   assert(!submit().stopped);assert.equal(disabledWrites,0);return;
 }
 if(mode==='stale-source'){
   frame.value='1';frame.onchange();const fresh=images[1];assert.equal(fresh.url,'/inspections/CASE/frames/1');
   fresh.load(200,120);assert.equal(drawn,fresh);assert.deepEqual(box(),[]);
   images[0].load(900,800);images[0].fail();assert.equal(canvas.width,200);assert.equal(drawn,fresh);
   assert(element('correction-source-status').textContent.includes('200 × 120'));
   assert.equal(element('correction-retry-image').hidden,true);assert(submit().stopped);
   typeBounds([0,0,200,120]);assert(!submit().stopped);
   frame.value='0';frame.onchange();assert.deepEqual(box(),[]);assert.equal(drawn,null);assert.equal(canvas.width,1);
   assert(submit().stopped);return;
 }
 if(mode==='stale-kind'){
   kind.value='retail_sale_price';kind.selectedOptions=[{dataset:{frame:'1'}}];kind.onchange();
   assert.equal(element('correction-raw').value,'MRP 180');assert.equal(frame.value,'1');
   images[0].load(100,60);assert.equal(drawn,null);assert.deepEqual(box(),[]);
   images[1].load(200,120);assert.deepEqual(box(),[20,20,150,100]);assert(!submit().stopped);return;
 }
 if(mode==='pending-load'){
   form.setAttribute('data-review-pending','true');await flush();
   images[0].load();assert.equal(canvas.width,1);assert.deepEqual(box(),[]);assert.equal(drawn,null);
   assert(submit().stopped);
   form.removeAttribute('data-review-pending');await flush();
   assert.deepEqual(box(),[10,10,80,50]);assert.equal(drawn,images[0]);assert(!submit().stopped);
   assert.equal(disabledWrites,0);return;
 }
 images[0].load(mode==='prefill-outside'?40:100,mode==='prefill-outside'?30:60);
 if(mode==='prefill-outside'){
   assert.deepEqual(box(),[]);assert(submit().stopped);assert.equal(element('bbox-2').value,'80');
   typeBounds([0,0,40,30]);assert(!submit().stopped);return;
 }
 assert.deepEqual(box(),[10,10,80,50]);assert(!submit().stopped);
 assert.equal(element('bbox-2').max,100);assert.equal(element('bbox-3').max,60);
 if(mode==='invalid'){
   typeBounds(supplied);assert.deepEqual(box(),[]);const result=submit();assert(result.prevented&&result.stopped);
   assert(element('bbox-0').validityMessage);assert(element('correction-geometry-error').textContent.includes('whole-pixel'));
   typeBounds([0,0,100,60]);assert(!submit().stopped);assert.equal(element('bbox-0').validityMessage,'');
 } else if(mode==='drag'){
   canvas.onpointerdown(pointer(10,20));assert(submit().stopped);
   canvas.onpointermove(pointer(180,100,2));assert.deepEqual(box(),[]);
   canvas.onpointerup(pointer(120,80));assert.deepEqual(box(),[5,10,60,40]);assert(!submit().stopped);
   canvas.onpointerdown(pointer(20,20));canvas.onpointermove(pointer(999,-40));assert.deepEqual(box(),[10,0,100,10]);
   canvas.onpointercancel(pointer(999,-40));assert.deepEqual(box(),[5,10,60,40]);
   canvas.onpointermove(pointer(50,50));assert.deepEqual(box(),[5,10,60,40]);
   canvas.onpointerdown(pointer(10,10));canvas.onpointermove(pointer(80,80));canvas.onlostpointercapture(pointer(80,80));
   assert.deepEqual(box(),[5,10,60,40]);
   canvas.onpointerdown(pointer(0,0,1,{button:2}));assert.deepEqual(box(),[5,10,60,40]);
   canvas.onpointerdown(pointer(0,0,1,{isPrimary:false}));assert.deepEqual(box(),[5,10,60,40]);
   canvas.onpointerdown(pointer(20,20));canvas.onpointerup(pointer(20,20));assert.deepEqual(box(),[]);assert(submit().stopped);
 } else if(mode==='pending-editor'){
   const before=element('correction-bbox').value, beforeRaw=element('correction-raw').value;
   form.setAttribute('data-review-pending','true');await flush();
   canvas.onpointerdown(pointer(10,10));canvas.onpointermove(pointer(80,80));canvas.onpointerup(pointer(80,80));
   kind.onchange();frame.onchange();element('bbox-0').oninput();element('correction-retry-image').onclick();
   assert.equal(element('correction-bbox').value,before);assert.equal(element('correction-raw').value,beforeRaw);
   assert.equal(images.length,1);assert(submit().stopped);
   form.removeAttribute('data-review-pending');await flush();assert(!submit().stopped);
 } else if(mode==='pending-mid-drag'){
   canvas.onpointerdown(pointer(20,20));canvas.onpointermove(pointer(80,80));const current=element('correction-bbox').value;
   form.setAttribute('data-review-pending','true');await flush();
   canvas.onpointermove(pointer(100,100));canvas.onpointercancel(pointer(100,100));assert.equal(element('correction-bbox').value,current);
   form.removeAttribute('data-review-pending');await flush();canvas.onpointermove(pointer(160,110));assert.equal(element('correction-bbox').value,current);
 }
 assert.equal(disabledWrites,0);
 assert.equal(element('declaration-data').textContent,JSON.stringify(transcript));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""


def execute(mode, bounds=None):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for correction editor browser-script tests")
    result = subprocess.run([node, "-e", HARNESS, str(JS), mode, json.dumps(bounds)],
                            capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mode", ["readonly", "load-failure", "stale-source", "stale-kind", "pending-load",
                                  "prefill-outside", "drag", "pending-editor", "pending-mid-drag"])
def test_correction_editor_load_races_pointer_events_and_pending_save(mode):
    execute(mode)


@pytest.mark.parametrize("bounds", [
    ["", 0, 50, 40], [" ", 0, 50, 40], [0, "", 50, 40], [0, 0, "", 40], [0, 0, 50, ""],
    [0, 0, 0, 40], [20, 10, 10, 40], [0, 20, 50, 20], [-1, 0, 50, 40], [0, -1, 50, 40],
    [0, 0, 101, 40], [0, 0, 50, 61], [1.5, 0, 50, 40], [0, 0, 50.2, 40],
    [0, 0, "Infinity", 40], ["NaN", 0, 50, 40],
])
def test_correction_editor_rejects_invalid_pixel_geometry(bounds):
    execute("invalid", bounds)


def test_correction_partial_has_validity_help_status_and_retry_control():
    environment = Environment(loader=FileSystemLoader(ROOT / "src/tula/web/templates"),
                              autoescape=select_autoescape())
    environment.globals["asset_url"] = lambda name: "/static/" + name
    analysis = Analysis(scan=Scan(scan_id="EDITOR-TEST", inspector_id="owner", frames=["source.png"]), package=PackageFacts())
    request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id="owner", role="inspector")))
    html = environment.get_template("_review.html").render(a=analysis, request=request, csrf_token="csrf",
        revisions=[], declaration_labels={DeclarationClass.NET_QUANTITY: "Net quantity"})
    assert 'id="correction-source-status"' in html and 'aria-live="polite"' in html
    assert 'id="correction-retry-image" type="button"' in html and "Retry source image" in html
    assert html.count('step="1" inputmode="numeric"') == 4
    assert html.count('aria-describedby="correction-bounds-help correction-geometry-error"') == 4
    assert 'id="correction-geometry-error" class="error-box" role="alert"' in html
