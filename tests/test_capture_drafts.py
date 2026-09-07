"""Real SQLite/original image and authenticated draft API regression coverage."""
from __future__ import annotations

import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from PIL import Image

from tula.domain.models import Analysis, PackageFacts, Scan, sha256_file
from tula.security import install_security
from tula.services import capture_drafts as draft_module
from tula.services.capture_drafts import CaptureDrafts, DraftError
from tula.storage.db import Repository
from tula.web.capture_drafts import install_capture_drafts


def pixels(color="white"):
    image = Image.new("RGB", (120, 80), color)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


@pytest.fixture
def case(tmp_path):
    app = FastAPI()
    repo = Repository(tmp_path / "data/tula.db")
    security = install_security(app, repo.path)
    password = "Capture draft test passphrase 2026!"
    admin = security.create_user("draft-admin", password, role="admin", bootstrap=True)
    user = security.create_user("draft-inspector", password, actor_id=admin.id)
    other = security.create_user("other-inspector", password, actor_id=admin.id)
    web = SimpleNamespace(app=app, repo=repo, ROOT=tmp_path)
    install_capture_drafts(web)
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
        def login(who):
            session = security.login(who.username, password)
            client.cookies.set("tula_session", session.token)
            client.headers["X-CSRF-Token"] = session.session.csrf_token
        login(user)
        yield SimpleNamespace(client=client, root=tmp_path, repo=repo, security=security, user=user,
                              other=other, admin=admin, login=login,
                              service=app.state.capture_drafts, data=pixels(), id=uuid.uuid4().hex)


def post(case, *, draft_id=None, revision=0, token=None, details=None, content=None, edit=None):
    return case.client.post(f"/v1/capture/drafts/{draft_id or case.id}",
                            files=[("files", ("../package.png", content or case.data, "image/png"))],
                            data={"revision": revision, "save_token": token or uuid.uuid4().hex,
                                  "details": json.dumps(details or {}), "panels": "back",
                                  "edits": json.dumps([edit or {"rotation": 90, "crop": [.1, .1, .9, .9]}])})


def record(case, draft_id=None):
    with case.repo._connect() as conn:
        row = conn.execute("SELECT * FROM capture_draft WHERE id=?", (draft_id or case.id,)).fetchone()
        return dict(row), json.loads(row["record"])


def original(case, draft_id=None):
    return case.service._original(record(case, draft_id)[1], 0)


def test_save_resume_roundtrip_reconstruction_original_hash_and_attestations(case):
    details = {"title": "Milk package", "lane": "citizen", "region": "Test district", "concerns": "milk, soy",
               "geo": [12.9715987, 77.5945661],
               "dimensions": {"pdp_width_mm": 90.5}, "complete": True,
               "context": {"category": "food", "category_confirmed": True, "bundle_type": "single",
                           "bundle_confirmed": True, "is_imported": False, "imported_confirmed": True}}
    response = post(case, details=details)
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 1
    source = original(case)
    assert source.read_bytes() == case.data
    assert list(source.parent.iterdir()) == [source]
    reconstructed = CaptureDrafts(Repository(case.repo.path), case.root, case.security)
    restored = reconstructed.get(case.id, case.user)
    image = restored["images"][0]
    assert image["sha256"] == sha256_file(str(source))
    assert image["filename"] == "package.png" and image["original_size"] == [120, 80]
    assert image["rotation"] == 90 and image["crop"] == [.1, .1, .9, .9] and image["panel"] == "back"
    assert restored["details"]["region"] == "Test district"
    assert restored["details"]["geo"] == [12.971599, 77.594566]
    assert restored["details"]["dimensions"] == {"pdp_width_mm": 90.5}
    assert restored["details"]["context"]["is_imported"] is False
    assert not restored["details"]["complete"]
    assert all(not value for name, value in restored["details"]["context"].items() if name.endswith("_confirmed"))
    download = case.client.get(image["url"])
    assert download.status_code == 200 and download.content == case.data
    assert "no-store" in download.headers["cache-control"]
    assert str(source.parent) not in json.dumps(restored)
    with case.repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM inspection").fetchone()[0] == 0
    events = case.security.audit_events(entity_type="capture_draft")
    assert [e["action"] for e in events] == ["capture_draft.created"]
    assert events[0]["after"] == {"revision": 1, "image_count": 1, "total_bytes": len(case.data)}
    assert "Milk package" not in json.dumps(events)


@pytest.mark.parametrize("who", ["other", "admin"])
def test_drafts_and_originals_remain_private_even_from_other_admins(case, who):
    assert post(case).status_code == 200
    image_url = case.client.get(f"/v1/capture/drafts/{case.id}").json()["images"][0]["url"]
    case.login(getattr(case, who))
    assert case.client.get("/v1/capture/drafts").json() == {"drafts": []}
    assert case.client.get(f"/v1/capture/drafts/{case.id}").status_code == 404
    assert case.client.get(image_url).status_code == 404
    assert case.client.delete(f"/v1/capture/drafts/{case.id}?revision=1").status_code == 404
    assert post(case, revision=1).status_code == 404
    assert original(case).read_bytes() == case.data


