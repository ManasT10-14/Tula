"""HTTP boundary for supplementary observation corrections."""
from __future__ import annotations

import json

from fastapi import Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..services.intelligence_review import FIELD_LABELS, correct_observation
from ..storage.workflow import RevisionConflict


def install_intelligence_review(web):
    web.templates.env.globals["intelligence_field_labels"] = FIELD_LABELS

    @web.app.post("/inspections/{scan_id}/intelligence/correct")
    def correct(request: Request, scan_id: str, revision: int = Form(...), field: str = Form(...),
                raw: str = Form(...), frame_index: int = Form(...), reason: str = Form(...),
                observation_index: int = Form(-1), date_interpretation: str = Form(""),
                bbox: str = Form("[]"), left: str = Form(""), top: str = Form(""),
                right: str = Form(""), bottom: str = Form("")):
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(401, "Sign in to correct an inspection.")
        try:
            rectangle = json.loads(bbox)
            if rectangle == [] and all(value.strip() for value in (left, top, right, bottom)):
                rectangle = [int(value) for value in (left, top, right, bottom)]
            updated = correct_observation(web.repo, scan_id, revision, user, field, raw,
                                           frame_index, rectangle, reason,
                                           observation_index=observation_index,
                                           date_interpretation=date_interpretation)
        except RevisionConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except KeyError:
            raise HTTPException(404, "No such inspection.") from None
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except json.JSONDecodeError:
            raise HTTPException(422, "Select a source rectangle or enter its four pixel bounds.") from None
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from None
        except OSError:
            raise HTTPException(422, "The recorded source image could not be read. Restore the evidence before correcting.") from None
        request.app.state.security.audit(actor_id=user.id, action="intelligence.corrected",
            entity_type="inspection", entity_id=scan_id, before={"revision": revision},
            after={"revision": updated.review.revision, "field": field,
                   "observation_index": observation_index, "reason": reason})
        return RedirectResponse(f"/inspections/{scan_id}#label-intelligence", status_code=303)
