"""Bugs found by the September 2026 repository and sample-image audit."""
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from tula.analyse import AnalyseOptions, Capture, analyse
from tula.bench import run_spec
from tula.domain.enums import AssuranceTier, Panel, Verdict
from tula.domain.enums import DeclarationClass as DC
from tula.domain.models import Measured, ScaleEstimate, sha256_file
from tula.extract import normalizers as norm
from tula.extract import pipeline
from tula.labgen import LabelSpec, render
from tula.ocr.base import OcrLine, OcrResult
from tula.ocr.engines import RapidOcrEngine, get_engine
from tula.rules import exemptions
from tula.storage.db import Repository, detect_shrinkflation


@pytest.mark.parametrize("text,value", [("MRP Rs 1234.56", 1234.56), ("MRP 99999.99", 99999.99)])
def test_prices_do_not_truncate_after_three_digits(text, value):
    assert norm.parse_price(text)["value"] == value


@pytest.mark.parametrize("text", ["Protein 6 g", "Serving size 10 g", "Rs 22.50 per 100 g", "Net Wt -5 g", "Net Wt 0 g"])
def test_nutrition_unit_price_and_invalid_quantities_are_not_net_contents(text):
    assert norm.parse_net_quantity(text) is None


@pytest.mark.parametrize("text,iso", [("Mfg 2026-03-01", "2026-03"), ("Mfg 15/03/2026", "2026-03"), ("Mfg 03/26", "2026-03")])
def test_full_dates_are_not_parsed_as_month_day(text, iso):
    assert norm.parse_date(text)["iso"] == iso


@pytest.mark.parametrize("text", ["Best before 03/2026", "Mfg: see base. Expiry 03/2026", "Mfg 31/02/2026"])
def test_expiry_and_invalid_dates_are_not_packing_dates(text):
    assert norm.parse_date(text) is None


def test_multiple_unit_prices_are_not_dual_mrp():
    assert norm.parse_price("MRP Rs 45\nRs 22.50 per 100 g\nRs 225 per kg")["count"] == 1


@pytest.mark.parametrize("text", ["Net Qty 1 N", "Net Qty 5 cm", "Net Qty 0 g"])
def test_small_pack_exemption_requires_positive_mass_or_volume(text):
    parsed = norm.parse_net_quantity(text)
    decls = {} if parsed is None else {DC.NET_QUANTITY: pipeline.Declaration(klass=DC.NET_QUANTITY, raw=text, norm=parsed)}
    assert not exemptions.determine(decls).exemptions


@pytest.mark.parametrize("text", ["Restaurant style noodles", "Bulk pack biscuits", "Freshly prepared tea"])
def test_marketing_does_not_exempt_retail_package(text):
    result = exemptions.determine({}, raw_text=text)
    assert result.in_scope and not result.exemptions


def test_manufacturer_does_not_include_contact_on_another_frame():
    first = OcrLine("Manufactured by: Foo", (0, 0, 100, 20), frame="front.png")
    second = OcrLine("Plot 42, Pune 411018", (0, 0, 100, 20), frame="back.png")
    result = pipeline.extract([(Panel.PDP, OcrResult(lines=[first])), (Panel.BACK, OcrResult(lines=[second]))])
    assert "411018" not in result.declarations[DC.MANUFACTURER].raw
    assert result.declarations[DC.MANUFACTURER].frame == "front.png"


def test_low_confidence_text_is_retained_for_review_but_not_adjudicated():
    result = pipeline.extract([(Panel.PDP, OcrResult(lines=[OcrLine("Net Wt 5 g", (0, 0, 100, 20), confidence=0.1)]))])
    assert result.spans and not result.declarations


def test_market_only_identity_is_not_manufacturer():
    result = pipeline.extract([(Panel.PDP, OcrResult(lines=[OcrLine("Marketed by Foo", (0, 0, 100, 20))]))])
    assert DC.MANUFACTURER not in result.declarations


