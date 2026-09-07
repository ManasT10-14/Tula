"""Authenticated source-route/template checks without OCR or operational data."""
from __future__ import annotations

import hashlib
import importlib
from html.parser import HTMLParser
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from tula.domain.enums import DeclarationClass as DC
from tula.domain.enums import Verdict
from tula.domain.models import Analysis, Citation, Finding, PackageFacts, Scan, sha256_file
from tula.security import install_security
from tula.storage.db import Repository
from tula.web.workflow import install_workflow


class Elements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def by_id(self, value):
        return next(attrs for _, attrs in self.tags if attrs.get("id") == value)


@pytest.fixture
def case(tmp_path, monkeypatch):
    # Import-time initialization, if needed, also stays in this disposable root.
    monkeypatch.setenv("TULA_DATA_DIR", str(tmp_path / "bootstrap"))
    web = importlib.import_module("tula.web.app")
    repo = Repository(tmp_path / "runtime" / "data" / "tula.db")
    repo.archive_rules(web.rules.pack)
    monkeypatch.setattr(web, "repo", repo)
    app = FastAPI()
    security = install_security(app, repo.path)
    password = "Isolated rescan navigation passphrase 2026"
    admin = security.create_user("rescan-nav-admin", password, role="admin", bootstrap=True)
    owner = security.create_user("rescan-nav-owner", password, role="inspector", actor_id=admin.id)
    outsider = security.create_user("rescan-nav-other", password, role="inspector", actor_id=admin.id)
    image = tmp_path / "retained.png"
    Image.new("RGB", (120, 100), "white").save(image)
    parent = Analysis(scan=Scan(scan_id="NAV-PARENT", inspector_id=owner.id,
        frames=[str(image)], frame_hashes={str(image): sha256_file(str(image))}, operator="Original officer"),
        package=PackageFacts(), rules_version=web.rules.pack.version,
        findings=[Finding(finding_id="F-NAV-PARENT-001", rule_id="NAV.RULE", rules_version=web.rules.pack.version,
            citation=Citation(), declaration=DC.RETAIL_SALE_PRICE, verdict=Verdict.PASS)],
        intelligence={"fields": {"expiry_date": [], "batch_number": []}})
    parent.review.status = "approved"
    parent.review.approved_by = admin.id
    parent.review.corrections = [{"kind": "retail_sale_price", "after": {"raw": "<script>alert('old price')</script>"},
                                 "actor": "<b>Old reviewer</b>", "reason": "Verified original package marking"}]
    repo.save(parent)
    with repo._connect() as conn:
        retained = conn.execute("SELECT record FROM inspection_revision WHERE scan_id=? AND revision=0",
                                (parent.scan.scan_id,)).fetchone()[0]
    child = Analysis(scan=Scan(scan_id="NAV-CHILD", inspector_id=owner.id, parent_scan_id=parent.scan.scan_id,
        parent_revision=0, parent_record_sha256=hashlib.sha256(retained.encode()).hexdigest(), rescan_target="expiry_date",
        frames=[str(image)], frame_hashes={str(image): sha256_file(str(image))}), package=PackageFacts(),
        rules_version=web.rules.pack.version, intelligence={"fields": {"expiry_date": []}})
    repo.save(child)
    app.add_api_route("/", web.home, methods=["GET"])
    app.add_api_route("/inspections/{scan_id}", web.inspection, methods=["GET"])
    isolated_web = SimpleNamespace(app=app, repo=repo, rules=web.rules, templates=web.templates,
                                  UPLOADS=tmp_path / "received")
    install_workflow(isolated_web)
    with TestClient(app, base_url="https://testserver") as client:
        def login(user):
            session = security.login(user.username, password)
            client.cookies.clear()
            client.cookies.set("tula_session", session.token)
            client.headers["X-CSRF-Token"] = session.session.csrf_token
        login(owner)
        yield SimpleNamespace(client=client, repo=repo, parent=parent, child=child, web=isolated_web,
                              image=image, login=login, owner=owner, outsider=outsider)


@pytest.mark.parametrize("target", ["retail_sale_price", "date_of_packing", "packing_date", "expiry_date"])
def test_real_home_prefills_target_revision_and_keeps_attestations_unchecked(case, target):
    response = case.client.get("/", params={"rescan": "NAV-PARENT", "target": target})
    assert response.status_code == 200
    page = Elements(response.text)
    assert page.by_id("parent-scan-id")["value"] == "NAV-PARENT"
    assert page.by_id("parent-revision")["value"] == "0"
    assert "disabled" not in page.by_id("parent-revision")
    assert any(tag == "option" and attrs.get("value") == target and "selected" in attrs for tag, attrs in page.tags)
    assert "checked" not in page.by_id("confirm-context")
    assert all("checked" not in attrs for _, attrs in page.tags if attrs.get("name") == "complete")
    assert "original inspection's rule version and assessment basis" in response.text


