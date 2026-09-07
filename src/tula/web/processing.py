"""Account-scoped access to retained processing work across browser sessions."""
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

STATE_LABELS = {"unfinished": "Unfinished and failed", "queued": "Queued", "running": "Processing",
                "failed": "Failed - action needed", "complete": "Saved inspections", "all": "All jobs"}


def install_processing(web, jobs):
    def listing(request, state, page):
        try:
            return jobs().list_for(request.state.user, state=state, page=page)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @web.app.get("/v1/jobs")
    def processing_jobs(request: Request, state: str = "unfinished", page: int = 1):
        return listing(request, state, page)

    @web.app.get("/processing", response_class=HTMLResponse)
    def processing_page(request: Request, state: str = "unfinished", page: int = 1):
        result = listing(request, state, page)
        return web.templates.TemplateResponse(request, "processing.html", {
            **result, "state_labels": STATE_LABELS,
            "as_of": datetime.now(UTC).strftime("%d %b %Y, %H:%M:%S UTC"),
            "page_url": lambda number: "/processing?" + urlencode({"state": state, "page": number}),
            "all_accounts": request.state.user.role in {"admin", "supervisor"},
        })
