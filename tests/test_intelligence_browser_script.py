"""Execute the supplementary evidence editor's real inline JavaScript."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).parents[1] / "src/tula/web/templates/_intelligence.html"

HARNESS = r"""
const vm=require('vm'),fs=require('fs'),assert=require('assert'),mode=process.argv[2];
const elements={},images=[],draws=[];
class Element {
 constructor(){this.value='';this.attrs={};this.children=[];this.events={};this.dataset={};this.width=0;this.height=0;this.hidden=false;this.validationMessage='';}
 setCustomValidity(message){this.validationMessage=message;}
 getAttribute(key){return this.attrs[key]??null;}
 addEventListener(key,action){this.events[key]=action;}
 getContext(){return{drawImage(image){draws.push(image);},strokeRect(){}};}
 getBoundingClientRect(){return{left:0,top:0,width:500,height:250};}
 setPointerCapture(){}replaceChildren(...nodes){this.children=nodes;}append(node){this.children.push(node);}
 scrollIntoView(){}focus(){}reportValidity(){this.reported=true;}
 reset(){element('field').value='manufacturing_date';element('frame').value='0';}
}
const element=name=>elements[name]||(elements[name]=new Element());
const form=element('correction-form');form.action='https://tula.example/inspections/QA/intelligence/correct';
element('editor-data').textContent=JSON.stringify({frames:['frame0','frame1'],fields:{batch_number:[{raw:'BATCH AB1234',sources:[{frame:'frame1',bbox:[10,20,200,80]}]}]}});
element('field').value='manufacturing_date';element('frame').value='0';
const editButton=new Element();editButton.dataset={field:'batch_number',index:'0'};
global.document={getElementById:id=>element(id.replace(/^intelligence-/,'')),createElement:()=>new Element(),querySelectorAll:()=>[editButton]};
global.Image=class{constructor(){this.naturalWidth=0;this.naturalHeight=0;images.push(this);}set src(value){this.url=value;}};
const source=fs.readFileSync(process.argv[1],'utf8');vm.runInThisContext(source);
const loaded=image=>{image.naturalWidth=1000;image.naturalHeight=500;image.onload();};
const coordinates=[0,1,2,3].map(i=>element('bbox-'+i));
const values=array=>{array.forEach((v,i)=>coordinates[i].value=String(v));coordinates[0].oninput();};
const submit=()=>{const event={preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;}};form.events.submit(event);return event;};
assert(element('image-size').textContent.includes('Loading'));assert.equal(element('canvas').width,0);
assert(coordinates.every(c=>c.validationMessage));assert(submit().stopped);
loaded(images[0]);
if(mode==='lifecycle'){
 values([10,20,200,80]);assert.equal(element('bbox').value,'[10,20,200,80]');
 element('frame').value='1';element('frame').onchange();
 assert.equal(element('canvas').width,0);assert.equal(element('bbox').value,'[]');assert(coordinates.every(c=>c.value===''));
 const current=images.at(-1);images[0].onerror();assert(element('image-size').textContent.includes('Loading'));
 current.onerror();assert.equal(element('retry-image').hidden,false);assert(submit().stopped);
 element('retry-image').onclick();assert.equal(element('retry-image').hidden,true);
 current.onload();assert.equal(element('canvas').width,0);
 loaded(images.at(-1));values([0,0,1000,500]);assert(!submit().prevented);
 assert.equal(draws.at(-1),images.at(-1));assert.equal(coordinates[2].max,1000);
}else if(mode==='pending'){
 values([10,20,200,80]);const oldCount=images.length;
 form.attrs['data-review-pending']='true';
 editButton.onclick();element('add-new').onclick();element('retry-image').onclick();
 element('canvas').onpointerdown({clientX:10,clientY:10,button:0,pointerId:1});
 element('canvas').onpointermove({clientX:100,clientY:100});
 assert.equal(images.length,oldCount);assert.equal(element('bbox').value,'[10,20,200,80]');
 delete form.attrs['data-review-pending'];editButton.onclick();loaded(images.at(-1));
 assert.equal(element('raw').value,'BATCH AB1234');assert.equal(element('frame').value,'1');
 assert.equal(element('bbox').value,'[10,20,200,80]');
 assert.equal(element('date-interpretation').disabled,true);
}else if(mode==='pointer'){
 const canvas=element('canvas');
 canvas.onpointerdown({clientX:10,clientY:10,button:0,pointerId:1});
 canvas.onpointermove({clientX:100,clientY:100});canvas.onpointercancel();
 const selected=element('bbox').value;canvas.onpointermove({clientX:200,clientY:200});
 assert.equal(element('bbox').value,selected);assert.equal(selected,'[20,20,200,200]');
 canvas.onpointerdown({clientX:10,clientY:10,button:2,pointerId:1});canvas.onpointermove({clientX:400,clientY:200});
 assert.equal(element('bbox').value,selected);
}else{
 const cases={valid:[0,0,1000,500],blank:['',0,100,100],fraction:[.5,0,100,100],negative:[-1,0,100,100],outside:[0,0,1001,500],inverted:[200,0,100,100],empty:[0,0,0,100],infinite:[0,0,Infinity,100]};
 values(cases[mode]);const event=submit();
 if(mode==='valid'){assert(!event.prevented);assert.equal(element('bbox').value,'[0,0,1000,500]');assert(coordinates.every(c=>!c.validationMessage));}
 else{assert(event.stopped);assert.equal(element('bbox').value,'[]');assert(coordinates.every(c=>c.validationMessage));}
}
"""


@pytest.mark.parametrize("mode", [
    "lifecycle", "pending", "pointer", "valid", "blank", "fraction", "negative", "outside",
    "inverted", "empty", "infinite",
])
def test_supplementary_source_and_rectangle_validation(tmp_path, mode):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to execute the supplementary editor")
    script = re.search(r"<script>\s*(.*?)\s*</script>",
                       TEMPLATE.read_text(encoding="utf-8"), re.DOTALL)
    assert script is not None
    path = tmp_path / "intelligence-editor.js"
    path.write_text(script.group(1), encoding="utf-8")
    result = subprocess.run([node, "-e", HARNESS, str(path), mode], capture_output=True,
                            text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr
