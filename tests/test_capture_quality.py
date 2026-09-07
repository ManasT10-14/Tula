"""Actual pixel checks, temporary-file isolation and capture UI contracts."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import struct
import subprocess
import zlib
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFilter

from tula.domain.enums import Lane
from tula.domain.models import Analysis, PackageFacts, Scan
from tula.security import install_security
from tula.services.capture import receive
from tula.web import capture_quality

ROOT = Path(__file__).parents[1]
JS = ROOT / "src/tula/web/static/capture.js"


def pixels(kind="sharp", size=(900, 650), *, fmt="PNG", exif=None):
    image = Image.new("RGB", size, (110, 110, 110))
    draw = ImageDraw.Draw(image)
    for x in range(20, size[0], 25):
        draw.line((x, 0, x, size[1]), fill=(30, 30, 30), width=3)
    for y in range(20, size[1], 25):
        draw.line((0, y, size[0], y), fill=(30, 30, 30), width=3)
    if kind == "blur":
        image = image.filter(ImageFilter.GaussianBlur(12))
    elif kind == "glare":
        draw.ellipse((200, 200, 380, 380), fill="white")
    elif kind == "dark":
        image = Image.new("RGB", size, (8, 8, 8))
    encoded = io.BytesIO()
    image.save(encoded, format=fmt, **({"exif": exif} if exif else {}))
    return encoded.getvalue()


@pytest.fixture
def case(tmp_path):
    app = FastAPI()
    security = install_security(app, tmp_path / "security.db")
    admin = security.create_user("preview-admin", "Preview test passphrase 2026!", role="admin", bootstrap=True)
    user = security.create_user("preview-inspector", "Preview test passphrase 2026!", actor_id=admin.id)
    session = security.login(user.username, "Preview test passphrase 2026!")
    capture_quality.install_capture_quality(SimpleNamespace(app=app, ROOT=tmp_path))
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
        client.cookies.set("tula_session", session.token)
        client.headers["X-CSRF-Token"] = session.session.csrf_token
        yield SimpleNamespace(client=client, app=app, root=tmp_path,
                              temporary=tmp_path / "data/capture-previews", user=user)


def preview(case, data=None, edit=None, filename="package.png"):
    return case.client.post("/v1/capture/preview", files={"file": (filename, data if data is not None else pixels())},
                            data={"edit": json.dumps(edit or {})})


def test_quality_uses_actual_pixels_and_identifies_blur_glare_and_lighting(case):
    results = {}
    for kind in ("sharp", "blur", "glare", "dark"):
        response = preview(case, pixels(kind))
        assert response.status_code == 200, response.text
        results[kind] = response.json()["quality"]
        assert not list(case.temporary.iterdir())
    codes = {kind: {item["code"] for item in result["issues"]} for kind, result in results.items()}
    assert "blur_or_low_detail" in codes["blur"]
    assert "possible_glare" in codes["glare"]
    assert "underexposed" in codes["dark"]
    assert results["sharp"]["metrics"]["denoised_laplacian_variance"] > results["blur"]["metrics"]["denoised_laplacian_variance"]
    assert results["dark"]["metrics"]["median_brightness"] < results["sharp"]["metrics"]["median_brightness"]
    assert all("score" not in result and result["unassessed"] for result in results.values())


def test_preview_dimensions_crops_and_original_evidence_hash_stay_consistent(case):
    exif = Image.Exif()
    exif[274] = 6
    original = pixels(size=(120, 80), fmt="JPEG", exif=exif)
    edit = {"rotation": 90, "crop": [0, 0, .5, 1]}
    response = preview(case, original, edit, "../../original.jpg")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["original_size"] == [80, 120]
    assert result["working_size"] == result["preview_size"] == [60, 80]
    assert result["quality"]["metrics"]["selected_original_fraction"] == pytest.approx(.5)
    assert result["retained"] is False and "original" not in result and "path" not in result
    with Image.open(io.BytesIO(base64.b64decode(result["preview"].split(",", 1)[1]))) as image:
        assert image.format == "JPEG" and image.size == (60, 80)
    assert "low_resolution" in {issue["code"] for issue in result["quality"]["issues"]}
    assert not list(case.temporary.iterdir())
    _, hashes, records = receive([UploadFile(file=io.BytesIO(original), filename="original.jpg")],
                                case.root / "final-capture", edits=json.dumps([edit]))
    stored = Path(records[0]["original"])
    assert stored.read_bytes() == original
    assert hashes[str(stored)] == hashlib.sha256(original).hexdigest()


def test_preview_warns_when_crop_removes_most_package_context(case):
    response = preview(case, pixels(), {"crop": [.4, .4, .6, .6]})
    assert response.status_code == 200, response.text
    quality = response.json()["quality"]
    assert quality["metrics"]["selected_original_fraction"] == pytest.approx(.04)
    warning = next(item for item in quality["issues"] if item["code"] == "tight_crop_context")
    assert "package context" in warning["message"] and "full-panel" in warning["action"]
    assert not list(case.temporary.iterdir())


def test_heic_decodes_to_bounded_browser_preview_without_replacing_original(case):
    pytest.importorskip("pillow_heif")
    original = pixels(size=(2200, 1300), fmt="HEIF")
    response = preview(case, original, filename="photo.heic")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["original_size"] == result["working_size"] == [2200, 1300]
    assert max(result["preview_size"]) == 1600
    assert result["preview"].startswith("data:image/jpeg;base64,")
    assert not list(case.temporary.iterdir())
    _, _, records = receive([UploadFile(file=io.BytesIO(original), filename="photo.heic")], case.root / "evidence")
    assert Path(records[0]["original"]).read_bytes() == original


@pytest.mark.parametrize("edit", [
    [], {"rotation": True}, {"rotation": 45}, {"rotation": "90"}, {"unexpected": "value"},
    {"crop": [0, 0, 2, 1]}, {"crop": [0, 0, .001, 1]}, {"crop": [0, 0, False, 1]},
    {"crop": [0, 0, float("inf"), 1]}, {"crop": "0,0,1,1"},
    {"crop": [0, 0, 10 ** 800, 1]},
])
def test_malformed_preview_edits_are_rejected_and_cleaned(case, edit):
    response = case.client.post("/v1/capture/preview", files={"file": ("photo.png", pixels())},
                                data={"edit": json.dumps(edit)})
    assert response.status_code == 422, response.text
    assert not list(case.temporary.iterdir())


def test_invalid_format_and_byte_pixel_limits_leave_no_temporary_evidence(case):
    for data, status in ((b"not a photo", 415), (pixels(size=(15, 20)), 413),
                         (b"x" * (25 * 1024 * 1024 + 1), 413), (pixels(fmt="GIF"), 415)):
        assert preview(case, data).status_code == status
        assert not list(case.temporary.iterdir())
    # A real PNG header lets the decoder reject oversized dimensions before
    # allocating/decompressing the image pixels.
    ihdr = struct.pack("!IIBBBBB", 6000, 6000, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    oversized = b"\x89PNG\r\n\x1a\n" + struct.pack("!I", len(ihdr)) + chunk + struct.pack("!I", zlib.crc32(chunk))
    oversized += b"\0\0\0\0IEND\xaeB`\x82"
    assert preview(case, oversized).status_code == 413
    assert not list(case.temporary.iterdir())


def test_quality_preview_requires_authenticated_session_and_csrf(case):
    csrf = case.client.headers.pop("X-CSRF-Token")
    assert preview(case).status_code == 403
    case.client.headers["X-CSRF-Token"] = csrf
    case.client.cookies.clear()
    assert preview(case).status_code == 401
    assert not list(case.temporary.iterdir())


def test_concurrency_limit_does_not_create_a_request_directory(case):
    assert capture_quality._SLOTS.acquire(blocking=False)
    assert capture_quality._SLOTS.acquire(blocking=False)
    try:
        response = preview(case)
        assert response.status_code == 429 and response.headers["retry-after"] == "2"
        assert not list(case.temporary.iterdir())
    finally:
        capture_quality._SLOTS.release()
        capture_quality._SLOTS.release()


def test_failure_cleanup_is_scoped_to_its_request_and_releases_slot(case, monkeypatch):
    marker = case.temporary / "another-request.txt"
    marker.write_text("Preserve another request", encoding="utf-8")

    def fail_quality(*args, **kwargs):
        raise RuntimeError("Deliberate quality failure")

    monkeypatch.setattr(capture_quality, "assess_quality", fail_quality)
    assert preview(case).status_code == 500
    assert list(case.temporary.iterdir()) == [marker]
    assert marker.read_text(encoding="utf-8") == "Preserve another request"
    assert capture_quality._SLOTS.acquire(blocking=False)
    assert capture_quality._SLOTS.acquire(blocking=False)
    capture_quality._SLOTS.release()
    capture_quality._SLOTS.release()


class Markup(HTMLParser):
    """Small standard-library reader for the actual rendered form attributes."""
    def __init__(self, html):
        super().__init__()
        self.html, self.tags, self.scripts, self.script_id = html, [], {}, None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.tags.append((tag, attributes))
        if tag == "script":
            self.script_id = attributes.get("id")

    def handle_endtag(self, tag):
        if tag == "script":
            self.script_id = None

    def handle_data(self, data):
        if self.script_id:
            self.scripts[self.script_id] = self.scripts.get(self.script_id, "") + data

    def find(self, **attrs):
        return next(attributes for _, attributes in self.tags if all(attributes.get(key) == value for key, value in attrs.items()))


def page(prior=None, requested=""):
    templates = Jinja2Templates(directory=str(ROOT / "src/tula/web/templates"))
    templates.env.globals["asset_url"] = lambda name: "/static/" + name
    request = SimpleNamespace(url=SimpleNamespace(path="/"), query_params={"rescan": requested},
        state=SimpleNamespace(user=SimpleNamespace(role="inspector", display_name="Officer"), csrf_token="test-csrf"))
    return Markup(templates.env.get_template("index.html").render(request=request,
        rescan_record=prior, rule_count=18, rules_version="test"))


def test_rescan_template_uses_authorized_record_and_does_not_reaffirm_attestations():
    prior = Analysis(scan=Scan(scan_id="PRIOR", lane=Lane.CITIZEN, region="Recorded district", frames=["a", "b"]),
                     package=PackageFacts(legal_context={"category": "food", "category_confirmed": True}),
                     intelligence={"allergens": {"concerns": ["milk", "soy"]}})
    rendered = page(prior, "PRIOR")
    data = json.loads(rendered.scripts["capture-initial-data"])
    assert data["prior_count"] == 2 and data["lane"] == "citizen"
    assert data["region"] == "Recorded district" and data["concerns"] == ["milk", "soy"]
    assert rendered.find(name="parent_scan_id")["value"] == "PRIOR"
    assert "checked" not in rendered.find(id="confirm-context")
    assert "checked" not in rendered.find(name="complete")
    denied = page(None, "PRIVATE-RECORD")
    assert denied.find(name="parent_scan_id")["value"] == ""
    assert "PRIVATE-RECORD" not in denied.html
    assert json.loads(denied.scripts["capture-initial-data"]) == {"prior_count": 0, "requested_job": ""}


def test_keyboard_crop_controls_have_labels_limits_and_javascript_parses():
    rendered = page()
    for name in ("left", "top", "right", "bottom"):
        control = rendered.find(id="crop-" + name)
        assert control["type"] == "number" and control["step"] == "1"
        assert any(tag == "label" and attrs.get("for") == "crop-" + name for tag, attrs in rendered.tags)
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to validate browser JavaScript syntax")
    result = subprocess.run([node, "--check", str(JS)], capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr


_BROWSER_HARNESS = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const priorCount = Number(process.argv[2]);
class Element {
  constructor(tag='div'){this.tag=tag;this.children=[];this.options=[];this.value='';this.checked=false;this.style={};this.disabled=false;this.textContent='';this.width=1600;this.height=1000;this.classList={add(){},remove(){}};}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(name,value){this[name]=value;}
  add(value){this.options.push(value);}
  addEventListener(){} focus(){} showModal(){this.open=true;} close(){this.open=false;}
  getContext(){return {translate(){},rotate(){},drawImage(){},strokeRect(){},fillRect(){}};}
  getBoundingClientRect(){return {left:0,top:0,width:this.width,height:this.height};}
  setPointerCapture(){}
}
const elements={}, element=id=>elements[id]||(elements[id]=new Element());
const data={prior_count:priorCount,lane:'citizen',region:'Prior district',concerns:['milk'],context:{category:'food',category_confirmed:true,bundle_type:'single',bundle_confirmed:true,shape:'rectangular',shape_confirmed:true,is_imported:true,imported_confirmed:true}};
element('capture-initial-data').textContent=JSON.stringify(data);
for(const [id,values] of Object.entries({lane:['field','citizen'], 'context-category':['unknown','food'], 'context-bundle':['unknown','single'], 'context-origin':['unknown','imported','domestic'], 'context-shape':['unknown','rectangular']})){element(id).options=values.map(value=>({value}));element(id).value=values[0];}
const complete=element('complete');complete.checked=true;element('confirm-context').checked=true;
global.document={getElementById:element,createElement:tag=>new Element(tag),querySelector:()=>complete};
global.window={tulaCSRF:()=> 'csrf'};global.location={assign(){}};
global.navigator={};global.URL={createObjectURL:()=> 'blob:original',revokeObjectURL(){}};
global.Option=function(text,value){this.value=value;this.text=text;};
global.Image=class {set src(value){this.url=value;}async decode(){if(this.url.startsWith('blob:'))throw Error('HEIC unavailable in browser');this.width=1600;this.height=1000;}};
global.FormData=class {
  constructor(form){this.values=new Map();if(form){for(const id of ['lane','region','concerns'])this.set(id,element(id).value);if(complete.checked)this.set('complete','1');}}
  append(key,value){this.values.set(key,[...(this.values.get(key)||[]),value]);}
  set(key,value){this.values.set(key,[value]);}delete(key){this.values.delete(key);}get(key){return(this.values.get(key)||[])[0];}
};
global.localStorage={getItem:()=>null,setItem(){},removeItem(){}};
const original={name:'original.heic',size:100,type:'image/heic'}, calls=[];let submitted;
const quality={width:3200,height:2000,issues:[],metrics:{median_brightness:120,contrast_p99_p01:180,denoised_laplacian_variance:80}};
global.fetch=async(url,options)=>{
  calls.push({url,options});
  if(url==='/v1/capture/preview')return {ok:true,status:200,json:async()=>({quality,original_size:[3200,2000],preview:'data:image/jpeg;base64,test'})};
  if(url==='/v1/inspections')submitted=options.body;
  return {ok:true,status:200,json:async()=>({id:'saved-job',state:'complete',stage:'saved',scan_id:'SCAN',detail:'Saved',created_at:'2026-09-07T00:00:00Z'})};
};
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
const flush=async()=>{for(let n=0;n<8;n++)await new Promise(setImmediate);};
const find=(root,text)=>{if(root.textContent===text)return root;for(const child of root.children){const result=find(child,text);if(result)return result;}};
(async()=>{
  assert.equal(element('lane').value,'citizen');assert.equal(element('region').value,'Prior district');assert.equal(element('concerns').value,'milk');
  assert.equal(element('context-category').value,'food');assert.equal(element('context-origin').value,'imported');
  assert.equal(complete.checked,false);assert.equal(element('confirm-context').checked,false);
  await element('files').onchange({target:{files:priorCount===11?[original,{...original,name:'second.heic'}]:[original],value:'selected'}});
  assert.equal(calls[0].options.body.get('file'),original);
  if(priorCount===11){assert.equal(calls.length,1);assert(element('image-count').textContent.startsWith('12 / 12'));assert(element('capture-error').textContent.includes('combined'));return;}
  assert(element('image-count').textContent.includes('2 retained + 1 new'));
  find(element('capture-list'),'Rotate').onclick();await flush();find(element('capture-list'),'Crop / zoom').onclick();
  assert(element('crop-dimensions').textContent.includes('2000 × 3200'));
  for(const[id,value]of Object.entries({'crop-left':200,'crop-top':320,'crop-right':1000,'crop-bottom':1600}))element(id).value=String(value);
  element('crop-left').oninput();assert.equal(element('apply-crop').disabled,false);element('apply-crop').onclick();await flush();
  await element('capture-form').onsubmit({preventDefault(){},target:element('capture-form')});await flush();
  assert.equal(submitted.get('files'),original);
  assert.deepEqual(JSON.parse(submitted.get('edits')),[{rotation:90,crop:[.1,.1,.5,.5]}]);
  const context=JSON.parse(submitted.get('legal_context'));
  for(const key of ['category_confirmed','bundle_confirmed','shape_confirmed','imported_confirmed'])assert.equal(context[key],false);
  assert.equal(submitted.get('lane'),'citizen');assert.equal(submitted.get('complete'),undefined);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""


@pytest.mark.parametrize("prior_count", [2, 11])
def test_browser_script_preserves_original_heic_crop_coordinates_and_rescan_limits(prior_count):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for capture JavaScript behavior tests")
    result = subprocess.run([node, "-e", _BROWSER_HARNESS, str(JS), str(prior_count)], capture_output=True,
                            text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