def test_scale_remains_bound_to_its_frame(tmp_path):
    a = render(LabelSpec(px_per_mm=8, net_qty_mm=3), tmp_path, "a")
    b = render(LabelSpec(px_per_mm=16, net_qty_mm=3), tmp_path, "b")
    result = analyse([Capture(str(a.png)), Capture(str(b.png))], AnalyseOptions(engine_name="fixture"))
    m = result.measurements["net_quantity_cap_height"]
    assert m.lower <= 3 <= m.upper
    assert {s.frame for s in result.scan.scales} == {str(a.png), str(b.png)}
    assert all(s.frame for s in result.spans)
    assert all(d.frame for d in result.declarations.values())


def test_blank_second_frame_prevents_absence_claims(tmp_path):
    a = render(LabelSpec(mrp=None, covers_all_declarations=False), tmp_path, "front")
    b = render(LabelSpec(blank_panel=True, covers_all_declarations=False), tmp_path, "back")
    result = analyse([Capture(str(a.png)), Capture(str(b.png), Panel.BACK)], AnalyseOptions(engine_name="fixture", capture_is_complete=True))
    f = next(f for f in result.findings if f.rule_id.endswith("MRP_PRESENT"))
    assert f.verdict is Verdict.INCONCLUSIVE
    assert result.scan.coverage.unreadable_frames == [str(b.png)]


def test_front_back_does_not_imply_complete_carton(tmp_path):
    a = render(LabelSpec(covers_all_declarations=False), tmp_path, "front")
    b = render(LabelSpec(covers_all_declarations=False), tmp_path, "back")
    b.meta.write_text('{"panel":"back"}')
    result = analyse([Capture(str(a.png)), Capture(str(b.png), Panel.BACK)], AnalyseOptions(engine_name="fixture"))
    assert not result.scan.coverage.is_complete


def test_repeated_bench_runs_preserve_evidence(tmp_path):
    a = run_spec(LabelSpec(), work_dir=tmp_path)
    digest = sha256_file(str(a.image))
    b = run_spec(LabelSpec(mrp=123), work_dir=tmp_path)
    assert a.image != b.image and sha256_file(str(a.image)) == digest


def test_corrupt_metadata_degrades_without_crashing(tmp_path):
    a = render(LabelSpec(), tmp_path)
    a.meta.write_text('{"mm_per_px":-0.5}')
    result = analyse([Capture(str(a.png))], AnalyseOptions(engine_name="fixture"))
    assert any("invalid capture metadata" in w for w in result.warnings)
    assert not result.measurements
    assert any(f.rule_id.endswith("NETQTY_HEIGHT") and f.verdict is Verdict.INCONCLUSIVE for f in result.findings)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_scale_values_are_validated(value):
    with pytest.raises(ValidationError):
        ScaleEstimate(source="test", mm_per_px=value, sigma=0.1, tier=AssuranceTier.C)


def test_negative_uncertainty_is_invalid():
    with pytest.raises(ValidationError):
        Measured(quantity="height", value=1, uncertainty=-1)


def test_rapidocr_object_output_is_not_silently_dropped():
    engine = RapidOcrEngine()
    engine._reader = lambda _: SimpleNamespace(boxes=[[[0, 0], [100, 0], [100, 20], [0, 20]]], txts=["Net Wt 200 g"], scores=[0.99])
    assert engine.read("unused").lines[0].text == "Net Wt 200 g"


def test_unknown_engine_has_actionable_error():
    with pytest.raises(ValueError, match="Unknown OCR engine"):
        get_engine("typo")


def test_shrinkflation_never_compares_different_dimensions():
    assert not detect_shrinkflation([{"net_quantity":200,"net_unit":"g","mrp":45,"captured_at":"2026-01-01"}, {"net_quantity":100,"net_unit":"ml","mrp":45,"captured_at":"2026-02-01"}])


