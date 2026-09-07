"""Execute the shipped capture script against a small deterministic browser surface.

Network and canvas decoding are controlled; File objects carry original bytes.
Real image/server/SQLite validation is covered by test_capture_drafts.py.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).parents[1] / "src/tula/web/static/capture.js"

HARNESS = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert'),{File}=require('buffer');
const mode=process.argv[2], id='a'.repeat(32), calls=[];
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.options=[];this.value='';this.checked=false;this.style={};this.disabled=false;this.textContent='';this.width=1600;this.height=1000;this.events={};this.classList={add(){},remove(){}};}
 append(...nodes){this.children.push(...nodes);} replaceChildren(...nodes){this.children=nodes;}
 setAttribute(key,value){this[key]=value;} add(option){this.options.push(option);}
 addEventListener(key,action){this.events[key]=action;} focus(){} showModal(){this.open=true;} close(){this.open=false;}
 getContext(){return{translate(){},rotate(){},drawImage(){},strokeRect(){},fillRect(){}};}
 getBoundingClientRect(){return{left:0,top:0,width:this.width,height:this.height};} setPointerCapture(){}
}
const elements={},element=id=>elements[id]||(elements[id]=new Element()),complete=element('complete');
element('capture-initial-data').textContent='{}';
element('capture-drafts').dataset={enabled:'true',requestedDraft:mode.startsWith('resume')?id:''};
for(const[key,values]of Object.entries({lane:['field','citizen'],'context-category':['unknown','food'],'context-bundle':['unknown','single'],'context-shape':['unknown','rectangular'],'context-origin':['unknown','imported','domestic'],'rescan-target':['','expiry_date']})){element(key).options=values.map(value=>({value}));element(key).value=values[0];}
complete.checked=true;element('confirm-context').checked=true;
global.document={getElementById:element,createElement:tag=>new Element(tag),querySelector:()=>complete};
global.window={tulaCSRF:()=> 'csrf',history:{replaceState(){}},addEventListener(){}};
global.location={assign(){}};Object.defineProperty(global,'navigator',{configurable:true,value:{geolocation:{getCurrentPosition(success,failure){if(mode==='location-denied')failure({code:1});else success({coords:{latitude:12.9715987,longitude:77.5945661,accuracy:14.4}});}}}});global.URL={createObjectURL:()=> 'blob:original',revokeObjectURL(){}};
global.File=File;global.crypto={randomUUID:()=> require('crypto').randomUUID()};
global.Option=function(text,value){this.text=text;this.value=value;};
global.Image=class{set src(value){this.url=value;}async decode(){if(this.url.startsWith('blob:'))throw Error('HEIC unsupported');this.width=1600;this.height=1000;}};
global.FormData=class{
 constructor(form){this.values=new Map();if(form){for(const key of ['lane','region','concerns'])this.set(key,element(key).value);if(complete.checked)this.set('complete','1');this.set('parent_scan_id',element('parent-scan-id').value);if(!element('parent-revision').disabled)this.set('parent_revision',element('parent-revision').value);if(!element('rescan-target').disabled)this.set('rescan_target',element('rescan-target').value);}}
 append(key,value){this.values.set(key,[...(this.values.get(key)||[]),value]);}set(key,value){this.values.set(key,[value]);}delete(key){this.values.delete(key);}get(key){return(this.values.get(key)||[])[0];}
};
global.localStorage={getItem:()=>null,setItem(){},removeItem(){}};
const bytes=Buffer.from('exact original HEIC bytes for client behavior'), original=new File([bytes],'original.heic',{type:'image/heic'});
const quality={width:3200,height:2000,issues:[],metrics:{median_brightness:120,contrast_p99_p01:180,denoised_laplacian_variance:80}};
const details={title:'Saved package',lane:'citizen',region:'Saved district',geo:[12.971599,77.594566],concerns:'milk, soy',parent_scan_id:'PARENT',parent_revision:2,rescan_target:'expiry_date',dimensions:{pdp_width_mm:85,pdp_height_mm:125},context:{category:'food',bundle_type:'single',shape:'rectangular',is_imported:true,category_confirmed:false}};
let rows=[],revision=0,failSave=false,failAnalyze=false,holdSave=false,releaseSave=null,staleDelete=false,submitted;
const reply=(data,status=200)=>({ok:status>=200&&status<300,status,json:async()=>data});
const summary=()=>({id,revision,title:'Saved package',image_count:1,expires_at:2000000000});
global.fetch=async(url,options={})=>{
 calls.push({url,options});
 if(url==='/v1/capture/preview')return reply({quality,original_size:[3200,2000],preview:'data:image/jpeg;base64,preview'});
 if(url==='/v1/capture/drafts')return reply({drafts:rows});
 if(url.includes('/images/'))return mode==='resume-stale'?reply({detail:'Draft changed; reload its latest saved version.'},409):{ok:true,status:200,blob:async()=>new Blob([bytes],{type:'image/heic'})};
 if(options.method==='DELETE'){
   if(staleDelete)return reply({detail:'Updated in another tab. Refresh before deleting.'},409);
   rows=[];return reply({deleted:true});
 }
 if(url.startsWith('/v1/capture/drafts/')&&options.method==='POST'){
   if(failSave)return reply({detail:'Draft service temporarily unavailable; retry.'},503);
   if(holdSave)await new Promise(resolve=>{releaseSave=resolve;});
   revision++;const result={...summary(),id:url.split('/').pop()};rows=[result];return reply(result);
 }
 if(url===`/v1/capture/drafts/${id}`)return reply({...summary(),revision:3,details,prior_count:2,images:[{filename:'original.heic',mime:'image/heic',rotation:90,crop:[.1,.1,.5,.5],panel:'left',url:`/v1/capture/drafts/${id}/images/0?revision=3`}]});
 if(url==='/v1/inspections'){
   submitted=options.body;
   if(failAnalyze)return reply({detail:'Queue full; retry.'},429);
 }
 return reply({id:'b'.repeat(32),state:'complete',stage:'saved',scan_id:'SCAN',detail:'Saved',created_at:'2026-09-07T00:00:00Z'});
};
const flush=async()=>{for(let n=0;n<12;n++)await new Promise(setImmediate);};
const find=(root,text)=>{if(root.textContent===text)return root;for(const child of root.children){const result=find(child,text);if(result)return result;}};
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
(async()=>{
 await flush();
 if(mode==='location-denied'){
   element('record-location').onclick();
   assert(element('location-status').textContent.includes('permission was denied'));
   assert.equal(element('geo').value,'');assert.equal(element('record-location').disabled,false);return;
 }
 if(mode==='resume-stale'){
   assert(element('draft-status').textContent.includes('Draft changed'));
   assert.equal(element('capture-list').children.length,0);assert.equal(element('capture-form').inert,false);
   assert(!calls.some(call=>call.options.method==='DELETE'));return;
 }
 if(mode==='resume'){
   assert(element('draft-status').textContent.includes('Resumed revision 3'));
   assert(element('image-count').textContent.includes('2 retained + 1 new'));
   assert.equal(element('region').value,'Saved district');assert.equal(element('parent-scan-id').value,'PARENT');
   assert.equal(element('geo').value,'[12.971599,77.594566]');assert(element('location-status').textContent.includes('12.97160'));
   assert.equal(element('context-origin').value,'imported');assert.equal(complete.checked,false);assert.equal(element('confirm-context').checked,false);
   assert.equal(element('pdp-width').value,85);assert.equal(element('pdp-height').value,125);
   const previews=calls.filter(call=>call.url==='/v1/capture/preview');
   assert.deepEqual(JSON.parse(previews[0].options.body.get('edit')),{rotation:0,crop:null});
   assert.deepEqual(JSON.parse(previews[1].options.body.get('edit')),{rotation:90,crop:[.1,.1,.5,.5]});
   assert.deepEqual(Buffer.from(await previews[0].options.body.get('file').arrayBuffer()),bytes);
   find(element('capture-list'),'Crop / zoom').onclick();assert(element('crop-dimensions').textContent.includes('2000 × 3200'));
   await element('capture-form').onsubmit({preventDefault(){},target:element('capture-form')});await flush();
   assert.equal(submitted.get('panels'),'left');assert.equal(submitted.get('parent_scan_id'),'PARENT');
   assert.equal(submitted.get('parent_revision'),'2');assert.equal(submitted.get('rescan_target'),'expiry_date');
   assert.equal(submitted.get('geo'),'[12.971599,77.594566]');
   assert.equal(element('rescan-context').hidden,false);assert(element('rescan-parent-reference').textContent.includes('revision 2'));
   assert.deepEqual(Buffer.from(await submitted.get('files').arrayBuffer()),bytes);
   assert(!calls.some(call=>call.options.method==='DELETE'));return;
 }
 element('record-location').onclick();assert(element('location-status').textContent.includes('approximately 14 metres'));
 assert.equal(element('geo').value,'[12.971599,77.594566]');
 await element('files').onchange({target:{files:[original],value:'selected'}});
 element('draft-title').value='Original draft';element('region').value='Before save';
 element('capture-form').events.input();
 find(element('capture-list'),'Rotate').onclick();await flush();
 failSave=true;await element('save-draft').onclick();assert(element('draft-status').textContent.includes('temporarily unavailable'));
 assert.equal(element('capture-list').children.length,1);
 let attempts=calls.filter(call=>call.options.method==='POST'&&call.url.includes('/drafts/'));
 const retryToken=attempts[0].options.body.get('save_token');
 failSave=false;holdSave=true;const pending=element('save-draft').onclick();await flush();
 element('region').value='Changed while saving';element('capture-form').events.input();
 releaseSave();await pending;
 assert(element('draft-status').textContent.includes('Newer changes'));
 attempts=calls.filter(call=>call.options.method==='POST'&&call.url.includes('/drafts/'));
 assert.equal(attempts[1].options.body.get('save_token'),retryToken);
 assert.equal(attempts[1].options.body.get('files'),original);
 assert.equal(JSON.parse(attempts[1].options.body.get('details')).region,'Before save');
 assert.deepEqual(JSON.parse(attempts[1].options.body.get('details')).geo,[12.971599,77.594566]);
 assert.equal(JSON.parse(attempts[1].options.body.get('details')).complete,undefined);
 assert.equal(JSON.parse(attempts[1].options.body.get('details')).parent_revision,null);
 assert.equal(JSON.parse(attempts[1].options.body.get('details')).rescan_target,'');
 assert.deepEqual(JSON.parse(attempts[1].options.body.get('edits')),[{rotation:90,crop:null}]);
 holdSave=false;await element('save-draft').onclick();
 assert(element('draft-status').textContent.includes('revision 2'));
 const latest=calls.filter(call=>call.options.method==='POST'&&call.url.includes('/drafts/')).at(-1);
 assert.equal(latest.options.body.get('revision'),'1');assert.equal(JSON.parse(latest.options.body.get('details')).region,'Changed while saving');
 const resume=find(element('draft-list'),'Resume in new tab');assert.equal(resume.target,'_blank');assert.equal(resume.rel,'noopener');
 failAnalyze=true;await element('capture-form').onsubmit({preventDefault(){},target:element('capture-form')});
 assert.equal(rows.length,1);assert(element('capture-error').textContent.includes('Queue full'));
 failAnalyze=false;await element('capture-form').onsubmit({preventDefault(){},target:element('capture-form')});await flush();
 assert.equal(submitted.get('geo'),'[12.971599,77.594566]');
 assert.equal(rows.length,1);assert(!calls.some(call=>call.options.method==='DELETE'));
 staleDelete=true;await find(element('draft-list'),'Delete saved draft').onclick();
 assert(element('draft-status').textContent.includes('another tab'));assert.equal(rows.length,1);
 staleDelete=false;await find(element('draft-list'),'Delete saved draft').onclick();
 assert.equal(rows.length,0);assert.equal(element('capture-list').children.length,1);
 assert(element('draft-status').textContent.includes('Saved draft deleted'));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""


@pytest.mark.parametrize("mode", ["save", "resume", "resume-stale", "location-denied"])
def test_real_capture_script_draft_save_resume_and_race_behavior(mode):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the capture draft browser script test")
    result = subprocess.run([node, "-e", HARNESS, str(JS), mode], capture_output=True,
                            text=True, timeout=25, check=False)
    assert result.returncode == 0, result.stderr
