"""Officer workflow HTTP boundary; all state changes delegate to application services."""
from __future__ import annotations

import json
import logging
import sqlite3

from fastapi import Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse

from ..domain.enums import Lane
from ..domain.models import validated_geo
from ..observability import log_event
from ..services import review
from ..services.capture import CaptureError, receive
from ..services.rescan import RescanError, current_parent, validate_target
from ..storage.workflow import RevisionConflict
from .lifecycle import get_jobs


def install_workflow(web):
    app = web.app

    def jobs():
        return get_jobs(web)

    def owned_job(request, job_id):
        job = jobs().get(job_id)
        if not job or (job["owner_id"] != request.state.user.id and request.state.user.role not in ("admin", "supervisor")):
            raise HTTPException(404, "No such processing job.")
        return job

    from .processing import install_processing
    install_processing(web, jobs)

    @app.exception_handler(CaptureError)
    async def capture_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, exc):
        log_event(
            logging.getLogger("tula.web"),
            logging.ERROR,
            "database_operation_failed",
            error_type=type(exc).__name__,
        )
        return JSONResponse({"detail": "The inspection database is temporarily unavailable. Your last save could not be confirmed. Reload the record before retrying."}, status_code=503)

    @app.get("/docs")
    def api_documentation(request: Request):
        return web.templates.TemplateResponse(request, "api_docs.html", {"schema": app.openapi()})

    @app.get("/redoc")
    def alternate_docs():
        return RedirectResponse("/docs", status_code=303)

    @app.exception_handler(RevisionConflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.post("/v1/inspections", status_code=202)
    def enqueue(request: Request, files: list[UploadFile], panels: str = Form("pdp"),
                lane: str = Form("field"), complete: str = Form(""), edits: str = Form("[]"),
                dimensions: str = Form("{}"), region: str = Form(""), geo: str = Form(""),
                concerns: str = Form(""), parent_scan_id: str = Form(""), legal_context: str = Form("{}"),
                parent_revision: int | None = Form(None), rescan_target: str = Form("")):
        validate_target(rescan_target, parent_scan_id)
        if parent_revision is not None and not parent_scan_id:
            raise RescanError("An original revision must link to an inspection.", 422)
        try:
            selected_lane = Lane(lane)
            from ..services.context import validate_context
            context = validate_context(json.loads(legal_context), actor_id=request.state.user.id)
        except (ValueError, TypeError):
            raise HTTPException(422, "Choose valid capture source and package context values.") from None
        try:
            coordinates = validated_geo(json.loads(geo) if geo else None)
        except (ValueError, TypeError, json.JSONDecodeError):
            raise HTTPException(422, "Record a valid latitude and longitude, or clear the location.") from None
        parent = None
        if parent_scan_id:
            with web.repo._connect() as conn:
                parent = current_parent(conn, parent_scan_id, request.state.user, parent_revision)
            if len(parent.scan.frames)+len(files) > 12:
                raise HTTPException(422, "This inspection already has too many images; start a new inspection.")
        captures, hashes, records = receive(files, web.UPLOADS, panels, edits, dimensions)
        try:
            job = jobs().enqueue(captures, hashes, records, actor=request.state.user, lane=selected_lane.value,
                                 complete=complete in ("1", "true", "on"), region=region,
                                 geo=coordinates,
                                 concerns=[c.strip() for c in concerns.split(",") if c.strip()][:30], parent=parent_scan_id or None,
                                 legal_context=context, parent_revision=parent.review.revision if parent else None,
                                 rescan_target=rescan_target)
        except RescanError:
            raise
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from None
        app.state.security.audit(actor_id=request.state.user.id, action="images.uploaded", entity_type="job",
                                 entity_id=job["id"], after={"frames": len(captures)+(len(parent.scan.frames) if parent else 0),
                                 "lane": lane, "location_recorded": coordinates is not None,
                                 "parent_scan_id": parent_scan_id or None,
                                 "parent_revision": parent.review.revision if parent else None, "rescan_target": rescan_target})
        return job

    @app.get("/v1/jobs/{job_id}")
    def job_status(request: Request, job_id: str):
        return owned_job(request, job_id)

    @app.post("/v1/jobs/{job_id}/retry")
    def job_retry(request: Request, job_id: str):
        owned_job(request, job_id)
        try:
            return jobs().retry(job_id, actor=request.state.user)
        except RescanError:
            raise
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/inspections/{scan_id}/review")
    def review_action(request: Request, scan_id: str, revision: int = Form(...),
                      action: str = Form(...), reason: str = Form(...), finding_id: str = Form(""),
                      verdict: str = Form(""), kind: str = Form(""), raw: str = Form(""),
                      frame_index: int = Form(0), bbox: str = Form("[]")):
        user = request.state.user
        try:
            if action == "decide":
                updated = review.decide(web.repo, scan_id, revision, user, finding_id, verdict, reason)
            elif action == "correct":
                a = web.repo.get(scan_id)
                if a is None:
                    raise KeyError(scan_id)
                from ..rules.engine import RulesEngine
                from ..rules.spec import RulePack
                archived = web.repo.archived_rules(a.rules_version)
                original_rules = RulesEngine(RulePack.model_validate(archived)) if archived else web.rules
                updated = review.correct(web.repo, original_rules, scan_id, revision, user, kind, raw,
                                         frame_index, json.loads(bbox), reason)
            else:
                updated = review.transition(web.repo, scan_id, revision, user, action, reason)
        except RevisionConflict:
            raise
        except KeyError:
            raise HTTPException(404, "No such inspection.") from None
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from None
        app.state.security.audit(actor_id=user.id, action="review."+action, entity_type="inspection",
                                 entity_id=scan_id, before={"revision": revision},
                                 after={"revision": updated.review.revision, "reason": reason})
        return RedirectResponse(f"/inspections/{scan_id}#review", status_code=303)

    @app.get("/inspections/{scan_id}/revisions/{revision}")
    def historic_revision(scan_id: str, revision: int):
        a = web.repo.revision(scan_id, revision)
        if a is None:
            raise HTTPException(404, "No such inspection revision.")
        return JSONResponse(a.model_dump(mode="json"))

    @app.post("/inspections/{scan_id}/context")
    def package_context(request: Request, scan_id: str, revision: int = Form(...),
                        reason: str = Form(...), category: str = Form("unknown"),
                        bundle_type: str = Form("unknown"), origin: str = Form("unknown"),
                        shape: str = Form("unknown"), confirmed: str = Form("")):
        from ..rules.engine import RulesEngine
        from ..rules.spec import RulePack
        from ..services.context import form_context
        try:
            a = web.repo.get(scan_id)
            if a is None:
                raise KeyError(scan_id)
            archived = web.repo.archived_rules(a.rules_version)
            original_rules = RulesEngine(RulePack.model_validate(archived)) if archived else web.rules
            context = form_context(category, bundle_type, origin, shape, confirmed == "1")
            updated = review.confirm_context(web.repo, original_rules, scan_id, revision,
                                             request.state.user, context, reason)
        except RevisionConflict:
            raise
        except KeyError:
            raise HTTPException(404, "No such inspection.") from None
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from None
        app.state.security.audit(actor_id=request.state.user.id, action="package.context_confirmed",
                                 entity_type="inspection", entity_id=scan_id,
                                 before={"revision": revision}, after={"revision": updated.review.revision})
        return RedirectResponse(f"/inspections/{scan_id}#package-context", status_code=303)

    @app.post("/inspections/{scan_id}/allergens")
    def allergen_concerns(request: Request, scan_id: str, concerns: str = Form(""), revision: int = Form(...)):
        from ..extract.intelligence import analyze_allergens
        try:
            def change(a):
                review._editable(a, request.state.user)
                a.intelligence["allergens"] = analyze_allergens(
                    a.intelligence.get("fields", {}).get("ingredients", []),
                    [s.strip() for s in concerns.split(",") if s.strip()][:30])
            updated = web.repo.revise(scan_id, revision, request.state.user.id, "allergens.concerns_changed",
                                      "Updated informational allergen concerns", change)
        except RevisionConflict:
            raise
        except KeyError:
            raise HTTPException(404, "No such inspection.") from None
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        app.state.security.audit(actor_id=request.state.user.id, action="allergens.concerns_changed",
                                 entity_type="inspection", entity_id=scan_id, after={"revision": updated.review.revision})
        return RedirectResponse(f"/inspections/{scan_id}#allergen-analysis", status_code=303)

    @app.get("/v1/analytics")
    def analytics(source: str = "inspection", date_from: str = "", date_to: str = ""):
        try:
            return web.repo.analytics(source=source, date_from=date_from, date_to=date_to)
        except ValueError:
            raise HTTPException(422, "Use ISO dates: YYYY-MM-DD.") from None
