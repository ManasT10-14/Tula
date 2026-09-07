# Repository audit — 7 September 2026

Reference: [the supplied SIH PRD](https://claude.ai/code/artifact/8df904f7-104a-4b5d-bcc1-a7147916dade). The complete artifact was read in the browser. It describes a much larger four-lane platform than this Python prototype. Its sample legal constants are explicitly draft material, and several were incorrect in the repository.

The audit covered routes and templates, OCR, extraction, metrology, exemptions and rule JSON, evidence records, SQLite, CLI, packaging, scripts and tests. Existing photographs and historical inspection records were preserved. Browser testing used a separate database under `out/audit/web-state`.

## Blockers corrected

| Area | Problem | Change |
|---|---|---|
| OCR startup | Auto mode could silently use fixture text | Auto selects real OCR; failures become recorded evidence gaps |
| CPU inference | Excessive threading and repeated model loading | Cached engine, bounded ONNX threads, reader lock; blocking web work runs off the event loop |
| OCR adapter | Object-style results were dropped | Support boxes/texts/scores and legacy tuples |
| Rotated labels | Vertical boxes scrambled address ordering | Sort vertical columns; preserve original coordinates |
| Confidence | Weak OCR could drive adverse findings | Retain weak spans for review, exclude them from adjudication and measurement |
| Provenance | Address and generic-name context crossed images | Track frames and bound context to the same frame |
| Quantities | Nutrition values, serving sizes and price denominators became net quantity | Context exclusions, numeric boundaries and positive-value validation |
| Prices | Large amounts truncated; unit price counted as another MRP | Complete-number parsing and removal of unit-price expressions |
| Dates | Expiry became packing date; malformed input silently defaulted | Cue-based extraction, calendar validation, same-frame split dates and explicit form errors |
| Arithmetic | Mass and volume compared as equivalent | Matching dimensions required; otherwise inconclusive |
| Exemptions | Count/length received g/ml exemptions; marketing words implied exclusions | Restrict units and tighten selected text cues; legal review remains required |
| Scale | Images sharing a panel borrowed calibration | Estimate and use scale per frame; no cross-frame fusion |
| Geometry | One span implied a square PDP | Require width and height; reject invalid geometry and raster claims of exact vector scale |
| Character height | OCR boxes treated as physical outlines; small glyphs missed | Pixel refinement, cap-height population handling and propagated spread; proxies are Tier C |
| Markers | Arbitrary ArUco identifiers assumed to be the LM card | Card IDs 0–3 only; unequal sides increase uncertainty |
| Tier gate | Weak measurements inherited a stronger scan tier | Use weaker measurement/scan tier for pass and violation decisions |
| Coverage | Front/back assumed complete; blank extra frames overlooked | Six named faces or explicit attestation, with every-frame legibility checks |
| Barcode | Only OCR digits available | Decode EAN/UPC/GTIN-14 from pixels; multiple codes require identity review |
| Forensics | Invalid codes/prefixes could imply an offence or origin | Unverified/corroborative findings; prefixes do not establish origin |
| Uploads | Malformed files, duplicate names and invalid enums crashed or overwrote files | Decode/validate, bound size and count, unique names, retained originals and EXIF-normalized copies |
| Evidence | Repeated bench runs overwrote images; changed files entered reports | Immutable filenames, ingestion hashes, integrity endpoint and verified serving/export |
| Exports | No crops/embedded JSON; wrong advisory headlines | PDF/DOCX crops, PDF attachment, advisory/NA summaries, clear draft notices and pagination fixes |
| Web | Presets lost lane; advisory displayed green; HTMX required CDN | Preserve lane, fix styling/filtering, local HTMX, visible errors and draft status |
| Storage | Shrinkflation compared different dimensions | Match base units; foreign keys, WAL and busy timeout |
| Packaging | Wheels omitted resources/dependencies | Include rules, templates, assets and runtime dependencies |

## Legal corrections and source limits

Version: `2026.09.07-draft`. Legacy IDs/filenames remain stable; `citation.clause` carries corrected references. “Check passed” means a particular screening check passed, not full legal compliance.

* Rule 9(4) permits Hindi in Devanagari **or** English. The bilingual violation was removed. The replacement is a net-quantity script screen, not full language validation. [Official parliamentary reply](https://sansad.in/getFile/annex/256/AU1181.pdf?source=pqars).
* Height screening now references Rule 7(2), Table I. Ordinary-package area bands use 1, 1.5, 2.5, 4 and 6 mm; the invented universal 1 mm floor was removed. [Department of Consumer Affairs FAQ](https://consumeraffairs.gov.in/public/upload/admin/cmsfiles/whatsnews/FAQs_on_Packaged_Commodities%2C_Rules_2011_whatsnews.pdf).
* Blanket bottom-placement and 50-paise MRP rounding checks were withdrawn. Rule 9(2) addresses reading through liquid; Rule 11 concerns quantity. Grouping requires review because preprinted and online declarations may occupy separate places. [Official Rajasthan rules compilation](https://legalmetrology.rajasthan.gov.in/Upload/LegalScheduleFile/LEGAL%20METROLOGY%20(PACKAGED%20COMMODITY)%20RULES%202011.pdf).
* Unit-price screening uses a 1 January 2024 commencement boundary following the G.S.R. 714(E) deferral. [Official September 2023 notification](https://consumeraffairs.gov.in/public/upload/files/Amendment%20of%20PCR%20ext%20till%2031.12.2023%20%281%29_1732871950.pdf).

This is **not** a complete consolidated legal ruleset. The FAQ and older state compilation disagree on the smallest blown/formed height band (2 versus 1.5 mm); the draft follows the FAQ and needs Gazette reconciliation. The arbitrary 5% unit-price tolerance was replaced with decimal arithmetic rounded to two places. Required statutory unit-price bases and exceptions still need legal review. Food/date exceptions, classifications, revised-price stickers, penalties and historical amendments remain incomplete. Source text is a screening paraphrase unless separately verified.

## Verification

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests scripts
python scripts/audit_images.py
python -m build --wheel
```

The original suite had 162 tests. New regressions cover parsing, provenance, degraded evidence, validation, uploads, immutable files, PDF attachments/crops, barcode decoding, dimensional comparisons and scale-card identity. Final result: **227 tests passed; Ruff clean; installed-wheel smoke test passed**.

`out/audit/image-results.json` records **53 runs**: 20 generated scenarios with fixture OCR, the same 20 with RapidOCR, 10 distinct existing photos, and rotated/blurred/blank stress images. All 40 scenario checks passed, including geometry interval comparisons where available. All 13 photo/stress runs produced zero adverse findings. Two photos yielded fewer than three OCR lines and remained unresolved. This is not a labeled accuracy benchmark or proof that those products comply.

The actual browser test ran the real-OCR citizen preset, verified advisory output and truth intervals, followed the full record and uploaded a generated image through the inspection form. HTTP tests cover pages, upload failures, image/report access and integrity errors. PDF attachment/crop structure was checked; Poppler-rendered pages were visually reviewed. The bundled DOCX renderer lacked LibreOffice, so installed Microsoft Word rendered both editable outputs for visual review. An installed wheel was checked outside the source tree.

## PRD gaps still requiring work

| Capability | Current state / completion requirement |
|---|---|
| Android/offline capture | Web uploads only; no guided camera, depth acquisition, signed capture or offline sync queue |
| Quality gate | Confidence/coverage safeguards; no validated blur/glare/skew/curvature/package-boundary model |
| Multilingual OCR / Path B | CPU RapidOCR only; Hindi often unreadable. No multilingual trained recognizer, VLM fallback or arbitration |
| Certified metrology | Synthetic validation only; physical caliper data, empirical error calibration, perspective/lens correction and certification needed |
| Marketplace | Advisory lane policy; no crawler, seller integration, listing ingestion or monitor |
| Premarket | Lane policy; no vector PDF/dieline parser or real Tier A acquisition |
| Review/approval | Draft fields and export; no persisted officer overrides, review UI, second approval, signature or service |
| Identity/security | No authentication, RBAC, tenant isolation, retention policy or public-deployment hardening |
| Custody | Ingestion hashes and originals; no signed camera time, tamper-evident DB or immutable object store |
| Archival exports | Ordinary attached-JSON PDF, not validated PDF/A-3; DOCX is editable and unsigned |
| Legal history | Draft date predicates, not a complete Gazette archive; old records retain their original findings |
| Production platform | SQLite/single CPU process; no durable queue, Postgres/PostGIS, object storage or scale/load validation |
| Acceptance dataset | Small synthetic matrix and unlabeled photo smoke tests; no held-out national benchmark |

The corrected repository supports a local SIH demonstration and continued development. These remaining capabilities are substantial work and cannot truthfully be described as completed or “perfect.”
