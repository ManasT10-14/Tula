"""Exercise the shipped shared review handler, including uncertain-save recovery.

The deterministic DOM/network surface tests navigation and failure branches. The
live browser journey separately verifies real form serialization and server saves.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).parents[1] / "src/tula/web/static/console.js"

HARNESS = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const mode=process.argv[2],record=mode.startsWith('admin-')?'/admin/rules':'/inspections/QA123',calls=[],events={},timers=new Map();
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.attrs={};this.disabled=false;this.value='';this.name='';this._text='';}
 set textContent(value){this._text=value;this.children=[];} get textContent(){return this._text+this.children.map(c=>c.textContent).join(' ');}
 set innerHTML(value){throw Error('Untrusted HTML must never be rendered');}
 append(...nodes){this.children.push(...nodes);} replaceChildren(...nodes){this._text='';this.children=nodes;}
 setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k]??null;}removeAttribute(k){delete this.attrs[k];}
 matches(selector){return selector==='[data-review-form]'&&this.tag==='form';}
 querySelector(){return this.children.find(c=>'data-review-feedback' in c.attrs)||null;}
 querySelectorAll(){return this.controls||[];}
}
const make=(tag,name,value)=>Object.assign(new Element(tag),{name,value});
const form=new Element('form'),sibling=new Element('form');
const successMode=mode.replace('-no-fragment','');
const successModes=['success','context','intelligence','allergens','admin-import','admin-activate'];
form.attrs.action=record+(successMode==='admin-import'?'/import':successMode==='admin-activate'?'/activate':successMode==='context'?'/context':successMode==='intelligence'?'/intelligence/correct':successMode==='allergens'?'/allergens':'/review');
const section=successMode==='admin-import'?'#imported':successMode==='admin-activate'?'#active-version':successMode==='context'?'#package-context':successMode==='intelligence'?'#label-intelligence':successMode==='allergens'?'#allergen-analysis':'#review';
sibling.attrs.action=record+'/allergens';
const reason=make('textarea','reason','Retained officer text <milk>'),revision=make('input','revision','7');
const button=make('button','action','request_rescan'),otherButton=make('button','action','submit');
const disabled=make('button','','');disabled.disabled=true;
form.controls=[reason,revision,button,otherButton];sibling.controls=[make('input','concerns','milk'),disabled];
global.document={querySelector:()=>({content:'session-csrf'}),querySelectorAll:()=>[form,sibling],createElement:tag=>new Element(tag),addEventListener:(key,fn)=>events[key]=fn};
let reloads=0,assigned=[],replacements=[];
global.location={href:'https://tula.example'+record+'#review',origin:'https://tula.example',reload(){reloads++;},assign(url){assigned.push(url);}};
global.window={history:{replaceState(_,title,url){replacements.push(url);}}};
global.FormData=class {
 constructor(f){this.entries=new Map(f.controls.filter(c=>!c.disabled&&c.tag!=='button').map(c=>[c.name,c.value]));}
 set(k,v){this.entries.set(k,v);}get(k){return this.entries.get(k);}
};
global.setTimeout=(fn,ms)=>{assert.equal(ms,45000);timers.set(1,fn);return 1;};
global.clearTimeout=id=>timers.delete(id);
const response=(status=200,detail=null,url=null)=>({status,ok:status>=200&&status<300,redirected:!!url,url,json:async()=>{if(detail===null)throw SyntaxError('raw HTML parse error');return detail;}});
let release;
global.fetch=async(url,options)=>{
 calls.push({url,options});
 if(mode==='network')throw TypeError('private network diagnostics');
 if(mode==='timeout')return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Error('aborted'))));
 if(mode==='timeout-body')return {...response(422),json:()=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Error('aborted'))))};
 if(mode==='duplicate')return new Promise(resolve=>release=()=>resolve(response(409,{detail:'Changed in another tab'})));
 if(successModes.includes(successMode))return response(200,null,'https://tula.example'+record+(mode.endsWith('-no-fragment')?'':section));
 if(mode==='html500')return response(502);
 if(mode==='json500')return response(500,{detail:'private database path and secret'});
 if(mode==='login')return response(200,null,'https://tula.example/login?next='+record);
 if(mode==='unauthorized')return response(401,{detail:'Unauthorized'});
 if(mode==='conflict')return response(409,{detail:'Changed in another tab'});
 if(mode==='invalid')return response(422,{detail:[{loc:['body','frame_index'],msg:'Choose an image',input:'private input'},{loc:['body','reason'],msg:'<script>literal text</script>'}]});
 if(mode==='forbidden')return response(403,{detail:'Evidence integrity check failed'});
 if(mode==='limited')return response(429);
 if(mode==='unexpected-json')return response(200,{ok:true});
 if(mode==='external')return response(200,null,'https://external.example'+record);
 if(mode==='other-record')return response(200,null,'https://tula.example/inspections/OTHER#review');
 return response(200);
};
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
const event={target:form,submitter:button,preventDefault(){this.prevented=true;}};
const flush=async()=>{for(let n=0;n<3;n++)await new Promise(setImmediate);};
(async()=>{
 const headerEvent={detail:{headers:{}}};events['htmx:configRequest'](headerEvent);
 assert.equal(headerEvent.detail.headers['X-CSRF-Token'],'session-csrf');
 const unrelated={target:new Element('div'),preventDefault(){throw Error('Unrelated form intercepted');}};
 await events.submit(unrelated);assert.equal(calls.length,0);
 const task=events.submit(event);
 assert(event.prevented);assert.equal(calls.length,1);
 assert(form.controls.every(c=>c.disabled));assert(sibling.controls.every(c=>c.disabled));
 assert.equal(form.getAttribute('aria-busy'),'true');
 assert.equal(sibling.getAttribute('data-review-pending'),'true');
 assert.equal(calls[0].options.body.get('reason'),reason.value);
 assert.equal(calls[0].options.body.get('revision'),'7');
 assert.equal(calls[0].options.body.get('action'),'request_rescan');
 assert.equal(calls[0].options.headers['X-CSRF-Token'],'session-csrf');
 if(mode==='duplicate'){
   await events.submit({target:sibling,preventDefault(){}});assert.equal(calls.length,1);release();
 }
 if(mode.startsWith('timeout')){await flush();timers.get(1)();}
 await task;
 assert.equal(reason.value,'Retained officer text <milk>');assert.equal(revision.value,'7');
 assert(form.controls.every(c=>!c.disabled));assert.equal(sibling.controls[0].disabled,false);assert(disabled.disabled);
 assert.equal(form.getAttribute('aria-busy'),null);assert.equal(sibling.getAttribute('data-review-pending'),null);
 assert.equal(timers.size,0);assert.equal(assigned.length,0);
 if(successModes.includes(successMode)){
   assert.equal(reloads,1);assert.equal(replacements.length,1);
   assert.equal(replacements[0],record+section);return;
 }
 assert.equal(reloads,0);assert.equal(replacements.length,0);
 const box=form.querySelector(),text=box.textContent,link=box.children[1];
 assert.equal(box.getAttribute('role'),'alert');assert(text.includes('Your entries are still here'));
 assert.equal(link.target,'_blank');assert.equal(link.rel,'noopener');
 assert(!text.includes('private'));assert(!text.includes('SyntaxError'));assert(!text.includes('raw HTML'));
 if(['unauthorized','login'].includes(mode)){assert(text.includes('session has expired'));assert(text.includes('copy any unsaved entries'));assert(text.includes('save there'));assert.equal(link.href,'/login?next='+encodeURIComponent(record));}
 else assert.equal(link.href,record);
 if(mode.startsWith('timeout')){assert(text.includes('timed out'));assert(calls[0].options.signal.aborted);}
 if(mode==='conflict'||mode==='duplicate')assert(text.includes('Changed in another tab'));
 if(mode==='invalid'){assert(text.includes('frame index: Choose an image'));assert(text.includes('<script>literal text</script>'));}
 if(mode==='forbidden')assert(text.includes('Evidence integrity check failed'));
 if(mode==='limited')assert(text.includes('Wait before trying again'));
 // A completed failure releases the shared pending guard, without automatic retries.
 assert.equal(calls.length,1);
 if(!mode.startsWith('timeout')&&mode!=='duplicate'){await events.submit(event);assert.equal(calls.length,2);}
})().catch(error=>{console.error(error);process.exitCode=1;});
"""


@pytest.mark.parametrize("mode", [
    "success", "context", "intelligence", "network", "html500", "json500", "login",
    "unauthorized", "conflict", "invalid", "forbidden", "limited", "unexpected-html",
    "unexpected-json", "external", "other-record", "timeout", "timeout-body", "duplicate",
    "success-no-fragment", "context-no-fragment", "intelligence-no-fragment", "allergens-no-fragment",
    "admin-import-no-fragment", "admin-activate-no-fragment",
])
def test_review_save_navigation_recovery_and_concurrency(mode):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to execute the shipped review handler")
    result = subprocess.run([node, "-e", HARNESS, str(JS), mode], capture_output=True,
                            text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr
