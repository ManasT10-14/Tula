"""Authenticated capture-draft API; image bytes never have public file URLs."""
from __future__ import annotations

import json
import threading

from fastapi import Form, HTTPException, Query, Request, Response, UploadFile

from ..security.web import require_roles
from ..services.capture import CaptureError
from ..services.capture_drafts import CaptureDrafts

_SLOTS = threading.BoundedSemaphore(2)


def install_capture_drafts(web):
    service = CaptureDrafts(web.repo, web.ROOT, web.app.state.security)
    web.app.state.capture_drafts = service
    inspector = require_roles("inspector", "supervisor", "admin")

    def run(action):
        try:
            return action()
        except CaptureError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    @web.app.get("/v1/capture/drafts")
    def list_drafts(request: Request):
        inspector(request)
        return {"drafts": run(lambda: service.list(request.state.user))}

    @web.app.get("/v1/capture/drafts/{draft_id}")
    def get_draft(draft_id: str, request: Request):
        inspector(request)
        return run(lambda: service.get(draft_id, request.state.user))

    @web.app.post("/v1/capture/drafts/{draft_id}")
    def save_draft(draft_id: str, request: Request, files: list[UploadFile], revision: int = Form(0),
                   save_token: str = Form(...), details: str = Form("{}"), panels: str = Form("pdp"),
                   edits: str = Form("[]")):
        inspector(request)
        try:
            if len(details) > 12000:
                raise ValueError()
            parsed = json.loads(details)
        except ValueError:
            raise HTTPException(422, "Provide valid capture draft details.") from None
        if not _SLOTS.acquire(blocking=False):
            raise HTTPException(429, "Draft saves are busy. Retry shortly.", headers={"Retry-After": "2"})
        try:
            return run(lambda: service.save(draft_id, revision, save_token, request.state.user, files,
                                            details=parsed, panels=panels, edits=edits))
        finally:
            _SLOTS.release()

    @web.app.delete("/v1/capture/drafts/{draft_id}")
    def delete_draft(draft_id: str, request: Request, revision: int = Query(..., ge=1)):
        inspector(request)
        run(lambda: service.delete(draft_id, revision, request.state.user))
        return {"deleted": True}

    @web.app.get("/v1/capture/drafts/{draft_id}/images/{index}")
    def draft_image(draft_id: str, index: int, request: Request, revision: int = Query(..., ge=1)):
        inspector(request)
        data, item = run(lambda: service.image(draft_id, index, revision, request.state.user))
        return Response(data, media_type=item["mime"], headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