def test_invalid_target_cannot_prefill_an_authorized_capture(case):
    response = case.client.get("/", params={"rescan": "NAV-PARENT", "target": "invented-target"})
    assert response.status_code == 422


def test_unassigned_inspector_can_read_lineage_but_cannot_create_targeted_capture(case):
    case.login(case.outsider)
    response = case.client.get("/", params={"rescan": "NAV-PARENT", "target": "expiry_date"})
    assert response.status_code == 200
    page = Elements(response.text)
    assert page.by_id("parent-scan-id")["value"] == ""
    assert "disabled" in page.by_id("parent-revision") and "disabled" in page.by_id("rescan-target")
    page = case.client.get("/inspections/NAV-CHILD")
    assert page.status_code == 200 and "Linked capture history" in page.text
    assert 'href="/inspections/NAV-PARENT"' in page.text
    assert 'href="/?rescan=NAV-CHILD' not in page.text
    assert 'action="/inspections/NAV-CHILD/review"' not in page.text


def test_displayed_parent_revision_is_rejected_after_another_review_edit(case):
    response = case.client.get("/", params={"rescan": "NAV-PARENT", "target": "expiry_date"})
    revision = Elements(response.text).by_id("parent-revision")["value"]
    case.repo.revise("NAV-PARENT", 0, case.owner.id, "review.comment", "Later retained comment",
                     lambda a: a.review.comments.append({"text": "Later retained comment"}))
    data = BytesIO()
    Image.new("RGB", (100, 100), "white").save(data, "PNG")
    response = case.client.post("/v1/inspections", data={"parent_scan_id": "NAV-PARENT", "parent_revision": revision,
        "rescan_target": "expiry_date"}, files={"files": ("closeup.png", data.getvalue(), "image/png")})
    assert response.status_code == 409 and "changed" in response.json()["detail"]
    assert not case.web.UPLOADS.exists()


def test_lineage_escapes_captured_assertions_and_does_not_copy_parent_approval(case):
    response = case.client.get("/inspections/NAV-CHILD")
    assert response.status_code == 200
    assert "&lt;script&gt;alert" in response.text and "<script>alert" not in response.text
    assert "&lt;b&gt;Old reviewer&lt;/b&gt;" in response.text
    assert "The retained reference hash matches" in response.text
    assert "earlier assertions, not corrections applied to this new analysis" in response.text
    assert "Current workflow: Approved" in response.text
    assert case.repo.get("NAV-CHILD").review.status == "draft"
    assert not case.repo.get("NAV-CHILD").review.approved_by


def test_damaged_parent_snapshot_shows_explicit_notice_without_old_assertions(case):
    with case.repo._connect() as conn:
        conn.execute("UPDATE inspection_revision SET record=record || ' ' WHERE scan_id='NAV-PARENT'")
    response = case.client.get("/inspections/NAV-CHILD")
    assert response.status_code == 200
    assert "could not be verified for comparison" in response.text
    assert "The retained reference hash matches" not in response.text
    assert "alert('old price')" not in response.text


def test_approved_parent_keeps_supplementary_closeup_navigation(case):
    response = case.client.get("/inspections/NAV-PARENT")
    assert response.status_code == 200
    assert 'href="/?rescan=NAV-PARENT&amp;target=expiry_date"' in response.text
    assert 'href="/?rescan=NAV-PARENT&amp;target=batch_number"' in response.text
    assert 'action="/inspections/NAV-PARENT/review"' not in response.text


@pytest.mark.parametrize("missing", [False, True])
def test_unavailable_parent_images_are_warned_before_capture(case, missing):
    if missing:
        case.image.unlink()
    else:
        case.image.write_bytes(b"changed retained image")
    response = case.client.get("/", params={"rescan": "NAV-PARENT", "target": "expiry_date"})
    assert response.status_code == 200
    assert "The original evidence is missing or changed" in response.text
    assert "cannot be created until the matching files are restored" in response.text
    assert 'role="alert"' in response.text
    case.login(case.outsider)
    response = case.client.get("/", params={"rescan": "NAV-PARENT"})
    assert "The original evidence is missing or changed" not in response.text
    assert Elements(response.text).by_id("parent-scan-id")["value"] == ""
