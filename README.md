# Tula · तुला

A local SIH prototype for screening packaged-commodity labels using CPU OCR, declaration extraction, measured character heights, draft rules and evidence reports.

The bundled pack `2026.09.07-legal-review-1` has source-linked legal corrections and applicability gates, but still requires independent legal sign-off. The supplied PRD also describes capabilities beyond this application. See the current [implementation ledger](docs/IMPLEMENTATION_STATUS.md) and [legal source matrix](docs/LEGAL_SOURCE_MATRIX.md).

Start with the [user manual and improvement plan](docs/USER_MANUAL.md) for a guided tour, every control and scenario, result interpretation, and current limitations. [What the recogniser can and cannot read](docs/OCR_CAPABILITY.md) reports measured recovery across twenty-four degraded labels, including dot-matrix MRP and date coding.

## Run

Python 3.11 or later:

```powershell
python -m pip install -e ".[dev]"
python -m tula.security bootstrap --username your.admin --display-name "Deployment Administrator"
python -m uvicorn tula.web.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

Bootstrap is a first-install step only: enter a password of at least 12 characters at the hidden prompts. Existing installations should use their provisioned account. Open [the local console](http://127.0.0.1:8000) and sign in. Real RapidOCR is the default; fixture text is explicitly selected only in testing/Bench. Recognition failures produce warnings and inconclusive results.

**Every page requires a signed-in account.** A checkout with no accounts stops at `/login`, which is the intended behaviour, not a fault.

### Local demonstration

To get a populated console instead of an empty one, run the seed script rather than bootstrapping by hand:

```powershell
python -m pip install -e ".[dev]"
python scripts/seed_demo.py
python -m uvicorn tula.web.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

It provisions `demo.admin`, `demo.inspector` and `demo.supervisor` (password `TulaDemo2026!judge`) and builds twelve inspections that walk the real workflow — package facts confirmed, findings decided, submitted, and approved by a supervisor who did not contribute to them. That last step is what the dashboard's approved-compliance rate counts, so without it every headline figure reads zero.

These accounts exist for a loopback walkthrough. **Delete them or change their passwords before exposing the application to anything but localhost**, and re-run with `--reset` to discard seeded records.

Tula emits privacy-bounded JSON operational events for HTTP requests, inspection stages and report generation. Each response carries `X-Request-ID`; background inspection events retain the originating upload request ID. Use it to correlate a user's error with service logs. Raw URLs, query strings, OCR text, filenames, evidence paths, cookies and passwords are excluded. The application suppresses Uvicorn's duplicate raw access line; keep `--no-access-log` explicit and apply the same query-string policy to any reverse proxy.

`TULA_DATA_DIR` selects a separate runtime directory for the web app and default bootstrap database. Source checkouts default to the repository; installed wheels default to `~/.tula`. Set this variable consistently before provisioning and starting the server. Bootstrap also accepts an explicit `--database` path. Keep the database, originals and working images together. See [authentication and deployment](docs/SECURITY.md) for roles, password management and HTTPS requirements outside loopback.

Use the [operations guide](docs/OPERATIONS.md) for offline backup, archive verification and restore. Restore preserves the original runtime path; it does not relocate retained evidence.

## Try it

A left sidebar groups the pages by the kind of work they are, and every demonstration
instrument sits behind `/lab` so nothing that tests the system appears on a casework page.

| Page | Group | Purpose |
|---|---|---|
| `/` | — | Upload/capture images, assign panels, rotate/crop, check quality, save/resume private capture drafts and confirm package facts |
| `/processing` | Casework | Find saved uploads, follow progress, retry failures and open completed inspections |
| `/repository` | Casework | Search inspections and product history. Operational inspections only; `?source=bench` opens generated runs |
| `/dashboard` | Casework | Local aggregates, inspection coverage by district and optional recorded coordinates; each headline figure links to the records behind it |
| `/inspections/{id}` | — | Review evidence, correct readings, record decisions, rescan, submit and independently approve, in five record tabs |
| `/rules` | Reference | Inspect draft rules, dates and assurance requirements |
| `/docs` | Reference | Authenticated searchable API reference |
| `/admin/users` | Administration | Administrator account and access management |
| `/admin/rules` | Administration | Administrator rule import, selection and rollback |
| `/admin/audit` | Administration | Append-only application event log |
| `/lab` | Demonstration | Every test instrument, with the generated-versus-operational separation stated |
| `/bench` | Demonstration | Generate labels with known geometry; compare fixture and real OCR |
| `/bench/scenarios` | Demonstration | Run the 20-scenario acceptance matrix |

