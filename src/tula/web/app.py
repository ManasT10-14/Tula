"""Officer console.

FastAPI serving Jinja templates with HTMX for the interactive bits: one
process, one language, no Node build step, and it works offline once loaded.
The API routes underneath are the same ones the PRD's section 12 lists, so a
React console can be dropped on later without touching anything here.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.staticfiles import StaticFiles

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

from .. import bench as bench_mod
from ..analyse import AnalyseOptions, analyse
from ..config import runtime_root
from ..domain.enums import Lane, Verdict
from ..domain.models import validated_geo
from ..labgen import LabelSpec
from ..observability import (
    RequestObservabilityMiddleware,
    install_operational_logging,
    suppress_default_access_log,
)
from ..report import render
from ..report.evidence import verify
from ..rules.engine import RulesEngine
from ..security import install_security
from ..services.reports import ReportError, export_inspection
from ..storage.backup import runtime_lease
from ..storage.db import Repository, detect_shrinkflation
from .admin_rules import install_admin_rules, load_active_rules

BASE = Path(__file__).resolve().parent
ROOT = runtime_root()
UPLOADS = ROOT / "data" / "uploads"
BENCH = ROOT / "data" / "bench"
OUT = ROOT / "out"

app = FastAPI(title="TATVA", description="Legal Metrology compliance engine", docs_url=None, redoc_url=None)
suppress_default_access_log()
install_operational_logging()
app.mount("/static", StaticFiles(directory=str(BASE / "static"), check_dir=False), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))
with runtime_lease(ROOT):
    repo = Repository(ROOT / "data" / "tula.db")
    rules = RulesEngine.from_directory()
    repo.archive_rules(rules.pack)
    rules = load_active_rules(repo, rules)
    security = install_security(app, repo.path)
    # Installed last so correlation also covers authentication/transport rejections.
    app.add_middleware(RequestObservabilityMiddleware)

templates.env.globals.update(
    asset_url=lambda name: "/static/" + name + "?v=" + __import__("hashlib").sha256(
        (BASE / "static" / name).read_bytes()).hexdigest()[:12],
    verdict_label=render.VERDICT_LABEL,
    verdict_class=lambda v: {
        Verdict.PASS: "ok",
        Verdict.VIOLATION: "bad",
        Verdict.ADVISORY: "warn",
        Verdict.INCONCLUSIVE: "warn",
        Verdict.UNVERIFIED: "warn",
        Verdict.EXEMPT: "info",
        Verdict.NOT_APPLICABLE: "muted",
    }.get(v, "muted"),
    finding_rows=render.finding_rows,
    finding_message=render.finding_message,
    default_spec=LabelSpec(),
)


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    from ..services.rescan import TARGET_LABELS
    rescan_record = repo.get(request.query_params["rescan"]) if request.query_params.get("rescan") else None
    if rescan_record and request.state.user.role not in {"admin", "supervisor"} and (
            rescan_record.scan.inspector_id != request.state.user.id):
        rescan_record = None
    target = request.query_params.get("target", "") if rescan_record else ""
    if target and target not in TARGET_LABELS:
        raise HTTPException(422, "Choose a supported declaration for the close-up.")
    rescan_evidence_unavailable = False
    if rescan_record:
        try:
            rescan_evidence_unavailable = any(item["status"] != "verified" for item in verify(rescan_record))
        except OSError:
            rescan_evidence_unavailable = True
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "recent": repo.search(limit=8),
            "stats": repo.stats(),
            "rules_version": rules.pack.version,
            "rule_count": len(rules.pack.rules),
            "rescan_record": rescan_record,
            "rescan_targets": TARGET_LABELS,
            "rescan_target": target,
            "rescan_evidence_unavailable": rescan_evidence_unavailable,
        },
    )


@app.post("/inspect", response_class=HTMLResponse)
def inspect(request: Request, files: list[UploadFile], panels: str = Form("pdp"),
            officer: str = Form(""), lane: str = Form("field"), complete: str = Form(""),
            geo: str = Form("")):
    """Synchronous compatibility endpoint; the guided console uses the durable job API."""
    from ..services.capture import receive
    try:
        chosen_lane = Lane(lane)
    except ValueError:
        raise HTTPException(422, "Choose a valid inspection lane.") from None
    try:
        coordinates = validated_geo(json.loads(geo) if geo else None)
    except (ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(422, "Record a valid latitude and longitude, or clear the location.") from None
    captures, originals, edits = receive(files, UPLOADS, panels)
    analysis = analyse(captures, AnalyseOptions(lane=chosen_lane, engine_name="rapidocr",
        operator=request.state.user.display_name,
        geo=coordinates,
        capture_is_complete=complete.lower() in ("1", "true", "on", "yes")), rules=rules)
    analysis.scan.inspector_id = request.state.user.id
    analysis.scan.original_frame_hashes = originals
    analysis.scan.capture_edits = edits
    repo.save(analysis)
    request.app.state.security.audit(actor_id=request.state.user.id, action="inspection.created",
                                    entity_type="inspection", entity_id=analysis.scan.scan_id)
    return templates.TemplateResponse(request, "_findings.html",
        {"a": analysis, "headline": render.headline(analysis)})


@app.get("/inspections/{scan_id}", response_class=HTMLResponse)
def inspection(request: Request, scan_id: str):
    analysis = repo.get(scan_id)
    if analysis is None:
        raise HTTPException(404, "No such inspection.")
    history = repo.history(analysis.package.gtin, source=analysis.scan.source) if analysis.package.gtin else []
    from ..services.rescan_history import linked_captures
    return templates.TemplateResponse(
        request,
        "inspection.html",
        {
            "a": analysis,
            "headline": render.headline(analysis),
            "sections": render.build(analysis),
            "history": history,
            "shrinkflation": detect_shrinkflation(history),
            "integrity": verify(analysis),
            "revisions": repo.revisions(scan_id),
            "linked_captures": linked_captures(repo, analysis),
            "declaration_labels": render.DECLARATION_LABEL,
            "csrf_token": request.state.csrf_token,
        },
    )


@app.get("/repository", response_class=HTMLResponse)
def repository(request: Request, q: str = "", verdict: str = "", page: int = 1):
    from urllib.parse import urlencode
    allowed = ("category", "review_status", "product_status", "source", "region", "operator", "manufacturer", "date_from", "date_to", "rule_id", "finding_outcome", "geo_lat", "geo_lon")
    filters = {key: request.query_params.get(key, "") for key in allowed}
    # Casework never shows generated bench runs unless they are asked for. An
    # officer searching inspection history should not have to filter test data
    # out of their own records; "all" remains available for support queries.
    filters["source"] = "" if filters["source"] == "all" else (filters["source"] or "inspection")
    try:
        result = repo.filter_search(q, page=page, overall=verdict, **filters)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    base_query = dict(request.query_params)
    base_query.pop("page", None)
    return templates.TemplateResponse(request, "repository.html",
        {**result, "q": q, "verdict": verdict, "filters": filters,
         "page_url": lambda number: "/repository?" + urlencode({**base_query, "page": number})})


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, source: str = "inspection", date_from: str = "", date_to: str = ""):
    if source not in ("inspection", "bench"):
        raise HTTPException(422, "Choose operational inspections or test-bench runs.")
    try:
        stats = repo.analytics(source=source, date_from=date_from, date_to=date_to)
        recent = repo.filter_search(source=source, date_from=date_from, date_to=date_to, page_size=8)["rows"]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return templates.TemplateResponse(request, "dashboard.html",
        {"stats": stats, "recent": recent, "source": source, "date_from": date_from, "date_to": date_to})


# ---------------------------------------------------------------------------
# artefacts and API
# ---------------------------------------------------------------------------


@app.get("/inspections/{scan_id}/report.{fmt}")
def download(request: Request, scan_id: str, fmt: str):
    analysis = repo.get(scan_id)
    if analysis is None:
        raise HTTPException(404, "No such inspection.")
    if fmt == "json":
        return JSONResponse(analysis.model_dump(mode="json"))
    if fmt not in {"pdf", "docx"}:
        raise HTTPException(404, "Unknown format. Use pdf, docx or json.")
    try:
        path = export_inspection(repo, request.app.state.security, request.state.user, analysis, OUT, fmt)
    except ReportError as exc:
        raise HTTPException(409, str(exc)) from None
    media = "application/pdf" if fmt == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return FileResponse(path, media_type=media, filename=path.name)


@app.get("/inspections/{scan_id}/notice.docx")
def notice(request: Request, scan_id: str, premises: str = ""):
    analysis = repo.get(scan_id)
    if analysis is None:
        raise HTTPException(404, "No such inspection.")
    try:
        path = export_inspection(repo, request.app.state.security, request.state.user,
                                 analysis, OUT, "notice.docx", premises=premises)
    except ReportError as exc:
        raise HTTPException(409, str(exc)) from None
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=path.name,
    )


@app.get("/v1/rules/{version}")
def rules_pack(version: str):
    """The rule pack is public.

    A respondent served with a notice can fetch the exact version that produced
    it and reproduce the determination. Systems that adjudicate against people
    should be auditable by them.
    """
    if version in (rules.pack.version, "current"):
        return JSONResponse(rules.pack.model_dump(mode="json"))
    archived = repo.archived_rules(version)
    if not archived:
        raise HTTPException(404, f"Unknown rules version {version!r}.")
    return JSONResponse(archived)


@app.get("/v1/products/{gtin}/history")
def product_history(gtin: str, source: str = "inspection"):
    try:
        history = repo.history(gtin, source=source)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(
        {"gtin": gtin, "source": source, "scans": history, "shrinkflation": detect_shrinkflation(history)}
    )


# ---------------------------------------------------------------------------
# bench -- the test instrument
#
# Kept behind /lab in the navigation. Demonstration instruments share the
# engine with casework but never the dataset, and an officer should never meet
# a generated label while working a real inspection.
# ---------------------------------------------------------------------------


@app.get("/lab", response_class=HTMLResponse)
def lab(request: Request):
    """Entry point for every test instrument, away from the casework pages."""
    return templates.TemplateResponse(request, "lab.html", {})


@app.get("/bench", response_class=HTMLResponse)
def bench_page(request: Request):
    """The lab: synthesise a label with known geometry and watch it be judged."""
    return templates.TemplateResponse(
        request,
        "bench.html",
        {
            "scenarios": bench_mod.SCENARIOS,
            "spec": LabelSpec(),
            "presets": {
                s.key: {**bench_mod.spec_to_form(s.spec), "lane": s.lane.value} for s in bench_mod.SCENARIOS
            },
        },
    )


@app.post("/bench/run", response_class=HTMLResponse)
async def bench_run(request: Request):
    form = dict(await request.form())
    try:
        spec = bench_mod.spec_from_form(form)
        lane = Lane(form.get("lane") or "field")
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc))
    engine = form.get("engine") or "fixture"
    if engine not in ("fixture", "rapidocr"):
        raise HTTPException(422, "Choose fixture or rapidocr.")
    stem = f"interactive-{uuid.uuid4().hex}"
    result = await run_in_threadpool(bench_mod.run_spec,
        spec,
        engine_name=engine,
        rules=rules,
        work_dir=BENCH,
        stem=stem,
        lane=lane,
    )
    result.analysis.scan.source = "bench"
    result.analysis.scan.inspector_id = request.state.user.id
    result.analysis.scan.operator = request.state.user.display_name
    repo.save(result.analysis)
    return templates.TemplateResponse(
        request,
        "_bench_result.html",
        {"r": result, "a": result.analysis, "stem": result.image.stem,
         "headline": render.headline(result.analysis)},
    )


@app.get("/bench/scenarios", response_class=HTMLResponse)
def bench_scenarios(request: Request, engine: str = "fixture"):
    """Acceptance matrix. Each row fetches its own result so no single
    request has to carry fourteen pipeline runs."""
    if engine not in ("fixture", "rapidocr"):
        raise HTTPException(422, "Choose fixture or rapidocr.")
    return templates.TemplateResponse(
        request,
        "scenarios.html",
        {"scenarios": bench_mod.SCENARIOS, "engine": engine},
    )


@app.get("/bench/scenarios/{key}", response_class=HTMLResponse)
def bench_scenario_row(request: Request, key: str, engine: str = "fixture"):
    if engine not in ("fixture", "rapidocr"):
        raise HTTPException(422, "Choose fixture or rapidocr.")
    scn = bench_mod.scenario(key)
    if scn is None:
        raise HTTPException(404, f"No scenario named {key!r}.")
    result = bench_mod.run_scenario(
        scn, engine_name=engine, rules=rules, work_dir=BENCH
    )
    result.analysis.scan.source = "bench"
    result.analysis.scan.inspector_id = request.state.user.id
    repo.save(result.analysis)
    return templates.TemplateResponse(
        request,
        "_scenario_row.html",
        {"s": scn, "r": result, "engine": engine},
    )


@app.get("/bench/image/{stem}.png")
def bench_image(stem: str):
    path = BENCH / f"{Path(stem).name}.png"
    if not path.exists():
        raise HTTPException(404, "No such rendered label.")
    return FileResponse(path, media_type="image/png")


@app.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request):
    """Browse the statute as the engine sees it."""
    ordered = sorted(rules.pack.rules, key=lambda r: r.citation.clause)
    return templates.TemplateResponse(
        request,
        "rules.html",
        {"rules": ordered, "pack": rules.pack},
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok", "rules_version": rules.pack.version,
            "rules": len(rules.pack.rules)}


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """The installed console's service worker, served from the root.

    A worker's default scope is the directory it is served from, so the copy
    under /static/ could only control /static/. Serving the same file here gives
    it the whole origin without a Service-Worker-Allowed header. It caches the
    shell and refuses to store casework; see the file for why.

    The middleware's "no-store, private" applies here as it does to any path
    outside /static/, which is what a worker script wants anyway: the browser
    re-fetches it to detect updates rather than serving a stale copy.
    """
    return FileResponse(BASE / "static" / "sw.js", media_type="text/javascript")


@app.get("/inspections/{scan_id}/frames/{index}")
def evidence_frame(scan_id: str, index: int):
    analysis = repo.get(scan_id)
    if analysis is None or not 0 <= index < len(analysis.scan.frames):
        raise HTTPException(404, "No such evidence frame.")
    record = verify(analysis)[index]
    if record["status"] != "verified":
        raise HTTPException(409, "Evidence is missing or its hash does not match.")
    return FileResponse(analysis.scan.frames[index])


@app.get("/inspections/{scan_id}/integrity")
def evidence_integrity(scan_id: str):
    analysis = repo.get(scan_id)
    if analysis is None:
        raise HTTPException(404, "No such inspection.")
    return verify(analysis)


# Install workflow endpoints after the compatibility console is defined.
import sys

from .capture_drafts import install_capture_drafts
from .capture_quality import install_capture_quality
from .intelligence_review import install_intelligence_review
from .lifecycle import install_job_lifecycle
from .workflow import install_workflow

install_workflow(sys.modules[__name__])
install_admin_rules(sys.modules[__name__])
install_intelligence_review(sys.modules[__name__])
install_capture_quality(sys.modules[__name__])
install_capture_drafts(sys.modules[__name__])
install_job_lifecycle(sys.modules[__name__])