def test_csrf_and_authentication(case):
    token = case.client.headers.pop("X-CSRF-Token")
    assert post(case).status_code == 403
    case.client.headers["X-CSRF-Token"] = token
    case.client.cookies.clear()
    assert case.client.get("/v1/capture/drafts", follow_redirects=False).status_code == 401


def test_idempotent_save_retry_stale_save_and_stale_delete_preserve_newer_revision(case):
    token = uuid.uuid4().hex
    assert post(case, token=token).status_code == 200
    first_source = original(case)
    retry = post(case, token=token)
    assert retry.status_code == 200 and retry.json()["revision"] == 1
    assert original(case) == first_source and len(list(case.service.root.iterdir())) == 1
    assert post(case, token=token, details={"title": "different"}).status_code == 409
    latest = post(case, revision=1, details={"title": "Latest"}, content=pixels("red"))
    assert latest.status_code == 200 and latest.json()["revision"] == 2
    assert not first_source.exists()
    assert post(case, revision=1).status_code == 409
    assert case.client.delete(f"/v1/capture/drafts/{case.id}?revision=1").status_code == 409
    assert case.client.get(f"/v1/capture/drafts/{case.id}/images/0?revision=1").status_code == 409
    assert case.client.get(f"/v1/capture/drafts/{case.id}").json()["details"]["title"] == "Latest"
    assert len(list(case.service.root.iterdir())) == 1
    assert len(case.security.audit_events(entity_type="capture_draft")) == 2
    assert case.client.delete(f"/v1/capture/drafts/{case.id}?revision=2").status_code == 200
    assert case.service.list(case.user) == [] and not list(case.service.root.iterdir())


@pytest.mark.parametrize("fault", ["missing", "tampered"])
def test_missing_or_tampered_original_blocks_manifest_and_download(case, fault):
    assert post(case).status_code == 200
    source = original(case)
    if fault == "missing":
        source.unlink()
    else:
        source.write_bytes(case.data[:-1] + b"x")
    assert case.client.get(f"/v1/capture/drafts/{case.id}").status_code == 409
    assert case.client.get(f"/v1/capture/drafts/{case.id}/images/0?revision=1").status_code == 409
    assert case.service.list(case.user)[0]["id"] == case.id


@pytest.mark.parametrize("details", [{"lane": "bench"}, {"title": "x" * 101}, {"region": "x" * 151},
    {"concerns": "x" * 601}, {"context": {"category": "fiction"}}, {"context": {"is_imported": "false"}},
    {"dimensions": {"pdp_width_mm": False}}, {"dimensions": {"pdp_height_mm": float("inf")}},
    {"context": {"attested_by": "someone"}}, {"parent_scan_id": "missing-parent"},
    {"geo": [91, 77]}, {"geo": [12]}, {"geo": [True, 77]}])
def test_invalid_details_are_rejected_without_saved_or_staged_images(case, details):
    assert post(case, details=details).status_code in {409, 422}
    assert not list(case.service.root.iterdir())
    assert case.service.list(case.user) == []


def test_limits_reject_excess_originals_or_account_quota_without_touching_existing(case, monkeypatch):
    assert post(case).status_code == 200
    source = original(case)
    monkeypatch.setattr(draft_module, "MAX_DRAFT_BYTES", len(case.data) - 1)
    assert post(case, revision=1).status_code == 413
    assert original(case) == source
    monkeypatch.setattr(draft_module, "MAX_DRAFT_BYTES", 100 * 1024 * 1024)
    monkeypatch.setattr(draft_module, "MAX_ACCOUNT_BYTES", len(case.data))
    assert post(case, draft_id=uuid.uuid4().hex).status_code == 413
    monkeypatch.setattr(draft_module, "MAX_ACCOUNT_BYTES", 300 * 1024 * 1024)
    monkeypatch.setattr(draft_module, "MAX_DRAFTS", 1)
    assert post(case, draft_id=uuid.uuid4().hex).status_code == 413
    assert len(list(case.service.root.iterdir())) == 1 and source.exists()


def test_real_concurrent_writers_only_one_can_advance_revision(case):
    assert post(case).status_code == 200

    def save(title):
        service = CaptureDrafts(Repository(case.repo.path), case.root, case.security)
        try:
            return service.save(case.id, 1, uuid.uuid4().hex, case.user,
                                [UploadFile(file=io.BytesIO(case.data), filename="package.png")],
                                details={"title": title})["revision"]
        except DraftError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(save, ["First writer", "Second writer"])) == [2, 409]
    assert case.service.get(case.id, case.user)["revision"] == 2
    assert len(list(case.service.root.iterdir())) == 1