Try `undersize_numerals`, `boundary_straddle`, `english_only`, `partial_capture`, `unreadable_capture` and `citizen_advisory`. English-only passes the script screen; the erroneous bilingual requirement and MRP rounding check have been removed.

A photograph has no inherent millimetre scale. Height checks need a calibrated reference and known panel dimensions; otherwise they remain inconclusive. The bench supplies synthetic calibration. The CLI also accepts trusted `<image>.meta.json` sidecars; web uploads do not import calibration sidecars.

```powershell
python -m tula.cli data/samples/rendered-front.png --engine rapidocr --lane citizen --out out/cli-demo
python scripts/make_scale_card.py --out out/scale-card.pdf
```

Print the card at 100% and physically verify its dimensions. A detected marker does not prove correct printing or coplanarity.

## Test

```powershell
python -m pytest -q
python -m ruff check src tests scripts
python scripts/audit_images.py
python -m build --wheel
```

The image audit runs **53 analyses**: 20 scenarios with fixture OCR, 20 with real OCR, 10 distinct repository photographs and three rotated/degraded stress images. Results and sample exports are in `out/audit/`. The audited matrix passed **40/40** checks. The photographs are smoke tests, not a labeled accuracy benchmark.

The current complete integration run passed **1,505 tests in 495.47 seconds**; Ruff and the capture JavaScript syntax check passed. The coordinate cases cover optional capture, invalid-coordinate rejection, private-draft persistence, background-job propagation, legacy database migration, indexed dashboard aggregation, accessible geography rendering and scoped point-to-repository drill-down, in addition to the existing review, rescan, OCR, reporting, backup and security coverage. Synthetic coordinate plots were visually inspected at 1440 px and 390 px, including keyboard focus on point links; they use no operational location data. See the [manual's testing section](docs/USER_MANUAL.md#15-tests-actually-performed), [current geography verification](out/geography-installed-qa/VERIFICATION.md), [rescan verification](out/rescan-browser-qa/VERIFICATION.md) and [performance verification](out/geography-installed-qa/final/geography-drilldown-performance.json) for exact boundaries. Full accessibility certification, browser-permission testing and physical-device GPS acceptance remain pending.

The current wheel passed **clean dependency installation outside the checkout**: all **117** packaged application/asset/rule members matched source, including **27 web assets** and 18 rules; all 50 installed distributions resolved inside the isolated environment and three OCR models were verified. Actual OCR, private saved drafts, separate-account approval, audited exports, Processing and exact restoration of 26 tables/eight retained files passed. The installed geography verifier confirmed dataset separation, validation, rounding, accessible SVG/table output, outside-extent disclosure, scoped repository drill-down, both geography indexes and the opt-in permission policy. The [receipt](out/geography-installed-qa/final/acceptance.json) and [verification narrative](out/geography-installed-qa/VERIFICATION.md) identify the final wheel by SHA-256. On a copied 100,000-record SQLite fixture with 50,000 inspections in one coordinate group, group count plus a recent 25-record page improved from 59.549 ms to 8.427 ms median after the drill-down index. This is synthetic local query evidence, not external production load. Earlier image-quality, repository and worklist measurements remain linked from the operations guide. Windows fonts remain a host dependency, and another machine, HTTPS/service operation, physical location testing, representative OCR validation and independent legal acceptance remain unfinished.

The [latest actual-photo evaluation](out/real-label-evaluation-rescan-final/REPORT.md) ran ten unchanged distinct photos: **12/16 readable targets accepted exactly, 15/16 recovered as structured candidates, zero wrong accepted and four abstentions**. One correct brand and both difficult front-price candidates remain review-only; the prices retain unverified currency. Another 134 field/photo pairs remain unknown. The [four additional photographs](out/additional-real-label-evaluation-rescan-final/REPORT.md) yielded **10/14 selected literal targets, 12/14 OCR candidates and 4/5 correctly accepted structured targets**, with zero wrong among those five and one batch abstention. This improves the older uncertainty run's 9/14 literal recovery: the reflective can's two dates and `103A` code now survive as candidates, but conflicting rows require review and `B:` still does not produce a structured batch. `OCT/26` remains misread as `0CT/26` with explicit uncertainty. There are no normalized-date or net-quantity targets in the additional set. These are unblinded, single-agent development sets, not independent accuracy estimates. Source rights, ambiguity and exact source/model/annotation boundaries are in the [actual-run findings](out/rescan-final-validation/FINDINGS.md).

The [latest retained controls](out/retained-ocr-rescan-final/results.json) confirm clean 5/5 structured and accepted; moderate 5/5 structured with four held for review; severe 1/5. The repeated real Parle photo retains one correct review-only price candidate, zero accepted. These metrics match the older joined-batch checkpoint; severe damage still needs a clearer image. After the final supplementary-summary fix, a [separate extraction-only replay](out/rescan-supplementary-replay/FINDINGS.md) reproduced identical full extractions and metrics across all 18 retained OCR records, with source/model/input integrity verified and no recognizer invoked. Thus the `rescan-final` artifacts document actual recognition before that last parser fix, while the replay verifies current extraction. These checks do not establish legal correctness or validate report/UI or deployment. Reproduce actual-image runs with `scripts/evaluate_real_labels.py`, `scripts/evaluate_additional_labels.py` and `scripts/verify_retained_ocr.py`, always using fresh output directories and preserving annotations and original pixels.

The expanded regression suite covers extraction, uncertainty, evidence tiers, coverage, uploads, immutable images, integrity, barcodes and report attachments. See [the audit](docs/AUDIT.md) for results and limits.

## Evidence and reports

- Declarations retain their source image and bounding box; measurements use that image's scale.
- Partial or unreadable captures cannot establish absence. Completeness attestation remains an operator assertion.
- Citizen and marketplace findings are advisory. This is application policy, not a ruling on legal admissibility.
- Originals and normalized images have SHA-256 records. Missing/changed evidence blocks web report and image serving.
- Exports include PDF, editable DOCX, draft notice and JSON. PDFs contain crops and an attached `analysis.json`; they are not certified PDF/A-3.
- Web PDF, report DOCX and notice DOCX exports retain unique files, revision/hash registrations and matching actor audit events; evidence is verified before and after rendering.
- Bench runs create new filenames, preserving earlier evidence.
- Inspector corrections, finding decisions, package facts and approval actions retain revisions and audit entries. Approval requires an independent supervisor or administrator.
- Queued jobs start with the server; interrupted attempts retain evidence for explicit retry.

## Layout

```text
src/tula/analyse.py       pipeline orchestration
src/tula/ocr/             CPU RapidOCR and explicit fixture adapter
src/tula/extract/         located declarations and normalizers
src/tula/imaging/         scale, glyph measurement and uncertainty
src/tula/rules/           evaluator, exemptions and rule loader
rules/lmpcr-2011/         18 draft definitions, including withdrawn checks
src/tula/forensics/       barcode decoding and GTIN checks
src/tula/report/          shared model, evidence, PDF and DOCX
src/tula/storage/         SQLite and product history
src/tula/web/             FastAPI, Jinja and local HTMX
src/tula/labgen.py        generated labels and geometry truth
src/tula/bench.py         scenario matrix
scripts/audit_images.py  reproducible image audit
tests/                   automated regressions
docs/AUDIT.md            fixes, sources and PRD gaps
```

Historical rows retain their original rule versions and findings. Upgrades do not silently rewrite them; back up runtime data before migration.