@pytest.fixture
def client(tmp_path, monkeypatch):
    from tula.web import app as web
    monkeypatch.setattr(web, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(web, "BENCH", tmp_path / "bench")
    monkeypatch.setattr(web, "OUT", tmp_path / "out")
    monkeypatch.setattr(web, "repo", Repository(tmp_path / "test.db"))
    # Explicitly replace only expensive OCR in this HTTP fixture. Production
    # upload routing itself must always select rapidocr, regardless of env.
    real_analyse = web.analyse

    def fixture_analysis(captures, options, **kwargs):
        return real_analyse(captures, options, engine=get_engine("fixture"), **kwargs)

    monkeypatch.setattr(web, "analyse", fixture_analysis)
    from tula.security import SecurityStore
    store = SecurityStore(tmp_path / "test.db")
    store.create_user("testadmin", "Test-password-2026-unique", role="admin", bootstrap=True)
    monkeypatch.setattr(web.app.state, "security", store)
    login = store.login("testadmin", "Test-password-2026-unique")
    client = TestClient(web.app, base_url="https://testserver")
    client.cookies.set("tula_session", login.token)
    client.headers["X-CSRF-Token"] = login.session.csrf_token
    return client


def png_bytes():
    stream = BytesIO()
    Image.new("RGB", (100, 100), "white").save(stream, format="PNG")
    return stream.getvalue()


@pytest.mark.parametrize("path", ["/", "/healthz", "/lab", "/bench", "/bench/scenarios", "/rules", "/repository", "/dashboard", "/v1/rules/current", "/static/htmx.min.js"])
def test_pages_and_local_assets(client, path):
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("data,status", [({"lane":"invalid"},422), ({"panels":"front"},422)])
def test_invalid_inspection_form_is_not_server_error(client, data, status):
    assert client.post("/inspect", data=data, files={"files":("label.png",png_bytes(),"image/png")}).status_code == status


def test_fake_image_is_rejected(client):
    r = client.post("/inspect", files={"files":("fake.jpg",b"not an image","image/jpeg")})
    assert r.status_code == 415


def test_production_inspect_ignores_fixture_environment(client, monkeypatch):
    from tula.web import app as web
    monkeypatch.setenv("TULA_OCR", "fixture")
    captured = []
    scoped_analysis = web.analyse

    def record_engine(captures, options, **kwargs):
        captured.append(options.engine_name)
        return scoped_analysis(captures, options, **kwargs)

    monkeypatch.setattr(web, "analyse", record_engine)
    response = client.post("/inspect", files={"files": ("sample.png", png_bytes(), "image/png")})
    assert response.status_code == 200
    assert captured == ["rapidocr"]


def test_duplicate_and_path_like_filenames_do_not_overwrite(client):
    from tula.web import app as web
    r = client.post("/inspect", files=[("files",("../same.png",png_bytes(),"image/png"))]*2)
    assert r.status_code == 200
    a = web.repo.get(web.repo.search()[0].scan_id)
    assert len(set(a.scan.frames)) == 2
    assert all(Path(p).is_relative_to(web.UPLOADS) for p in a.scan.frames)


@pytest.mark.parametrize("data", [{"engine":"invalid"}, {"panel_w_mm":"nan"}, {"panel_w_mm":"-1"}, {"panel_w_mm":"100000000"}, {"net_qty_mm":"bad"}])
def test_bad_bench_form_is_rejected(client, data):
    assert client.post("/bench/run", data=data).status_code == 422


def test_bench_result_image_url_exists(client):
    import re
    r = client.post("/bench/run", data={})
    assert r.status_code == 200
    image_url = re.search(r'src="([^"]+\.png[^\"]*)"', r.text)[1]
    assert client.get(image_url).status_code == 200


def test_barcode_decodes_without_human_readable_digits(tmp_path):
    import numpy as np
    import zxingcpp

    from tula.forensics.barcodes import decode
    barcode = zxingcpp.create_barcode("8901234567890", zxingcpp.EAN13)
    path = tmp_path / "barcode.png"
    Image.fromarray(np.asarray(zxingcpp.write_barcode_to_image(barcode, scale=4, add_hrt=False))).save(path)
    assert decode(str(path)) == ["8901234567890"]


def test_changed_evidence_blocks_reports(client):
    from tula.web import app as web
    client.post("/inspect", files={"files": ("sample.png", png_bytes(), "image/png")})
    scan_id = web.repo.search()[0].scan_id
    assert all(r['status'] == 'verified' for r in client.get(f'/inspections/{scan_id}/integrity').json())
    analysis = web.repo.get(scan_id)
    Path(analysis.scan.frames[0]).write_bytes(b'changed')
    for endpoint in ('report.pdf', 'report.docx', 'notice.docx', 'frames/0'):
        assert client.get(f'/inspections/{scan_id}/{endpoint}').status_code == 409
    assert client.get(f'/inspections/{scan_id}/report.json').status_code == 200


def test_pdf_contains_machine_readable_record_and_crops(tmp_path):
    from pypdf import PdfReader

    from tula.report.pdf import write
    result = run_spec(LabelSpec(), work_dir=tmp_path)
    path = write(result.analysis, tmp_path / 'report.pdf')
    reader = PdfReader(path)
    attached = json.loads(reader.attachments['analysis.json'][0])
    assert attached['scan']['scan_id'] == result.analysis.scan.scan_id
    assert any(page.images for page in reader.pages)


def test_split_packing_date_stays_on_same_frame():
    from tula.extract.pipeline import extract
    lines = [OcrLine('Mfg:', (0,0,100,20), frame='a'),
             OcrLine('03/2026', (0,25,100,45), frame='a')]
    result = extract([(Panel.PDP, OcrResult(lines=lines))])
    assert result.declarations[DC.DATE_OF_PACKING].norm['iso'] == '2026-03'
    lines[1].frame = 'b'
    result = extract([(Panel.PDP, OcrResult(lines=lines))])
    declaration = result.declarations[DC.DATE_OF_PACKING]
    # Unresolved cues now retain observations; no value may be borrowed from b.
    assert all(declaration.norm.get(key) is None for key in ('iso', 'year', 'month', 'day'))
    assert declaration.raw == 'Mfg:'
    assert {source.frame for source in declaration.provenance.sources} == {'a'}


def test_unit_price_different_dimensions_are_inconclusive():
    from tula.rules.evaluator import EvalContext, evaluate
    ctx = EvalContext(facts={'a': 'g', 'b': 'ml'})
    assert evaluate(ctx, {'same_units_arithmetic': ['$a', '$b', True]}) is None


@pytest.mark.parametrize('declared,price,expected', [(22.5,45,True), (22,45,False), (50,99.99,True)])
def test_unit_price_uses_decimal_rounding_not_five_percent_slack(declared, price, expected):
    from tula.rules.evaluator import EvalContext, evaluate
    ctx = EvalContext(facts={'d':declared,'p':price,'q':200,'b':100})
    assert evaluate(ctx, {'unit_price_matches':['$d','$p','$q','$b']}) is expected


@pytest.mark.parametrize('payload', ['[]', '{"mm_per_px":0.1,"mm_per_px_source":"artwork"}'])
def test_invalid_or_exact_artwork_metadata_cannot_upgrade_raster(tmp_path, payload):
    r = render(LabelSpec(), tmp_path)
    r.meta.write_text(payload)
    a = analyse([Capture(str(r.png))], AnalyseOptions(engine_name='fixture'))
    assert a.scan.tier is AssuranceTier.C
    assert any('invalid capture metadata' in w for w in a.warnings)


def test_unrelated_aruco_id_is_not_a_scale_card(tmp_path):
    import cv2
    import numpy as np

    from tula.imaging.metrology import from_aruco
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for marker_id in (0, 17):
        pixels = np.full((300, 300), 255, dtype=np.uint8)
        pixels[50:250,50:250] = cv2.aruco.generateImageMarker(d, marker_id, 200)
        p = tmp_path / f'marker-{marker_id}.png'
        Image.fromarray(pixels).save(p)
        result = from_aruco(str(p), 25)
        if marker_id == 0:
            assert result is not None and abs(result.mm_per_px - 0.125) < 0.002
        else:
            assert result is None