def test_expiry_cleanup_preserves_other_data_and_does_not_resurrect_draft(case, monkeypatch):
    assert post(case).status_code == 200
    source = original(case)
    other = case.root / "data/not-drafts/keep.txt"
    other.parent.mkdir()
    other.write_text("Unrelated evidence", encoding="utf-8")
    expires = record(case)[0]["expires_at"]
    monkeypatch.setattr(case.service, "clock", lambda: expires + 1)
    assert case.service.list(case.user) == []
    assert not source.exists() and other.read_text(encoding="utf-8") == "Unrelated evidence"
    assert case.client.get(f"/v1/capture/drafts/{case.id}").status_code == 404
    assert post(case).status_code == 404
    assert "capture_draft.expired" in [e["action"] for e in case.security.audit_events(entity_type="capture_draft")]


def test_delete_or_replacement_never_removes_original_referenced_by_inspection(case):
    assert post(case).status_code == 200
    source = original(case)
    analysis = Analysis(scan=Scan(scan_id="accepted-evidence-reference", inspector_id=case.user.id,
                                  frames=[str(source)], frame_hashes={str(source): sha256_file(str(source))}),
                        package=PackageFacts())
    case.repo.save(analysis)
    assert post(case, revision=1, content=pixels("red")).status_code == 200
    assert source.exists() and source.read_bytes() == case.data
    assert case.client.delete(f"/v1/capture/drafts/{case.id}?revision=2").status_code == 200
    assert source.exists()
    case.service.cleanup()
    assert source.exists()


def test_delete_keeps_a_directory_referenced_by_another_active_draft(case):
    assert post(case).status_code == 200
    source = original(case)
    duplicate = uuid.uuid4().hex
    with case.repo._connect() as conn:
        conn.execute("""INSERT INTO capture_draft SELECT ?,owner_id,revision,state,created_at,updated_at,
                     expires_at,total_bytes,save_token,fingerprint,record FROM capture_draft WHERE id=?""", (duplicate, case.id))
    assert case.client.delete(f"/v1/capture/drafts/{case.id}?revision=1").status_code == 200
    assert source.exists() and case.service.get(duplicate, case.user)["images"][0]["sha256"] == sha256_file(str(source))


def test_parent_rescan_is_reauthorized_and_hash_verified_on_resume(case):
    parent_file = case.root / "parent.png"
    parent_file.write_bytes(case.data)
    analysis = Analysis(scan=Scan(scan_id="private-parent", inspector_id=case.other.id,
                                  frames=[str(parent_file)], frame_hashes={str(parent_file): sha256_file(str(parent_file))}),
                        package=PackageFacts())
    case.repo.save(analysis)
    assert post(case, details={"parent_scan_id": analysis.scan.scan_id}).status_code == 403
    case.login(case.admin)
    assert post(case, details={"parent_scan_id": analysis.scan.scan_id}).status_code == 200
    assert case.service.get(case.id, case.admin)["prior_count"] == 1
    parent_file.write_bytes(pixels("red"))
    assert case.client.get(f"/v1/capture/drafts/{case.id}").status_code == 409


def test_audit_failure_rolls_back_draft_and_cleans_only_new_attempt(case, monkeypatch):
    assert post(case).status_code == 200
    source = original(case)

    def unavailable(*args, **kwargs):
        raise RuntimeError("Deliberate audit storage failure")

    monkeypatch.setattr(case.security, "_audit", unavailable)
    assert post(case, revision=1, content=pixels("red")).status_code == 500
    assert record(case)[0]["revision"] == 1
    assert original(case) == source and source.read_bytes() == case.data
    assert len(list(case.service.root.iterdir())) == 1


def test_invalid_image_crop_count_and_path_id_cannot_create_a_draft(case):
    assert post(case, content=b"not an image").status_code == 415
    assert post(case, edit={"rotation": 45}).status_code == 422
    assert post(case, edit={"crop": [0, 0, .001, 1]}).status_code == 422
    assert post(case, draft_id="invalid-id").status_code == 404
    response = case.client.post(f"/v1/capture/drafts/{case.id}",
        files=[("files", (f"image-{index}.png", case.data, "image/png")) for index in range(13)],
        data={"save_token": uuid.uuid4().hex})
    assert response.status_code == 413
    assert not list(case.service.root.iterdir())


def test_template_exposes_explicit_limits_resume_controls_and_named_dialogs():
    from test_capture_quality import page
    markup = page()
    assert markup.find(id="save-draft")["type"] == "button"
    assert markup.find(id="capture-drafts")["data-enabled"] == "true"
    assert "100 MB" in markup.html and "300 MB" in markup.html and "30 days" in markup.html
    assert "My saved drafts" in markup.html and "Save capture draft" in markup.html
    for name in ("edit", "camera"):
        assert markup.find(id=name + "-dialog")["aria-labelledby"] == name + "-dialog-heading"
    assert "<h2>Package images</h2>" in markup.html and "<h2>Inspection details</h2>" in markup.html
