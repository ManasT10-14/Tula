# Tula user manual and feature coverage

Updated 7 September 2026. This guide describes the inspection workspace, its controls, verified source code and known unfinished work. Start with the guided tour below. Sections 2–7 cover everyday use; sections 8–15 explain rules, reports, administration and coverage. Section 17 gives the next improvement priorities.

**Open the demonstration: [Inspect](http://127.0.0.1:8000/).** Start it from the repository with `python scripts/seed_demo.py` followed by `python -m uvicorn tula.web.app:app --port 8000`, and sign in as `demo.inspector` or `demo.supervisor`. Every record named in this guide is produced by that seed script through the real pipeline, so the walkthrough regenerates on any machine. Record identifiers differ on each run: find records by brand in Repository rather than by identifier.

These loopback links address the computer running Tula. On a phone, `127.0.0.1` addresses the phone itself. For another device, use an administrator-configured HTTPS deployment address; plain LAN HTTP is refused by the application's transport protection.

The seeded demonstration runs on `2026.09.07-legal-review-1`. Inspections saved under an earlier pack keep the version they were judged under, as intended: a record is evidence of what the rules said on the day it was assessed.

**Current walkthrough:** the demonstration includes targeted close-ups, parent-revision comparison, conflicting-reading safeguards, clearer unresolved finding headings, possible-overexposure guidance, a retained-area warning for tight crops, and optional consented device-coordinate capture feeding an accessible dashboard plot. The original ten real photos and four additional photos completed actual OCR evaluation, followed by a separate replay through the final extraction code. Section 15 separates these results from generated examples and independent validation still needed. Section 17 lists further improvements.

Tula helps an officer turn package photographs into extracted declarations, evidence-linked screening findings, a recorded review and downloadable reports. The rules remain a draft. A machine flag, an officer decision and supervisor approval are three different stages.

For your first walkthrough, follow **Inspect → Review inspection → Evidence and corrections → Finding decisions → Submit → Independent approval → Report → Repository → Dashboard**. Then explore Bench, Scenarios and Rules. The improvement plan at the end is for the next development pass.

## A guided tour before you upload anything

You can explore the saved examples without changing them:

1. Open [Inspect](http://127.0.0.1:8000/). Find the image area, capture source, package context and Analyze button. This is where a new inspection starts.
   To find an accepted upload later, open [Processing](http://127.0.0.1:8000/processing). Its default unfinished view can be empty while **All jobs** still shows completed examples.
2. Open [Repository](http://127.0.0.1:8000/repository) and open the **Sparkle Max** detergent record. Read the summary cards, open the evidence image, and expand one finding. Compare **what OCR read**, **what the rule checked**, and **what the officer decided**. Its two height violations are the metrology result: 1 kg of detergent on a 200 × 300 mm panel with numerals well under the prescribed minimum.
   Then open the **Nilgiri Estate** tea record. Same pipeline, no violations: it is the control that shows the system is not simply a violation printer.
3. Scroll to **Officer review & approval**. Locate the correction, finding-decision and rescan controls described in section 6. You do not need to save changes to understand them.
   Open the **Neem Fresh** soap record to see three separate violations on one package: a retail price without the prescribed tax wording, and both height rules.
4. Open the **Silk Shine** sachet record. Its 6 ml net quantity puts it under the 10 g/ml threshold, so it is screened **Exempt** and is excluded from the compliance rate rather than counted as a pass.
   Open the **Awadh Pure** ghee record for the opposite state: it is still **Draft**, with nothing decided, which is what an inspection looks like before an officer touches it.
5. Use **Report (PDF)**, **Editable report**, **Draft notice**, or **JSON record** at the top of a record. The approved exempt example has no officer-verified violation for a notice to allege.
6. Open [Repository](http://127.0.0.1:8000/repository). Search `Bikaner`, which returns the three-year shrinkflation series, then clear the search and try Category → Cosmetic. Expand the additional filters to distinguish **Finding outcome** from the overall **Machine outcome**.
7. Open [Dashboard](http://127.0.0.1:8000/dashboard). Use the operational dataset. An approved exempt record does not enter the compliant/non-compliant rate, so the rate is computed from the approved compliant and non-compliant records only; it reads as unavailable when that denominator is empty.
8. Open [Rules](http://127.0.0.1:8000/rules), then [Bench](http://127.0.0.1:8000/bench). Rules explains the draft checks; Bench creates controlled examples. Open **Scenarios** when you want to run and save the 20 preset tests.

For your own first inspection, use the step-by-step sequence in section 3. Do not use QA records as official case evidence.

| Term you will see | Plain meaning |
|---|---|
| OCR | Software reading printed text from an image. |
| Declaration | A statement printed on the package, such as its quantity or price. |
| MRP | Maximum retail price. |
| Net quantity | The declared amount of product, such as 200 g or 1 L; the app does not physically weigh it. |
| Unit sale price | A price for a stated reference quantity. Its required basis depends on applicability. |
| PDP | Principal display panel: the package's main display face. |
| GTIN | Product identifier commonly encoded in the barcode. |
| Finding | One rule check and its observed evidence/result. |
| Revision | A retained version of the inspection before or after a recorded change. |

## Contents

1. [How the supplied reference was used](#1-how-the-supplied-reference-was-used)
2. [Navigation and accounts](#2-navigation-and-accounts)
3. [Your first inspection](#3-your-first-inspection)
4. [Every capture control](#4-every-capture-control)
5. [Read the inspection record](#5-read-the-inspection-record)
6. [Correct, review, rescan and approve](#6-correct-review-rescan-and-approve)
7. [Product, dates, ingredients and allergens](#7-product-dates-ingredients-and-allergens)
8. [Rules and physical measurements](#8-rules-and-physical-measurements)
9. [Reports and retained evidence](#9-reports-and-retained-evidence)
10. [Repository and product history](#10-repository-and-product-history)
11. [Dashboard](#11-dashboard)
12. [Bench and all 20 scenarios](#12-bench-and-all-20-scenarios)
13. [Administration and technical access](#13-administration-and-technical-access)
14. [Coverage against the linked requirements](#14-coverage-against-the-linked-requirements)
15. [Tests actually performed](#15-tests-actually-performed)
16. [Troubleshooting](#16-troubleshooting)
17. [What to improve next](#17-what-to-improve-next)

## 1. How the supplied reference was used

**Yes: [your Claude artifact](https://claude.ai/code/artifact/8df904f7-104a-4b5d-bcc1-a7147916dade) was used as the product requirements reference.** The retained text supplied with the work item contains 43 numbered sections, from **Primary objective** through **Final deliverable**. All 43 were read again for this audit and manual. The retained copy is 27,923 bytes with SHA-256 `cb3dd1e82a2d079fde387977ed93424572b05fff56bb67a2c30c6f753f2b6404`. It is titled *Tula Compliance Engine* and presents a product requirements document for SIH PS 26034.

It informed the four delivery lanes, evidence-linked findings, measurement uncertainty, versioned rules, reports, repository, review workflow and testing approach. It describes a much larger platform than the current application.

The artifact is user-generated, and its own draft warning requires checking legal constants against official text. Its status as the official SIH-issued problem statement has not been independently established. We should not present its example rules, architectural choices or accuracy targets as already verified facts.

One concrete discrepancy: the artifact asks for both Hindi and English. The [official parliamentary explanation of Rule 9(4)](https://sansad.in/getFile/annex/256/AU1181.pdf?source=pqars) permits Hindi in Devanagari or English. The current limited script screen therefore accepts either; it does not verify every declaration's language.

Other source checks cover typography thresholds, dates of amendments, consumer-care requirements, unit-price bases, exemptions and revised-price situations. The [legal source matrix](C:/Users/offic/OneDrive/Desktop/sih/docs/LEGAL_SOURCE_MATRIX.md) records the research and official links. The bundled source pack is now `2026.09.07-legal-review-1`, with source-linked applicability gates and legal corrections. It remains a draft requiring legal sign-off. The Rules page shows the version selected by that running server; each older inspection keeps the version originally used.

The reference's claimed accuracy, response times, full offline mobile support and advanced forensics are targets. They are not measured achievements of the current repository. Section 14 maps every FR-01–FR-32 requirement to actual coverage.

## 2. Navigation and accounts

The main navigation has seven pages:

| Click | What you do |
|---|---|
| [Inspect](http://127.0.0.1:8000/) | Start an inspection, upload/capture package images and begin analysis. |
| [Processing](http://127.0.0.1:8000/processing) | Find accepted uploads, reopen progress, retry failed jobs and open completed inspections. |
| [Bench](http://127.0.0.1:8000/bench) | Generate a controlled test label and vary one property. |
| [Scenarios](http://127.0.0.1:8000/bench/scenarios) | Run the predefined acceptance examples. Opening this page starts test runs. |
| [Repository](http://127.0.0.1:8000/repository) | Search saved records, filter results and reopen inspections. |
| [Dashboard](http://127.0.0.1:8000/dashboard) | View stored inspection activity and review outcomes. |
| [Rules](http://127.0.0.1:8000/rules) | Read the active draft rule definitions and their evidence requirements. |

An individual inspection opens from its product/reference link in Repository or Dashboard, or from **Review inspection →** after processing.

On a narrow phone screen, the top navigation scrolls horizontally; swipe it to reach every page. Wide evidence tables scroll within their own boxes. Keyboard users can Tab to a table or correction-image region and use arrow keys to scroll. Expand a section by selecting its heading/summary. **Skip to content** is the first keyboard shortcut link on each main page.

Click your name in the upper-right corner to change your password: enter **Current password**, **New password**, then **Update password**. There is no separate general Settings/Profile page. **Sign out** ends your session. Administrators additionally see **Administration**, leading to account management, and **Rule versions**, leading to rule-pack administration.

| Account role | Current use |
|---|---|
| Inspector | Create inspections; correct and decide findings on assigned inspections; read history, evidence and reports. |
| Supervisor | Inspector capabilities plus independent approval and reopening approved records. |
| Administrator | Supervisor capabilities plus account management, the audit log and rule-version import/selection. |

Use an account provisioned by the administrator. There is no public self-registration or shipped default password. Session expiry can return you to sign-in; saved inspections remain in the database. Current roles do not implement the reference's separate citizen, brand, auditor and state/district tenancy model.

Sessions expire after 30 minutes of inactivity or eight hours after sign-in. Password or access changes can also sign out existing sessions. Signed-in roles can read the shared records, evidence, reports and main pages; assignment restricts inspector edits, not district/state read access. Saved job status/retry is restricted to its owner or a supervisor/administrator.

The walkthrough was checked using inspector, supervisor and administrator QA accounts. Browser sessions expire, so sign in with an account provisioned for the role you want to rehearse. The retained QA records are demonstration evidence, not official inspections. Use a separately provisioned inspector account when rehearsing the assigned-inspector workflow.

## 3. Your first inspection

Follow this sequence to understand the complete workflow:

1. Open **Inspect**.
2. Select clear photographs of **one physical package**. Include the main display face, back and every other printed panel.
3. Review each preview's **Package panel** assignment. The first image defaults to front/PDP, the second to back; verify these defaults.
4. Add a close-up of small or dotted MRP, manufacturing, expiry and batch markings.
5. Rotate sideways images. Use **Crop / zoom** only when needed, retaining the whole declaration and enough surrounding context.
6. Keep **Capture source → Field inspection** for an officer demonstration. Enter a location if useful.
7. Expand **Confirm package context for applicable rules**. Select the category, bundle, origin and shape you can verify; check the confirmation box. Leave unknown facts unconfirmed. Optionally enter allergens to highlight and measured physical panel dimensions.
8. Confirm **Every printed face of this package is included** only after checking actual coverage.
9. Click **Analyze package →**. The processing card shows the actual stage and then **Review inspection →**.
10. Open the record. Compare the extracted text with its source image before deciding any finding.
11. Correct misread declarations, save reasoned finding decisions, or request a clearer image.
12. When unresolved findings are cleared, enter a review note and **Submit for approval**.
13. A different supervisor who did not create, correct or decide the record reviews it and approves.
14. Download the report. Open Repository and Dashboard to see the saved record and its current review status.

Wait for **Checking image quality…** to finish on each preview before submitting. Read the recapture guidance and expand **Measured image indicators** if useful. Quality warnings guide your judgment; they are not OCR accuracy scores or automatic proof that a declaration is missing.

For a guided example, open Repository and choose the **Sparkle Max** detergent record. It carries two measured height violations, a confirmed package category and a full decision history, so every stage of the record page has something in it to read.

For the approval and export stages, open the **Silk Shine** sachet record. It has confirmed package context, no unresolved findings and workflow state **Approved**. The seed's inspector account submitted it and a separate supervisor account approved it, which exercises the separation-of-duties rule; it is not review by two human officers.

For the opposite end of the workflow, open the **Awadh Pure** ghee record. It is **Draft** with nothing decided — what an inspection looks like the moment analysis finishes and before an officer has touched it.

For a genuinely unresolved case, open the **Deccan Spice** record. It is **Submitted** and waiting on a supervisor, which is where a record sits when the inspector has finished but approval has not happened.

These records are regenerated by `python scripts/seed_demo.py` through the real extraction, rules and review code. If the pipeline changes, they change with it; their identifiers change on every run, which is why this guide names them by brand.

## 4. Every capture control

| Control | How to use it and what happens |
|---|---|
| Choose package images | Select several images together, or add more afterward. Each becomes a preview card. |
| Drop package photos here | Drag files into the upload area. |
| Use camera | Opens the browser camera. Allow camera access, position the label and click **Take photo**. Close stops the camera stream. |
| Package panel | Choose Front / principal display, Back, Left, Right, Top, Bottom, or Close-up / other. |
| Rotate | Rotates the working view clockwise by 90 degrees. Rotation resets an earlier crop, so rotate first. |
| Crop / zoom | Opens the image editor. Drag a rectangle or enter **Left, Top, Right, Bottom** whole-pixel bounds, then **Apply crop**. Bounds refer to the rotated original image; the editor shows its dimensions. **Zoom** ranges from 100–300%; **Close** exits the editor. |
| Reset crop | Restores the uncropped working view while preserving rotation, then refreshes its quality check. The original selected file remains unchanged. |
| Measured image indicators | Expands pixel-based quality measurements and recapture guidance for that preview. After cropping it also shows the percentage of the original image retained. A clear result does not certify that every declaration is readable. |
| Retry image check | Retries a failed preview/quality request. This is separate from retrying a saved analysis job. |
| Remove | Removes that image from the pending upload; it does not delete an existing saved inspection. |
| Total image count | Counts newly selected images and any retained parent images together, up to 12. |
| Capture source | Chooses field, citizen, marketplace or premarket screening policy. |
| Inspection location / district | Optional typed location, used in repository filters and dashboard grouping. It is not verified GPS. |
| Record device location | Requests browser geolocation only after you press the button. On success, the form retains latitude and longitude rounded to six decimal places. No continuous tracking runs. Browser permission, secure/loopback origin and a location-capable device are required. |
| Clear recorded location | Removes the pending coordinates from the current form. It does not erase coordinates from an inspection that was already saved. |
| Allergens to highlight | Optional comma-separated concerns, such as milk, peanuts, soy or gluten. |
| Physical panel dimensions | Enter both measured PDP width and height in millimetres, from 1–5000 mm; the form uses a 0.1 mm step. This helps describe the panel; it does not by itself create a pixels-to-millimetres scale. |
| Every printed face included | Your coverage attestation. Unreadable or conflicting images can still prevent an absence finding. |
| Confirm package context for applicable rules | Expands category, package/bundle, origin and shape choices. These facts help determine which checks can safely apply. |
| Product category | Select General packaged commodity, Food, Alcohol, Tobacco, Pan masala, Medical device, Cosmetic, Seed, or Not confirmed. |
| Package / bundle | Select Single, Combination, Group, Multi-piece or Not confirmed. |
| Origin context | Select Imported commodity, Domestic commodity or Not confirmed. This is separate from an OCR origin suggestion. |
| Package shape | Select Rectangular, Cylindrical, Other or Not confirmed. Current physical-height decisions require supported, confirmed geometry. |
| I verified the selected context | Attests the selected package facts. Selecting a value alone is not an attestation. |
| Analyze package | Validates and preserves evidence, queues real OCR and saves the analysis. |
| Capture draft name | Optional name to recognize unfinished capture work later. |
| Save capture draft | Explicitly saves original image files, panel choices, rotation/crops and typed details under your account. |
| My saved drafts / Refresh saved drafts | Lists your saved, unexpired capture drafts and reloads their current revisions. |
| Resume in new tab | Restores saved originals and edits in another tab; the present tab keeps its unsaved selection. |
| Delete saved draft | Deletes the stored draft. Already loaded images and accepted inspection evidence remain available. |

The four capture sources share the image pipeline:

| Source | Current behavior | Beyond current coverage |
|---|---|---|
| Field inspection | Officer image workflow, review and approval. | Native Android capture, depth sensors, complete offline mobile operation. |
| Citizen submission | Advisory screening of uploaded photographs. | Public consumer accounts, complaint filing and grievance integration. |
| Marketplace image | Advisory screening of manually supplied listing images. | URL ingestion, crawlers, seller catalogues and listing-text comparison. |
| Pre-market raster label proof | Screening of image proofs. | Vector PDF/AI import, dielines and exact artwork measurements. |

Supported server inputs are JPEG, PNG, WebP and HEIC/HEIF with the decoder installed. Limits are **12 images per inspection, 25 MB per image, 25 megapixels, and at least 16 × 16 pixels**. If the browser cannot decode HEIC, the server supplies a reduced JPEG preview for editing while retaining the original selected file for analysis upload. If server decoding also fails, export a JPEG or PNG.

The originals are retained separately from normalized working images. Cropping changes what OCR sees. Retaining the original does not mean the uncropped areas were also analyzed. A tight crop should therefore be an additional close-up, alongside a full panel image.

Each preview now requests blur, glare, lighting, exposure and resolution checks before full analysis, and refreshes them after editing. Preview requests temporarily send the image to this application's server; those temporary files are removed after the response and do not create an inspection. Click **Analyze package** to retain the evidence and run OCR. These checks are heuristic guidance, not the reference's validated live on-device quality gate. Hardware camera capture has not been independently exercised in this QA session; the browser upload path has.

The diagnostics can also flag low contrast, uneven lighting, fragmented ink, possible perspective distortion and a nearly white frame that may be overexposed or unprinted. If a crop keeps less than 25% of the original, the card reports the percentage and asks for a full-panel companion image. It does not claim that text was clipped. The diagnostics cannot identify every occlusion or read every tiny declaration. Numeric crops must use whole-pixel bounds inside the rotated original and retain at least 16 pixels in both directions. Zoom changes display magnification only.

Device location is optional and independent of the typed district/location. Press **Record device location** only when location retention is appropriate for the inspection. The browser reports whether access was denied, timed out, unavailable or invalid; you can continue without it. The saved record retains the six-decimal coordinate, while Dashboard groups and displays it at 0.001° (roughly 100 m) to reduce unnecessary precision in aggregate views. A location can still be inaccurate because of the device, browser, indoor conditions or user settings. The current QA verified validation, storage and rendering with synthetic coordinates; it did not accept a real browser permission prompt or measure physical GPS accuracy.

### Processing and retry

The displayed stages cover receipt/validation, OCR and quality assessment, extraction, applicability, measurement, rules, and saving. The camera uses **Close camera** to stop capture; camera access requires browser permission and a supported secure or loopback origin.

Difficult or multiple images take longer on CPU. Open **Processing** to find accepted uploads across browser sessions. Return to **Inspect** in the same browser/profile and server address to restore the latest locally remembered job. A failed job can offer **Retry saved images**. A connection interruption is not evidence that the saved job was lost.

On **Processing**, the default **Unfinished and failed** view shows queued, processing and failed jobs. Use **Show jobs** to select one state, **Saved inspections** or **All jobs**, then **Apply filter**. The totals cover all jobs your account may access, even when the table is filtered. **Refresh jobs** updates the table; the displayed check time is UTC. Results have Previous/Next controls with 25 jobs per page.

Each row shows the submitting inspector, image count, capture source, location when recorded, submission time, state, attempt count and last processing stage. Choose **Open progress →** for live updates or to read a failure and use **Retry saved images**. A completed row offers **Review inspection →**. Inspectors see their own jobs; supervisors and administrators can see jobs across accounts. An unavailable or unauthorised saved-job link does not expose another inspector's upload.

The Processing page tracks uploads. Find inspections awaiting officer decisions or supervisor approval through **Repository → Review status**. There is no automatic assignment or risk ranking of cases.

Pending image selections and crop edits are **not automatically saved**. Use **Save capture draft** to keep unfinished work. Once Analyze is accepted, the server retains the inspection evidence independently; completed records remain in Repository. Restore remembered processing work using the same account as well as the same browser and server address.

Queued work resumes when the server starts, without requiring you to open a page. An interrupted running attempt becomes an explicit retryable failure once its worker lease expires; the saved images and last processing stage remain available. In-progress work has a limited shutdown grace period. Do not upload a duplicate simply because the browser disconnected; check the remembered job or Repository first.

This is a local durable job workflow, not an offline mobile sync service. Leaving a browser tab does not make processing run on a phone.

### Save and resume unfinished capture work

1. Add at least one image and finish its preview/quality check. Set panels, rotation/crops and any known details.
2. Optionally enter **Capture draft name**, then **Save capture draft**. Wait for the saved revision confirmation.
3. Return to **Inspect → My saved drafts** under the same account. Select **Refresh saved drafts** if another tab changed the list.
4. Select **Resume in new tab**. The server verifies and reloads the originals, restores edits, source, location, concerns, dimensions and package selections, and runs fresh preview checks.
5. Recheck package facts and complete coverage: their verification checkboxes are deliberately cleared when resuming.
6. Save again after further edits, or choose **Analyze package**. Analysis uses its usual validated upload/OCR workflow. The saved draft remains until you explicitly delete it or it expires.

Drafts are private to their owner, including against other supervisor/administrator accounts. Limits are **10 drafts, 100 MB of originals per draft and 300 MB per account**; the usual per-image and combined 12-image limits still apply. Drafts expire 30 days after the last successful save and are cleaned up on subsequent draft-list/save requests. A linked rescan draft also checks authorization and the retained parent evidence before resuming.

A saved capture draft, a processing job and an inspection in workflow state **Draft** are different: the first is unfinished input, the second is an accepted analysis upload, and the third is a saved analysis awaiting review. Capture drafts do not appear in Repository, Processing or inspection dashboard totals. Saving is an explicit server operation requiring a connection; it is not offline phone synchronization.

Deleting or expiring a draft prevents access and removes its unreferenced original files. Audit events and the deleted/expired metadata row remain for lifecycle tracking. Inspection evidence is protected separately.

If another tab saved a newer revision, the app refuses to overwrite it. Resume the latest saved version before saving again. A failed save keeps the images in the present tab; retry after resolving the displayed problem. If a saved original is missing or changed, resume is blocked. Delete only drafts you no longer need; deletion is not an inspection-deletion feature.

## 5. Read the inspection record

Start with the four summary cards:

| Card | Meaning |
|---|---|
| Potential machine violations | Issues proposed by the automatic screening. |
| Officer-verified violations | Findings an officer explicitly confirmed with a reason. |
| Findings awaiting resolution | Potential or uncertain findings still blocking submission. |
| Review workflow / revision | Current workflow state and the saved revision number. |

**Zero potential violations does not imply a compliant product.** The example record has zero potential machine violations and unresolved findings, so it remains Needs review.

Then work down the page:

| Area | What to look for |
|---|---|
| Record header | Accepted product name where available, reference, capture time, officer, rule version, OCR engine and evidence tier. An unresolved brand is not used as the heading. |
| Capture quality & OCR evidence | Image-specific quality guidance, failed passes and readings requiring verification. A warning can flag suspicious characters without an alternative reading. |
| Evidence images | Open a full image; compare printed characters and surrounding context. |
| Officer review & approval | Notes, rescan request, submission, approval and correction controls. |
| Label intelligence | Date/batch/ingredient observations, source text and uncertainty. |
| Inspection particulars | Capture and package facts carried into the report. |
| Declarations extracted | Recognized declaration text, panel and script. Expand **View field evidence and confidence** for source locations, extraction method and score limitations. |
| Applicability and exemptions | Why the draft engine considered duties applicable or exempt. These classifications need review. |
| Findings | Open each finding to read reasoning, citation, available measurement, source highlight and officer decision. |
| Measurement annexe | Millimetres, uncertainty, method and scale evidence where available. |
| Adjudication trail | Who reviewed, submitted or approved, with recorded times and reasons. |
| Evidence integrity | Whether current evidence files match their retained hashes. |
| Notes and limitations | Issues encountered during processing. |
| Product history | Appears when matching GTIN records exist. |

The product heading uses an accepted brand, otherwise a readable generic name or **Package inspection**. An implicit brand requires readable product/commodity support in the **same image**; an explicit printed cue such as `Brand:` can also support it. Text from another package face does not supply this support. An unsupported name remains visible with its source and confidence as a review candidate rather than becoming the accepted identity.

This safeguard can hold a correctly read logo for review when the image contains only the logo or its product wording is uncertain. Check the original and use **Correct a declaration → Brand** with a source rectangle and reason when you can verify it. Product-context support is a heuristic, not proof of brand identity or a registry check.

### Finding outcomes

| Outcome | Interpretation |
|---|---|
| PASS | This particular implemented check passed on available evidence. |
| VIOLATION | Machine screening suggests a problem; it still needs officer verification. |
| ADVISORY | A possible issue in an advisory capture lane. |
| INCONCLUSIVE | Reading, coverage, measurement or applicability is insufficient. |
| UNVERIFIED | A required fact or identifier could not be verified. |
| EXEMPT | The draft exemption logic matched; confirm the exception and its scope. |
| NOT_APPLICABLE | The rule is outside the evaluated scope or conditions. |

Unresolved finding headings now say that evidence is insufficient or that verification is required. Read the outcome, evidence and explanation together. This wording also appears in PDF and editable reports; the original stored machine message remains in the JSON record and is not rewritten.

### Workflow states versus approved result

A record starts **Draft**, can move into **In review**, then **Submitted**, and finally **Approved**. These are workflow states.

**Compliant / Non-compliant / Needs review** describe the application’s reviewed result. Compliant and non-compliant outcomes require approval. Approval covers the requirements examined; it does not fill gaps in the rule pack.

**Exempt** and **Not applicable** are also possible reviewed results. They are excluded from the approved compliance-rate denominator. A submitted record can have zero unresolved findings and still display Needs review while it awaits its separate approver.

Severity labels—minor, major and critical—prioritize findings in this application. They are not penalty amounts.

## 6. Correct, review, rescan and approve

### Correct an OCR mistake

1. Find **Correct a declaration**.
2. Choose the declaration and its **Source image**. Wait for the loaded source dimensions to appear.
3. Enter the **Verified label text** exactly as observed.
4. Drag around the complete printed declaration. Alternatively enter **Left, Top, Right and Bottom** whole-pixel bounds in the loaded source image.
5. Enter a **Correction reason**.
6. Click **Save correction & recheck**.

The rectangle must have positive width and height and fit inside the displayed source dimensions: Right must exceed Left, and Bottom must exceed Top. Blank, fractional, reversed or out-of-image bounds prevent saving. A cancelled drag restores the previous selection. The image area can also receive keyboard focus for scrolling; the coordinate inputs provide the keyboard selection method.

Changing the source image clears the previous selection while the new image loads. If loading fails, use **Retry source image**. If the session has expired, sign in and reopen the record before correcting it. A correction cannot be submitted using an unloaded image or a stale rectangle from another image.

The original OCR remains in revision history. The correction records the before/after values, source location, officer and reason. Rules rerun against the corrected declaration using the inspection's stored pack version.

**View field evidence and confidence** distinguishes machine readings from officer-verified transcription. New machine records retain contributing text locations across lines and images. A visible cue such as “MRP” without a readable value remains unresolved. OCR model scores and heuristic extraction scores are separate, and neither is a measured probability of correctness. For a corrected declaration, no automated score is assigned to the human-entered text; the first OCR transcription and its original score remain separately identified across repeated corrections. Older records may have incomplete provenance and are labelled accordingly; opening them does not rewrite their history.

**A correction clears earlier finding decisions and approval/submission state.** Review the new results before submitting again. A changed text box also cannot silently inherit an old typography measurement.

This editor covers nine primary declaration classes: manufacturer/packer/importer, generic name, net quantity, packing date, retail price, consumer care, unit price, country of origin and brand. Supplementary date, batch and ingredient observations have a separate editor described in section 7.

### Confirm or correct the package facts

Under **Package facts & applicable rules**, check the assessment date and stored rule version. Select Product category, Package / bundle, Origin context and Package shape. Confirm your verification, enter a reason, then use **Save package facts & recheck**.

This creates a new revision and reruns applicability using the original pack version. It clears earlier finding decisions and submission/approval state. It preserves retained image evidence and the inspection's assessment date. An unknown category, bundle or unsupported shape can leave **Manual legal review required**. Do not select a convenient category merely to remove a finding.

### Record a finding decision

Expand a finding and choose:

| Choice | Use when |
|---|---|
| Needs another scan / review | Evidence or legal interpretation remains unresolved. |
| Verify violation | You checked the source and applicable rule and confirm the issue. |
| Requirement satisfied / reject flag | The evidence satisfies the requirement or the machine flag is incorrect. |
| Rule does not apply | You can substantiate why the rule is outside this inspection's scope. |

Enter **Reason & observed evidence**, then **Save finding decision**. The record preserves the machine result as well as your decision.

### Comment or request a rescan

Enter a Review note, then:

- **Add comment** records a note.
- **Request rescan** records that clearer evidence is needed.
- **Capture a targeted close-up →** opens Inspect with the existing record as its parent.

A targeted rescan creates a **new linked inspection** with the previous evidence and the additional images. It does not overwrite the old inspection. The combined set is still subject to the 12-image limit. Requesting a rescan is currently a stored workflow action; it does not send someone a message.

Add at least **one new image** to create a linked rescan. Retained parent evidence must still pass integrity checks. You can record a request for a rescan when evidence is damaged, but creating the linked inspection remains blocked until the matching originals are restored.

To capture one particular declaration:

1. Expand its **Finding**, then choose **Capture a close-up of…**. Date, batch and ingredient cards also have their own close-up links, including on approved records.
2. Check **Original inspection**, its revision and **Focus of this close-up** on Inspect. You can change the focus to another listed declaration or **Other marking / additional package face**.
3. Photograph the complete marking, including the MRP/currency cue, date qualifier or batch label and the value. Keep some surrounding print and move reflected light away from the text.
4. Add the new photograph, review its panel and crop, and reconfirm package facts and coverage only when verified. **Save capture draft** also retains the selected focus and original revision. Resuming still clears the attestations.
5. Choose **Analyze package**. The new record contains the earlier images and your close-up. It uses the original record's archived rule version and legal assessment-date basis, with a new capture timestamp.
6. Open **Linked capture history** on the result. It links to the original inspection and the exact retained revision, confirms whether its reference hash matches, and lists earlier officer corrections for comparison. Those corrections, finding decisions and approval are not automatically applied to the new analysis.
7. Review the combined evidence and record fresh corrections and decisions where needed. The original inspection lists its linked rescans; the PDF, editable report and JSON retain the parent reference, revision and hash.

If the original revision changed before you submitted or resumed a draft, the app refuses the stale request. Reopen the original inspection and start a fresh linked capture after reviewing the changes. Once a job has been accepted, later legitimate changes to the parent do not replace the revision captured by that job. If retained images or capture metadata change, processing/retry is blocked until the matching evidence is restored.

When photographs disagree, the analysis keeps their source readings and requests verification. For example, **5 g** in one image and **500 g** in another cannot silently establish a small-package exemption. This also applies when OCR joins repeated recognized explicit quantity/date cues into one text region; arbitrary numbers after a single cue are not fully covered. Equivalent readings such as **500 g** and **0.5 kg** can agree. Supplementary summaries retain conflicts too. Source text windows keep the original whole-region rectangle; they do not geometrically locate each substring. Manufacturing and packing dates remain distinct events; an uncertain event cannot establish a date-dependent exemption. Compare all sources before making an audited correction.

A clearer new image does not automatically replace conflicting retained readings; an audited correction may still be necessary. Check that all uploaded images show the same physical package. The application assumes this relationship and does not independently verify it.

The form prefills the parent's capture source, location, allergen concerns and category/bundle/origin/shape selections. Review them for this rescan. Package-fact verification and whole-package completeness are deliberately unchecked and require fresh confirmation. Physical panel dimensions are not automatically carried into the form; re-enter measured dimensions where appropriate.

The count includes retained and newly selected images. You can remove a newly selected image, but this form does not delete the parent's retained evidence. The 12-image limit applies to their combined total.

New rescan images default to **Close-up / other**. Verify or change their panel assignments. Only a parent record authorised for your account supplies retained evidence and prefilled details.

### Submit and approve

Submission requires actual findings and no unresolved potential/uncertain findings. Enter a meaningful review note and click **Submit for approval**.

A separate supervisor or administrator opens the submitted record, reviews it, enters an approval reason and clicks **Approve inspection**. The approver must not be its creator, submitter or material contributor, including declaration/supplementary corrections, finding decisions and package-context attestations. The check includes retained revisions: replacing an earlier decision does not make its author independent. Retained history and image evidence must pass integrity checks.

Inspectors can edit assigned records. Other records remain readable, with mutation controls restricted; supervisors and administrators have broader review permissions. An approved record must be reopened before edits. Missing or altered evidence prevents corrections, decisions, submission and approval; comments, rescan requests and authorised reopening remain available to document and address the problem.

Ordinary comments, rescan requests and reopening alone do not disqualify an otherwise independent approver. Material corrections, decisions and package-fact attestations do.

Approved records require a supervisor to **Reopen for correction** before editing. Old revisions remain accessible under **Immutable revision history**; each revision link returns its saved JSON.

### Saving changes and recovering from a failure

Save one completed form at a time. **Saving review…** temporarily locks all review forms on the record, including correction selections, to prevent overlapping changes to the same revision. A confirmed save automatically reloads the current record and returns to the relevant section, even when you were already at that section. You do not need to refresh after each successful save. Complete or copy unsaved text in other forms before saving, because a successful reload replaces the page.

If saving fails, the entered values stay in the current tab and an inline message explains the problem. Use **Open latest record in a new tab** to compare its revision and recorded action before retrying. A timeout, interrupted connection or server error means confirmation was unavailable; the server may already have saved the action. Do not submit it again without checking.

If another person changed the record, a revision conflict prevents overwriting their work. Keep your unsaved text, open the latest record, review the changes and copy only the still-needed entries into that record. If your session expired, use **Sign in and check the record in a new tab**, then copy any unsaved entries into the newly signed-in record and save there. The old tab retains the previous session token. For a rate-limit message, wait before retrying.

## 7. Product, dates, ingredients and allergens

**Label intelligence** supplements the main compliance findings.

| Feature | What it provides | How to use it carefully |
|---|---|---|
| Product context | Suggested category, food/non-food and origin signals, with supporting text. | Confirm against the package; a suggestion is not a registry lookup. |
| Dates | Manufacturing/packing, expiry/use-by and best-before observations where read. | Check ambiguous day/month interpretations and the actual printed cue. |
| Relative shelf life | Statements such as a number of months from manufacture. | The system does not invent an absolute expiry from an unresolved reference date. |
| Printed date status | Compares a detected expiry, best-before or use-by observation with the processing-time UTC date shown as **as of**. Conflicting readings have no such status. | This is separate from the legal assessment date. It describes label text; it does not establish food safety. |
| Batch and lot markings | Extracted codes with source evidence and alternative OCR readings, including supported joined cue/code forms such as `BATCHAB1234`. | Inspect dotted digits and similar-looking characters. Ambiguous cue boundaries or unsupported code formats can still require manual correction. |
| Price candidates requiring review | A possible amount and nearby visual currency context when reliable MRP extraction was not possible. | An INR hypothesis is explicitly unverified. Inspect both the digits and currency mark; use **Correct a declaration → Retail sale price** to record verified price text. A supplementary candidate does not enter MRP counts or unit-price arithmetic. |
| Ingredients | Located printed ingredient text. | OCR may miss small print or another panel. |
| Allergen concerns | Explicit, possible related-ingredient and cross-contact text matches. | Read the source statement and match type. |
| Dietary claims | Printed claims supported by extracted text. | The app does not certify the claim or validate every visual symbol. |
| Additives | Printed additive identifiers. | It does not infer an ingredient identity or safety conclusion from a code alone. |

To change concerns after analysis, find **Ingredient and allergen review**, enter comma-separated terms and click **Check ingredient statements**. Standard concern names receive the supported matching behavior; custom ingredient names are matched literally.

“No matching text” means no match in the statements that were read. It does not prove absence of an allergen. These features are informational and are not a complete FSSAI compliance or medical assessment.

Each observation can expose **Source and extraction details**: source image, highlighted rectangle, printed text, extraction method, OCR confidence and alternative readings. Confidence values and heuristic extraction scores are not calibrated probabilities of truth.

An absent extraction score does not mean an officer entered the observation. The source details distinguish automated candidates from **Inspector correction**. For price candidates, the amount's OCR score does not verify the currency symbol. Uncertain brand or commodity text can remain visible as a candidate without being accepted as the product's normalized identity.

### Correct supplementary text

1. Use **Correct this observation**, or expand **Correct or add a supplementary observation** if OCR missed a field.
2. Choose Manufacturing date, Packing date, Expiry date, Best before, Use by, Batch / lot number, Ingredients, or Importer.
3. Select **Source image**, wait for its source dimensions, and enter **Verified printed text**, including its cue such as `EXP` or `Ingredients:`.
4. Select the complete marking by dragging, or enter **Left, Top, Right and Bottom** whole-pixel bounds inside the loaded image. The rectangle must have positive width and height.
5. For a date, leave **Explicit date interpretation** blank to preserve ambiguity. Enter a supported candidate only when evidence resolves its meaning; explain that evidence in **Correction reason**.
6. Click **Save supplementary correction**. Use **Add another observation** when recording a separate marking.

If the source image fails to load, use **Retry source image**; unresolved image loading or invalid bounds prevent saving. Changing the image clears the old selection. These saves use the same **Saving review…**, automatic record reload and failure-recovery behavior described in section 6. The supplementary price candidate has no direct correction button here: verify it through **Correct a declaration → Retail sale price**.

The original transcription, corrected text, evidence, officer and reason remain in revision history. Ingredient edits refresh allergen matches and printed additive identifiers. A submitted record returns to review. These edits do not silently change primary statutory declarations or rule findings: if the primary packing-date or manufacturer/importer declaration is also wrong, correct it through **Correct a declaration**. A supervisor must reopen an approved record before editing.

## 8. Rules and physical measurements

Open **Rules** to see the draft pack version, rule title/ID, displayed citation, date range, minimum evidence tier and severity. The live page is read-only.

Expand **Official sources and review scope** to follow the source notification links. Signed-in users can also retrieve the current pack at `/v1/rules/current`.

The current pack has **18 definitions**, including withdrawn checks:

| Definition | Current coverage / limitation |
|---|---|
| Manufacturer / packer / importer | Presence and parsed name/address; no official registration verification. |
| Common/generic commodity name | Extracts a commodity description distinct from branding; vocabulary and layout errors remain possible. |
| Net quantity present | Screens a printed declared quantity; does not weigh or measure the contents. |
| Quantity unit symbol | Normalizes quantities and checks supported standard notation. |
| Packing month/year | Reads supported date cues; food and other special regimes can require legal review instead of the general package check. Historical coverage remains bounded. |
| MRP present | Requires sufficient readable coverage before treating absence as adverse. |
| Single MRP | Multiple values trigger legal review because revised-price situations can be lawful. The app does not prove physical over-stickering or tampering. |
| Tax wording | Screens inclusive-of-tax phrasing in recognized price text. |
| Consumer care | Source-linked dated requirements examine name, address, phone and email. Missing OCR components remain uncertain; the app does not test external reachability. |
| Unit-price presence | Dated applicability and confirmed package/bundle facts gate the requirement. Known exceptions are retained; unknown facts require review. |
| Unit-price arithmetic | Checks the supported prescribed quantity basis and rounded decimal value. Ambiguous exact quantity boundaries and unconfirmed applicability require legal review. |
| Country of origin | Confirmed import facts determine relevance; unknown or conflicting context remains subject to review. |
| Declaration grouping | Retains manual review; a complete spatial compliance engine is not implemented. |
| General declaration height | Uses source-linked area bands with calibrated measurements and confirmed supported geometry. Food/device referrals and unsupported contexts require legal review. |
| Net-quantity numeral height | Measures selected glyphs, with measurement-tier and uncertainty gates. |
| Bottom placement | The unsupported blanket prohibition is withdrawn. |
| Net-quantity script | Limited Latin/Devanagari screen; does not require both or validate every declaration's language. |
| MRP rounding | The blanket check is withdrawn. Historical rounding provisions existed and still need dated treatment. |

Some IDs retain older clause numbers for compatibility. Read the displayed citation and source matrix; an ID is not itself a verified legal citation. Some applicability/exemption logic lives in Python. The Rules page displays source/review details, and its requirement descriptions are screening paraphrases, not certified quotations of the statute.

Additional GTIN checks include barcode decoding, check digits and prefix observations. **A barcode prefix does not prove manufacturing country**, and a valid checksum does not prove authenticity. Official GS1/packer/FSSAI registry verification is not integrated.

### Millimetres, uncertainty and tiers

A photograph contains pixels. Physical type size requires a justified scale in that specific image. Entering width and height alone is not sufficient.

To prepare the supported scale card, run `python scripts/make_scale_card.py --out out/scale-card.pdf` from the project, open the resulting PDF and print at **100% / actual size**. Physically check that each marker is 25 mm; printer scaling can invalidate it. Photograph the card beside the declaration, both visible and in the same plane, with the camera as parallel as practical. Upload that photograph normally: marker detection is automatic. There is no separate calibration-upload or calibration-settings control. Enter independently measured panel dimensions where relevant. A detected marker still does not prove correct printing, coplanarity or measurement accuracy; those require physical verification. See [the run guide](C:/Users/offic/OneDrive/Desktop/sih/README.md).

| Tier | Meaning in the design | Current acquisition coverage |
|---|---|---|
| A | Metrics from supported vector artwork. | No vector artwork intake in the current app. |
| B | Supported calibration, such as an ArUco scale card. | Card-based processing exists; device-depth examples can also be supplied by test/trusted metadata. No live phone-depth capture is integrated. |
| C | Weak/estimated or unavailable physical calibration. | Cannot satisfy height rules requiring Tier B. |

Example: a measured height of 1.40 ± 0.20 mm has an interval from 1.20 to 1.60 mm. If a validated applicable minimum were 2.50 mm, the entire interval is below it. A 2.50 ± 0.20 mm interval overlaps that minimum and remains inconclusive.

These numbers explain decision logic; they do not prescribe a threshold for every package. Real-package measurement error has not been validated against a full independent physical calibration set.

Evidence tier, capture completeness and capture lane answer different questions. An uncalibrated photo can still support a readable text-presence finding in a field lane; it cannot support a physical height finding that needs calibration. A full-resolution photo of one face cannot establish what is absent from every other face.

No automated conviction or notice service occurs. The rule, measurement and review views explain the evidence available to the officer.

## 9. Reports and retained evidence

At the top of a saved inspection:

| Download | Contents / purpose |
|---|---|
| Report (PDF) | Readable report, evidence crops, rule findings, review state, decisions, corrections, measurements and attached analysis JSON. |
| Editable report | DOCX version for subsequent editing. Changes in Word do not update the stored inspection. |
| Draft notice | Editable working document using officer-verified violations; unresolved or rejected machine flags are excluded. It is not automatically served or signed. |
| JSON record | Structured analysis and provenance for inspection or integration. |

Exports include available supplementary label/date/ingredient/allergen observations and recorded officer notes. The report separates machine findings from officer decisions and shows whether supervisor approval was recorded.

Downloads reflect the current saved revision and include an **Original image appendix** alongside highlighted crops. Report illustrations can be resized/oriented for reading; the retained hashes identify the source files. If evidence verification fails, PDF/DOCX/notice export is blocked, while **JSON record** remains available for investigation.

Each generated PDF, report DOCX and notice DOCX is retained as a distinct export with its inspection revision, file hash and an audit event identifying the requesting account. Repeated downloads create separate retained exports. Evidence is checked before and after rendering. A failed export does not replace an earlier report.

The report preserves the typed district/location and any recorded GPS coordinates. Imported status remains **Not confirmed** until the officer explicitly confirms imported or domestic context; a machine suggestion alone cannot become a confirmed “no.” Confirmed product category appears under applicability, separately from text-based category and origin suggestions. Product suggestions, printed package references, dietary claims and additive identifiers include their source text, image/rectangle and available confidence or uncertainty, with highlighted evidence crops. These observations do not certify contents, claims or safety.

The PDF has an embedded JSON attachment, but **PDF/A-3 conformance has not been established**. Reports do not currently contain a digital signature, trusted timestamp or complete capture-time custody certification.

Expanded field evidence can make reports longer. Findings and image-appendix headings now stay with their first content block. Representative 14-page and six-page reports were rendered and all 20 pages inspected; wider Word/LibreOffice compatibility and archival-format validation remain separate acceptance work.

The application preserves original uploads and working images with hashes, and checks evidence integrity before relevant access/export/approval operations. If files are missing or changed, access can be blocked. The [operations guide](C:/Users/offic/OneDrive/Desktop/sih/docs/OPERATIONS.md) explains how an administrator backs up and verifies the database, evidence, retained rule packs and reports together.

Draft reports can be downloaded before approval, with their review state visible. The wider reference requirement for a legally authorised, served notice remains unfinished.

## 10. Repository and product history

Open **Repository**. Type an inspection reference, product text, brand, manufacturer, GTIN or an explicit month and year such as “August 2026”. Click **Search**.

| Filter | Use |
|---|---|
| Dataset | All records, Operational inspections or Test-bench runs. |
| Approved result | Compliant, Non-compliant, Needs review, Exempt or Not applicable. |
| Review status | Draft, In review, Submitted or Approved. |
| Category | General packaged commodity, Food, Alcohol, Tobacco, Pan masala, Medical device, Cosmetic, Seed, legacy Personal care/Household/Industrial categories, and Unknown / not confirmed. Stored custom categories also remain searchable. |
| From / To | Inclusive capture-date range in UTC; From must not be later than To. |
| Inspector | Partial match on recorded inspector name. |
| Manufacturer | Partial match on extracted manufacturer text. |
| Rule ID | Exact rule identity from a finding or the Rules page. |
| Finding outcome | Filter individual findings. **Flagged · violation or advisory** selects either of those outcomes. If you also enter a Rule ID, both conditions must match the same finding. |
| Machine outcome | Aggregate inspection-level machine result, separate from approval; it is not a filter on one individual finding's result. |
| Location | Exact match on recorded location/district. |

Expand **Date, inspector, manufacturer & rule filters** to access the additional fields. **Clear all filters** returns to the full repository. Results show a total, page number and Previous/Next controls, normally 25 records per page.

Click a product/reference to reopen it. Inspection history includes immutable review revisions. Records with the same valid GTIN can also show a quantity/price history.

Category labels prefer officer-confirmed package facts; otherwise they use the OCR suggestion or Unknown. Rows show **Confirmed**, **Suggested** or **Not confirmed** so you can distinguish these sources. The dropdown includes the supported confirmed categories and retained older/custom labels.

The product history on an inspection page stays within that record's operational or Bench dataset. It shows machine outcome, review state and reviewed product result separately, with quantity units. Matching a barcode is a local comparison key, not verified product identity.

The shrinkflation observation compares successive captured records with compatible quantity units. It can highlight reduced quantity, including a reduction at unchanged MRP. It is a signal to investigate, not a determination of illegality. Old stock, OCR mistakes and shared/misread GTINs can distort the comparison.

Free-text citation search includes the full stored citation. “Rule 7” matches a cited rule, rather than an unrelated GTIN digit or Rule 70. Percent and underscore characters are treated literally. An exact **Rule ID** filter is useful when you want one particular check.

Search covers indexed product/inspection metadata and finding rule/citation/message/outcome text. It is not a full search across every OCR line, ingredient observation, review comment or exported report.

Use an explicit month/year phrase by itself, such as `August 2026`; combine other criteria through the dedicated filters. There is no separate **Catalog** page: product access is through Repository and an inspection's Product history.

There is no current bulk-delete, bulk-export or full manufacturer entity-resolution interface.

## 11. Dashboard

Open **Dashboard**, choose **Operational inspections** or **Test-bench runs**, optionally set dates, then **Apply filters**.

| Display | Meaning |
|---|---|
| Total inspections | Stored inspection records in the selected dataset/range, with recent counts. |
| Approved compliant | Supervisor-approved records with no verified violation in the checked scope. |
| Approved non-compliant | Approved records with at least one officer-verified violation. |
| Needs review | Records still requiring evidence, review or approval. |
| Approved compliance rate | Uses approved compliant/non-compliant records only. With none approved, it is unavailable. |
| Approved compliance by capture date | Current approved outcomes grouped by UTC capture date. Click a chart point or table date to open that day's records; expand **View compliance counts and rate denominator** to inspect the counts. |
| Inspection activity over time | Counts across up to 30 recorded activity dates; an expandable data table is available. |
| Declarations flagged most often | Potential machine findings grouped by rule. Related checks may count separately. |
| Product categories | Confirmed package categories first, otherwise OCR suggestions/Unknown. **Category basis** shows how many records come from each source. |
| Potential finding severity | Severity of machine flags; not a fine or an officer workload score. |
| Inspector activity | Saved work grouped by inspector. |
| Manufacturer activity | Stored inspections and potential findings by extracted manufacturer. |
| Recorded locations | Grouping by the optional typed district/location. It remains separate from device coordinates. |
| Recorded inspection coordinates | An offline SVG coordinate plot for consented saved coordinates, rounded to 0.001°. Point size shows inspection count; orange means at least one machine-potential finding and green means none. Select a point or the matching location in the accessible table to open those inspections in Repository with dataset/date/coordinate filters preserved. Coordinates outside the India plotting extent remain in the table. This is an operational plot, not an official boundary map or market-prevalence heat map. |
| Recent inspections | Direct links back to saved records. |

Bench examples are separated from operational counts. These statistics describe this local database; they do not establish national market prevalence or a deduplicated list of offending companies.

Totals count inspection records, not distinct physical products. A linked rescan adds a separate record; saved revisions of that record do not add inspections to the total.

The compliance chart excludes pending, exempt and not-applicable records from its rate. A date with no approved compliant/non-compliant records has **Not available**, not 0%. Previous points can change when an inspection is approved or reopened: this is current review status grouped by capture date, not a frozen history of past approvals. Date filters and recent-activity counts use UTC, so a capture shortly after midnight in India may appear under the previous UTC date. Future-dated records do not inflate the recent week/month counters.

Clicking a rule under **Declarations flagged most often** preserves the dataset/date range and opens records where that same rule has a **Violation** or **Advisory** finding. Passing findings alone do not match this link. These are potential machine flags; approval and officer decisions are separate filters and columns.

The plot shows at most the 200 most frequent rounded coordinate groups while its summary count still includes every matching inspection. Dataset and date filters apply to it. A coordinate outside the fixed 6–38°N, 68–98°E India plotting extent remains in the accessible table and is called out below the plot. Repository also exposes paired **Coordinate latitude/longitude** filters; both values are required and select the same 0.001° group. Risk-ranked worklists, route planning, validated boundary/heat maps, cross-state offender graphs and automatic trend alerts remain future work.

### Inspection coverage by district

**Inspection coverage by district** ranks the typed inspection locations for the selected dataset and date range. Bar length is the number of inspections recorded there; an amber bar marks a district where at least one potential machine finding was raised, and the chip beside the count is how many. Selecting a district opens Repository filtered to exactly those records, preserving the dataset and date scope. An accessible table below the chart carries the same figures.

This counts inspections this office recorded, not market prevalence: a district with more inspections is a district that was visited more often. It uses the typed location field, which is independent of the optional recorded coordinates below it — an inspection can have either, both or neither.

Tula deliberately does **not** draw an administrative boundary map of India. Depicting national or state boundaries in an official context requires an authorised base map, and an approximate outline taken from a general-purpose dataset would be both wrong and inappropriate for a government product. The coordinate plot is therefore labelled as an operational coordinate frame, and district aggregation is shown as a ranked chart rather than a shaded map. An approved boundary layer remains future work.

## 12. Bench and all 20 scenarios

Use **Bench** to explain or debug one behavior with known generated geometry.

1. Click a preset; it fills the form.
2. Select the recognition engine.
3. Click **Render and analyse**.
4. Compare generated evidence, findings and measured-versus-drawn geometry.
5. Click **Full record** to inspect the saved result and exports.

**fixture** uses supplied reference text, so it isolates rule/extraction/measurement behavior. **rapidocr** reads actual image pixels. Recheck the engine after loading a preset. A fixture result is not an OCR accuracy demonstration.

### Every Bench input

| Controls | What they vary |
|---|---|
| Brand; Generic name | Branding versus commodity description. |
| Net quantity value; Unit symbol; Cap height | Quantity parsing, notation and printed numeral size. |
| MRP; Second MRP | Price presence and multiple-price cases. |
| Unit price; Unit price basis | Arithmetic and quantity-dimension cases. |
| Panel width; Panel height | Known generated panel geometry. |
| Packing date | Printed manufacturing/packing date on the generated label. |
| Product category; Assessment date; Imported commodity | Known generated package context, including the date on which the scenario is assessed. This is distinct from its printed packing date. |
| GTIN; Country of origin | Identifier/check-digit and origin review signals. |
| Consumer care | Contact extraction. |
| Scale source | Synthetic/trusted calibration metadata for the experiment. |
| Recognition engine | Reference-text fixture or real pixel OCR. |
| Lane | Field/premarket or advisory policy. |
| Inclusive of all taxes checkbox | Adds/removes that phrase. |
| Devanagari net quantity checkbox | Adds/removes the Hindi-script quantity. |
| Whole package captured | Controls completeness evidence in the scenario. |
| Unreadable frame | Produces blank evidence to exercise refusal to infer absence. |

### Preset catalogue

| Preset | Intended demonstration |
|---|---|
| compliant | Reference label. Grouping or other review gates can still leave it inconclusive overall. |
| undersize_numerals | Clearly undersized net-quantity glyphs. |
| boundary_straddle | Measurement interval overlaps the draft threshold. |
| citizen_tier_c | Weak scale limits height decisions. Its legacy name is misleading: the preset uses a field lane. |
| large_panel | Larger panel selects a different area-dependent height band. |
| non_standard_unit | Quantity is parseable but the printed unit notation is noncanonical. |
| missing_tax_clause | Tax wording is absent. |
| dual_mrp | Multiple price values require review, including whether a lawful revised-price situation exists. |
| unrounded_mrp | No current blanket rounding offence is emitted. |
| wrong_unit_price | Arithmetic disagrees with the declared quantity/basis. |
| english_only | The limited net-quantity script screen accepts Latin. |
| gtin_origin_conflict | Barcode-prefix concern requires review; it cannot establish manufacturing origin. |
| exempt_sachet | A generated cosmetic sachet with known context exercises small-pack exemption behavior. A small package is not automatically exempt in every category. |
| pre_2022_packing | An explicitly historical assessment exercises the earlier applicability period. A packing date alone does not establish the sale/assessment date. |
| missing_mrp_complete | Missing MRP with complete/readable coverage. |
| partial_capture | Missing MRP with incomplete coverage stays unresolved. |
| unreadable_capture | Blank evidence cannot establish missing declarations. |
| citizen_advisory | A suspected issue receives advisory treatment. |
| marketing_copy_generic | Marketing text alone is not a reliable commodity name. |
| truncated_gtin | Invalid/incomplete identifier remains unverified. |

**Scenarios** runs this catalogue as a table with expectations and measurement checks. Opening the page starts and saves Bench runs; real OCR fills gradually. This is a software test matrix, not a legal validation certificate.

The generator uses a Hindi-capable font and shaping support when Devanagari is selected. If the machine lacks either, it reports the missing requirement; deselect Devanagari to generate an explicitly Latin-only label. Synthetic scenarios cover selected software boundaries, not the full legal or real-label accuracy problem. Future legal corrections must be reflected in their expectations.

## 13. Administration and technical access

Administrators can open **Administration → People & access**:

1. Create an account with username, display name, role and initial password.
2. Change a user's display name, role or active status using **Save access**. You may change your own display name; you cannot change your own role or deactivate yourself.
3. Reset another user's password using **Reset & sign out sessions**.
4. Open **Audit log**, optionally filter by inspection/entity ID, and expand **View change**.

Passwords require at least 12 characters. Role, activation and password changes revoke affected sessions. The application prevents self-demotion/deactivation and retains an active administrator.

Usernames are fixed after creation and are matched without case sensitivity. Use 3–80 ASCII letters, numbers or `. _ @ + -`, starting with a letter or number.

Audit entries record actors, actions, entities, times and available before/after values. They are append-only through application/database protections; they are not an externally notarized or administrator-proof ledger.

On **Audit log**, enter **Inspection or entity ID**, click **Filter events**, and expand **View change**. Times are UTC and Previous/Next controls page through the results.

### Manage rule versions

Open **Rule versions** using an administrator account:

1. Read **Currently selected** to identify the pack used for new inspections. **Download selected rule pack** retrieves its retained JSON.
2. Under **Retained versions**, expand **Source and review notes** to inspect provenance, notes and the content hash. **Download JSON** retrieves that version.
3. To prepare a change, give the JSON a unique version identifier. Under **Import a draft version**, either use **Upload JSON file (up to 512 KB)** or paste the complete pack, and enter **Source review and purpose of this draft**.
4. Click **Validate and retain draft**. Schema/expression validation must pass. Import alone does not select the draft.
5. Review the retained version. Enter **Activation or rollback reason**, then **Select this version** to use it for subsequent inspections.
6. To roll back, select an earlier retained version with a reason. Queued jobs and existing inspection records retain their original pack. Activation revalidates the selected pack, so an old invalid pack can be refused. If another administrator changed the active version while your page was open, reload before trying again.

The selected version is restored after restart. Changes and reasons are audited; altered content cannot reuse an existing version identifier. This is a JSON import/selection interface. A visual rule editor, side-by-side diff and test-impact preview remain unfinished. Activation is an operational administrator decision, not legal certification.

For installation and first-administrator provisioning, use [README.md](C:/Users/offic/OneDrive/Desktop/sih/README.md) and [SECURITY.md](C:/Users/offic/OneDrive/Desktop/sih/docs/SECURITY.md). There is currently no email password recovery, MFA or SSO.

### Back up and restore the workspace

There is no backup button in the browser. An administrator uses the installed command-line tool documented in [OPERATIONS.md](C:/Users/offic/OneDrive/Desktop/sih/docs/OPERATIONS.md): finish jobs, stop all processes using the runtime, create an archive outside that runtime, and independently verify it. Restore requires an absent destination at the **original absolute runtime path**; existing evidence paths are not rewritten. Keep the application release and model/dependency environment separately. Read the full procedure before maintenance; copying only the database would omit the image and report evidence.

The latest source additionally checks active saved-draft original files against their database hashes, sizes, paths and edit metadata during backup/verification/restore. Deleted or already expired drafts do not require removed originals; archive creation time determines expiry for verification. Focused tests passed, but a fresh installed release containing these checks has not yet been accepted. Use the version-specific operations notes when planning maintenance.

Technical clients have authenticated endpoints for queued inspection creation, job status/retry, saved review/revisions, analytics, rules and GTIN history. Sessions and mutation protection are required. These are not the entire API surface proposed in the artifact. The authenticated **/docs** page documents the current API without requiring an external documentation CDN.

Open [API reference](http://127.0.0.1:8000/docs) directly; it is not in the main navigation. Use **Find an endpoint** and expand the method/path you need. **Download the complete OpenAPI schema** links to `/openapi.json`; **Inspect your session and CSRF token** links to `/v1/session`. This is a searchable reference, without an interactive request console. `/redoc` redirects here.

For an API client, retain the same authenticated session cookies. Read `/v1/session` and send its CSRF token as `X-CSRF-Token` on JSON or multipart mutations. URL-encoded forms can provide `csrf_token`. A copied mutation request without the session or token is rejected; do not publish either credential in logs or examples.

| Technical route | Purpose |
|---|---|
| `POST /v1/capture/preview` | Check one image's quality and receive an editing preview; temporary files are discarded. |
| `GET /v1/capture/drafts`; `GET/POST /v1/capture/drafts/{id}` | List, retrieve or explicitly save the signed-in owner's unfinished capture work. |
| `DELETE /v1/capture/drafts/{id}?revision=N`; `GET /v1/capture/drafts/{id}/images/{index}?revision=N` | Delete the specified saved revision or retrieve a verified original; owner-only access. |
| `POST /v1/inspections` | Queue validated package images; returns a job handle with HTTP 202. |
| `GET /v1/jobs` | List permitted processing jobs by state and page; inspectors are restricted to their own jobs. |
| `GET /v1/jobs/{id}`; `POST /v1/jobs/{id}/retry` | Inspect processing state or retry saved evidence. |
| `POST /inspections/{id}/context` | Record confirmed package facts and rerun applicable checks. |
| `POST /inspections/{id}/intelligence/correct`; `POST /inspections/{id}/allergens` | Correct supplementary observations or change informational ingredient concerns. |
| `POST /inspections/{id}/review`; `GET /inspections/{id}/revisions/{revision}` | Save review actions and retrieve retained revisions; consult the schema for the action fields. |
| `GET /v1/analytics` | Database-backed summaries with supported date/dataset filters. |
| `GET /v1/rules/{version}` | Retrieve an available retained rule pack. Authentication is required. |
| `GET /v1/products/{gtin}/history` | Retrieve matching operational history by default; use `?source=bench` for Bench records. |
| `GET /v1/admin/rules`; `GET /v1/admin/rules/{version}` | Administrator rule-version listing and retained JSON. |
| `POST /admin/rules/import`; `POST /admin/rules/activate` | Administrator draft import and version selection. |
| `GET /healthz` | Public basic server/rule-version status. |

Account-management pages have their own **Dashboard**, **New inspection**, **History**, **Users**, and **Audit log** links. Return to a main page or use [Rule versions](http://127.0.0.1:8000/admin/rules) to reach rule administration.

## 14. Coverage against the linked requirements

“Available” means the named functionality exists; it does not mean every accuracy, speed, legal or scale target in the reference has been met.

### All 43 supplied sections

This matrix accounts for every numbered section in the retained reference. “Partial” means there is working, test-backed behavior, but at least one requested capability or acceptance claim remains unfinished.

| Section | Requested area | Verified state and boundary |
|---|---|---|
| 1 | Primary objective | **Partial.** The core capture, extraction, rules, evidence, reports, history, dashboards, search and access workflow is available; the wider platform objective still needs physical, legal, scale and field acceptance. |
| 2 | Inspect the existing codebase | **Completed process.** Routes, services, templates, JavaScript, rule data, storage, reports, tests, packaging and runtime behavior were audited before and during the fixes. |
| 3 | Product vision | **Partial.** Tula operates as an evidence-led inspection system; production and jurisdiction-wide readiness are not claimed. |
| 4 | UI/UX | **Partial.** Responsive capture/review/history/admin flows, empty/error/loading states and keyboard/accessibility checks exist; formal accessibility and representative-device certification remain. |
| 5 | Dashboard | **Partial.** Live database summaries cover activity, outcomes, category, typed location, review work and optional rounded inspection coordinates. The accessible indexed coordinate view opens its underlying Repository records while preserving scope, but it is not a validated political-boundary/market-prevalence map; external production load proof remains. |
| 6 | Inspection workflow | **Available.** Guided upload, panel assignment, processing, review, corrections, rescan, submit, approve and exports are connected end to end. |
| 7 | Robust OCR | **Partial.** Actual local RapidOCR, multiple processing passes, orientation/region handling and evidence spans are used; independent representative accuracy and all difficult scripts/layouts remain. |
| 8 | Do not trust OCR blindly | **Available with limits.** Alternatives, confidence, source regions, warnings, conflicts and human corrections prevent uncertain text from becoming silent fact. Calibration still needs independent data. |
| 9 | Non-horizontal/inconsistent layouts | **Partial.** Rotation, crop, spatial grouping, multi-line fragments and source-panel context are handled; arbitrary curved and heavily distorted labels remain difficult. |
| 10 | Structured declaration extraction | **Partial.** Nine core declaration classes plus supplementary product/date/ingredient/contact observations are retained with provenance; the reference's wider schema needs annotated validation. |
| 11 | Legal Metrology rule engine | **Partial.** An 18-rule, source-linked, versioned pack evaluates confirmed facts and applicability. It is a legal-review draft, not independently signed legal advice or exhaustive category coverage. |
| 12 | Rule-based plus AI architecture | **Available for current scope.** OCR/extraction proposes evidence; deterministic code makes rule outcomes and calculations. No unlabelled generative decision path is presented as legal truth. |
| 13 | Evidence-first design | **Available.** Findings link to source images/regions/text, originals and hashes are retained, and revision/report references are auditable. Signed capture credentials and trusted timestamps remain. |
| 14 | Compliance status | **Available.** Compliant, non-compliant and needs-review semantics are separated from workflow state and approval. |
| 15 | Human in the loop | **Available.** Inspectors correct and decide, supervisors independently approve/request changes, and administrators manage rule versions. |
| 16 | Confidence system | **Partial.** OCR/extraction confidence, alternatives, quality warnings and uncertainty-aware findings are exposed; statistical calibration on an independent benchmark remains. |
| 17 | Image quality analysis | **Partial.** Blur, glare, exposure, lighting, resolution and tight-crop context guidance is generated before legal review; mobile live-camera geometry, representative false-positive rates and device latency are not validated. |
| 18 | Intelligent rescan | **Available.** Reviewers can request a reasoned, targeted rescan; a linked child preserves its parent and captured revision, context and history. |
| 19 | Ingredient/allergen analyzer | **Partial.** Ingredient text, declarations and allergen observations can be reviewed and corrected; comprehensive food-law inference and validated multilingual entity extraction remain. |
| 20 | Expiry/date intelligence | **Partial.** Packing, manufacture, best-before/expiry candidates, source isolation and ambiguity handling exist; every date syntax and category rule is not covered. |
| 21 | Product intelligence | **Partial.** Identity fragments, GTIN checks, quantity/price context and matching-history observations exist; authoritative registries and robust product resolution remain. |
| 22 | Multi-image understanding | **Partial.** Multiple panels are processed together, evidence provenance is preserved and conflicts are raised; general video/burst fusion and full six-face reconstruction remain. |
| 23 | Report generation | **Available.** Audited PDF, editable DOCX and draft notice exports include inspection facts, findings, evidence and revision/rule references. PDF/A-3 and all-office compatibility remain unproven. |
| 24 | Inspection history | **Available.** Immutable revisions, workflow events, parent/child rescans and product history can be inspected. Cross-tenant/state-scale entity history remains. |
| 25 | Search | **Available with limits.** Literal search, citation queries, filters and pagination cover retained inspections. Million-record latency has not been tested. |
| 26 | Role-based access | **Available for the three supplied roles.** Inspector, supervisor and administrator permissions are enforced; broader future personas/tenancy are outside the current role set. |
| 27 | Audit log | **Available.** Authentication, review, approval, export, administrative and backup-related actions retain actor/time/context records. External immutable log storage remains. |
| 28 | Security | **Partial.** Password hashing, session/cookie/CSRF controls, authorization, upload validation, private evidence and security headers are tested. HTTPS termination, secret rotation and external penetration testing are deployment work. |
| 29 | Performance | **Partial.** Durable background analysis, Processing/retry, indexed pagination/date queries and bounded 100,000-record/job concurrency measurements exist; target hardware and external production load remain unproven. |
| 30 | Error handling | **Available.** Validation errors, stale revisions, failed jobs, retries and generic correlated server errors have recovery paths without exposing private exception text. |
| 31 | No fake AI | **Available as a guardrail.** Actual OCR is used, uncertain/missing facts remain explicit, and generated Bench cases are labelled separately from real-photo evidence. |
| 32 | Difficult real-world labels | **Partial.** Real photographs, rotated/cropped/fused conflicts and generated dotted controls were exercised; the sets are development data, not a blinded representative benchmark. |
| 33 | Automated testing | **Available.** The current complete suite passes 1,505 tests plus Ruff and capture-JavaScript syntax validation; browser, installed-wheel, backup/restore, OCR, export, quality, geography/drill-down and query-plan checks are retained. External device/load/legal acceptance remains separate. |
| 34 | Data model | **Available for current scope.** Users, inspections, images, OCR evidence, declarations, findings, decisions, revisions, jobs, drafts, reports, rule versions and audit events persist in 26 tables. |
| 35 | Architecture quality | **Available for current scope.** Domain, extraction, rules, services, storage, security, reports and web layers are separated and packaged. Broader deployment architecture is not certified. |
| 36 | Observability | **Available.** Privacy-bounded JSON events correlate HTTP, background stages and report generation by request ID with timings; raw query-bearing Uvicorn access lines are suppressed. External collector/alert integration remains. |
| 37 | Legal safety/uncertainty | **Available as a product constraint.** Draft-rule provenance, uncertainty, applicability, confirmation and human approval are explicit. Independent legal sign-off is still required. |
| 38 | Impressive demo | **Available locally.** Seeded records, a 20-scenario Bench, actual-photo examples and guided workflows demonstrate core behavior; production claims are deliberately bounded. |
| 39 | Design for judges | **Available through the product and this manual.** Problem, workflow, intelligence, trust and impact can be demonstrated with evidence. A separate competition presentation is not part of this repository. |
| 40 | Avoid useless overengineering | **Applied as a scope rule.** Implementation favors auditable local workflows and records deferred capabilities instead of simulating them. |
| 41 | Final quality bar | **Partial.** Current automated/local-installed gates pass, while legal sign-off, physical metrology, external load, formal accessibility and representative OCR gates remain open. |
| 42 | Execution rule | **Applied.** The work followed repeated inspect, implement, run, test, debug, polish and end-to-end verification cycles with retained failure history. |
| 43 | Final deliverable | **Partial.** Source, rules, manual, operations guide, test outputs, package and acceptance receipts are present; the open external/physical/legal gates prevent a claim of universal production perfection. |

### Functional requirements and differentiators

| Reference | Current coverage | Remaining work |
|---|---|---|
| FR-01 Upload | Available with image limits and validation. | Verify all device/browser formats and target queue latency. |
| FR-02 Guided multi-view | Multi-image preview, panel selection and guidance available. | Enforced capture sequence and measured six-face success rate. |
| FR-03 Real-time quality | Server pre-analysis blur/glare/exposure/lighting/resolution guidance, retained-crop percentage and processing diagnostics. | Validated live mobile tilt/distance/coverage feedback, representative false-positive measurement and device latency target. |
| FR-04 Burst/video fusion | Not implemented. | Capture, align and evaluate multi-frame fusion. |
| FR-05 Barcode | Decode/check-digit path available. | Establish required symbology coverage and field decode rate. |
| FR-06 Declaration extraction | Nine core classes plus supplementary observations, spatial context, rotated multi-line identity fragments, source provenance and explicit uncertainty. | Complete the proposed wider declaration schema and validate these heuristics on independently annotated labels. |
| FR-07 Language/script | Latin/Devanagari handling and script observations. | Broader validated language coverage; correct the reference's bilingual legal assumption. |
| FR-08 Vector artwork | Not implemented. | PDF/AI ingestion and exact source metrics. |
| FR-09 Listing URL | Not implemented. | Marketplace text/image ingestion and comparison. |
| FR-10 Scale-source fusion | Backend calibration/metadata support exists. | Two independently acquired real sources on supported devices. |
| FR-11 Cap-height uncertainty | Implemented measurement logic. | Physical reference-set validation and claimed error bounds. |
| FR-12 PDP/package geometry | Partial dimensions and package context. | Reliable curved/irregular geometry and propagated classification uncertainty. |
| FR-13 Guard-band decision | Implemented interval/tier checks. | Validate policy by rule; do not conflate text evidence with metric assurance. |
| FR-14 Versioned rules | Retained packs/findings, dated legal gates and administrator selection exist. | Full amendment history and independent legal sign-off. |
| FR-15 Exemptions | Source-linked conditional/category/date gates and confirmed package facts. | Broader exception capture and complete category-specific rule coverage. |
| FR-16 Legibility index | Quality heuristics only. | Validated consumer-vision legibility model. |
| FR-17 Tamper forensics | Duplicate-price screening only. | Validated physical over-sticker/tampering detection and lawful exceptions. |
| FR-18 External validity | Parsing and local identifier checks only. | PIN, contact reachability, geocode and authorised registries. |
| FR-19 Human review | Decisions, corrections and independent approval available. | Legally authorised notice-service workflow. |
| FR-20 Training corrections | Evidence-linked corrections and immutable revisions retained. | Curated annotation/training export and model feedback process. |
| FR-21 Archival PDF | PDF with images and embedded JSON available. | Demonstrate PDF/A-3 conformance. |
| FR-22 DOCX | Editable report and draft notice available. | Broader cross-office compatibility verification. |
| FR-23 Statutory notice | Draft working document available. | Complete facts, current legal procedure, authorisation and service. |
| FR-24 Evidence chain | Ingestion hashes, original preservation and optional consented coordinates are available. | Physical GPS accuracy, signed capture attestation, trusted timestamps and signed custody chain. |
| FR-25 Search | Search, citation queries, literal punctuation, filters and pagination available. | Test the reference's million-record latency target. |
| FR-26 History | GTIN history, inspection revisions and linked parent/child capture history with the captured parent revision and earlier corrections. | Full packer identity/history resolution. |
| FR-27 Quantity/price drift | Basic GTIN quantity-change observations available. | Validated product matching and richer longitudinal alerts. |
| FR-28 Roles | Inspector, supervisor and administrator implemented. | Auditor/brand/citizen roles and district/state/brand tenancy. |
| FR-29 Dashboard | Database-backed activity, approved compliance by capture date, category, typed location, review summaries and an indexed optional-coordinate plot/table with scoped Repository drill-down; local 100,000-record migration/query performance measured. | Validated boundary/heat maps, case-level geographic comparison and external production/load validation. |
| FR-30 Targeting/routes | Not implemented. | Risk models, field evaluation and route planning. |
| FR-31 Offline sync | Local OCR and durable server jobs. | Complete offline mobile operation and sync conflict resolution. |
| FR-32 Rule authoring | Read-only Rules page plus protected administrator import, selection and rollback. | Visual authoring, diff and test-impact preview. |

The reference also names 14 differentiators. Their coverage is:

| Reference differentiator | Current state |
|---|---|
| D1 Metric type measurement | Implemented measurement/uncertainty logic; independent physical validation remains. |
| D2 Package digital twin | Images have panel assignments; there is no reconstructed six-face atlas. |
| D3 Consumer-eye legibility | Quality heuristics exist; low-vision simulation and a validated index do not. |
| D4 Price-sticker forensics | Multiple-price text review exists; physical sticker/tamper detection does not. |
| D5 Shrinkflation watch | Basic compatible-quantity changes across matching GTIN records. |
| D6 Unit-price audit | Deterministic basis/arithmetic checks with applicability gates. |
| D7 Consumer-care reachability | Printed contact parsing; no phone/email/address reachability verification. |
| D8 GTIN forensics | Decode/check-digit checks and qualified prefix observations; no authoritative authenticity check. |
| D9 Versioned rules | Retained packs, sources, findings and administrator selection; legal sign-off remains. |
| D10 Dual-path arbitration | Multiple OCR processing passes expose disagreements; no document-VLM arbiter or calibrated selective-accuracy claim. |
| D11 Chain of custody | Original files and ingestion hashes retained; no signed capture credentials, trusted timestamp or Merkle custody bundle. |
| D12 Bilingual verification | Limited script handling; the reference's claim that both languages are always required needs correction. |
| D13 Risk targeting/offender graph | Database summaries only; no validated risk model, routes or cross-state entity graph. |
| D14 Public benchmark | Local generated scenarios and difficult-image tests; no published adjudicated benchmark platform. |

Current OCR uses actual local recognition and processing passes. The unimplemented capabilities above should not be presented as functioning features in the demo.

## 15. Tests actually performed

The current complete integration run passed **1,505 tests in 495.47 seconds**, covering the review/editor, targeted rescan integrity/history, multi-image and fused-region reconciliation, report wording, draft-backup, identity, OCR uncertainty, capture-quality guidance, optional coordinate capture/storage/dashboard/drill-down behavior, operational observability and scalable query-plan changes. Source and tests pass Ruff, and capture JavaScript passes the Node syntax check. The current wheel also passed clean-installed release acceptance outside the checkout, including a genuine service restart and restored draft preview. The previous 1,497-, 1,476-, 1,471-, 1,462-, 1,315- and 1,059-test checkpoints remain historical evidence; overlapping totals are not added. Passing software tests does not establish complete legal coverage, representative OCR accuracy or production acceptance.

| Evidence | What was verified |
|---|---|
| Complete integration | 1,505 tests passed. Output: `out/geography-drilldown-all-tests.txt`. |
| Optional coordinates | Unit/integration/browser-script cases cover numeric and range validation, capture-draft round trips, resume/submission, background-job propagation, source/date scoping, 0.001° grouping, the 200-group cap, exact totals, legacy migration, point/table drill-down, invalid paired filters and both geography indexes. The installed-only verifier rendered the SVG/table from `site-packages`, separated operational from Bench data and opened the matching repository group. |
| Geography visual QA | The real Jinja geography partial was rendered with clearly labelled synthetic Bengaluru, Nagpur, New Delhi and outside-extent points. Desktop 1440 px and mobile 390 px screenshots were inspected; the mobile pass led to a responsive SVG fix and a successful rerender. The drill-down rerender had no document overflow at either width, and programmatic keyboard focus reached the point link with its expected source/date/coordinate query. No operational location data or permission prompt was used. |
| Capture quality | Generated clean, nearly white and four-percent-crop controls passed; their contact sheet was visually inspected. Fourteen unique retained photographs decoded with zero new overexposure flags after 41 duplicate copies were excluded by SHA-256. This is not a representative device benchmark. |
| Review saves | Eight actual Chromium cases covered network failure, HTML/JSON server errors, structured validation, unexpected responses, a real successful save/reload, a real stale revision and session loss. Unsaved entries survived failures. The successful QA comment advanced record `1C45AC48B9` to revision 6 and reloaded at the review section. |
| Shared save script | 25 executing-JavaScript cases cover section navigation for review/context/allergens/intelligence/rule import/activation, request and response-body timeouts, duplicate/sibling submission locks, CSRF, input retention and recovery guidance. |
| Both correction editors | Twelve actual Chromium checks covered failed source-image loading and successful retries, invalid rectangles blocked before posting, and exact valid rectangle serialization. Valid submissions were intercepted with a QA validation response, so this run did not commit corrections. Backend correction and revision behavior is covered separately by integration tests. |
| Rule administration | Actual browser import of an unchanged QA clone, activation, stale-version rejection and rollback passed. The active pack was restored to `2026.09.07-legal-review-1`; the clone and audit trail remain. An existing inspection's JSON was byte-identical before and after. |
| Capture drafts | Earlier 51 focused API/browser-script/capture-quality cases and actual browser save → resume in a new tab → retained original/crop/rotation → Analyze passed with a real Parle photograph. Deleting its QA draft preserved inspection evidence. Later database-to-archive draft validation passed 69 backup/draft and 36 capture/runtime cases in separate runs. |
| Actual officer workflow | Earlier generated-image upload, crop, actual RapidOCR, saved record, rescan note, declaration correction, finding decision and dashboard update were exercised. QA Sachet `133BC2DDBE` remains Approved revision 2 through separate QA inspector/supervisor accounts. One QA agent operated both; this is account-separation testing, not independent human review. |
| Targeted rescan | Actual Chromium capture → crop → save draft → resume in a new tab → fresh attestations → actual OCR created child `1A769C7032` from parent `1C45AC48B9` revision 6. Parent JSON stayed byte-identical; the child retained its reference/hash/rule basis and began a fresh review. This reused a retained generated QA image, not a new physical photograph. |
| Conflicting readings | Regression cases cover disagreements between images and repeated cues inside one OCR region, role-specific dates and contacts, equivalent quantities, retained provenance and conservative supplementary summaries. The final focused reconciliation set contains 85 cases. |
| Reports | PDF, DOCX and notice downloads retained matching hashes, revisions and audit records. PDF JSON attachments matched saved records. All 20 pages of representative 14-page and six-page PDFs were rendered and visually inspected after pagination fixes. |
| Current rescan report and interface | All 15 child finding headings were checked against their outcomes; 12 newly exported PDF pages were rendered and visually inspected. PDF embedded JSON matched the saved record; DOCX text and the parent reference were checked. Expanded parent/child views at 1366 and 390 pixels had no reported axe violations, page errors or document overflow. Some accessibility checks remain incomplete; visual Word/LibreOffice compatibility was not tested. |
| Search, dashboard and Processing | Category/context filters, same-finding rule/outcome filters, flagged dashboard links, account-scoped job lists, recovery/retry and invalid-link guidance were checked. Rates exclude pending, exempt and not-applicable records. |
| Current installation | The current geography drill-down wheel passed in a new isolated environment outside the checkout: all 117 packaged members matched current source, including 27 web assets and 18 rules; three models were checked. All 50 installed distributions resolved inside the clean environment. Actual OCR, private saved-draft resume, separate-account approval, Processing, audited exports and exact restoration of all 26 tables and eight retained files passed. A fresh restored process regenerated the draft preview. The installed geography verifier separately checked invalid-coordinate rejection, operational/Bench isolation, rounding, the accessible SVG/table, outside-extent disclosure, scoped drill-down, the dedicated index and `geolocation=(self)`. Source-browser rescan testing is separate from this installed workflow check. See the operations guide for the receipt. |
| Indexed repository reads | On the same local 100,000-inspection/20,000-finding SQLite dataset, recent pages improved from 1,448.7 ms to 18.7 ms median, deep pagination from 2,500.4 ms to 23.5 ms, dashboard analytics from 1,578.2 ms to 639.4 ms and 64 concurrent filtered reads from 15.5 seconds to 3.8 seconds. A separate 100,000-job worklist check passed with covering indexes. On a copied 100,000-record fixture with 50,000 records in one rounded coordinate group, group count plus a recent 25-record page improved from 59.549 ms to 8.427 ms median after the expression/recency index; both queries selected it and index creation took 443.43 ms. Coordinates were assigned directly for this synthetic query benchmark, so it is not OCR, concurrent-write or external load evidence. |
| Historical installation | The previous wheel passed outside-checkout actual OCR, permissions, separate-account approval, audited exports, Processing and exact backup/restore. It also passed in a clean venv and again with optional Tesseract disabled. Those results belong to that earlier wheel. |

The earlier interface scan covered **27 page/viewport cases**: sign-in plus 13 pages at 1366 and 390 pixels, with no reported axe violations, document overflow or JavaScript errors. Actual keyboard actions verified Skip to content and scrolling in 11 evidence/history regions. After the review changes, a focused seven-case repeat covered sign-in, review, approved inspection and rule administration at both widths, again with no reported violations, document overflow or page errors. Some automated checks remain incomplete. This does not certify full WCAG conformance or physical-camera operation.

The first installed acceptance attempt exposed a test-harness restart flaw: it reused an imported application in the same Python process after restoring files. The harness now launches a new installed process, matching an actual restart; the unchanged application wheel then passed. The failed attempt and successful receipt are retained separately. This needed no production code change. Windows fonts remain a host dependency, and the Python socket guard is not an OS-wide air-gap test.

The first full rescan regression run had one outdated assertion requiring an empty normalization dictionary. Extraction correctly retained diagnostics without borrowing the date from another image. The test now explicitly checks absent normalized date components and source-frame isolation; the complete rerun passed. Both outputs remain available, and no production code changed for this test correction.

The QA runtime at [Inspect](http://127.0.0.1:8000/) was refreshed after the geography drill-down source freeze. Its four retained upload jobs were complete, with none queued or running before restart. Migration preserved the nullable coordinate columns and added `idx_inspection_source_geo_recent`; its 24 older inspections remain unlocated. Original analyses and rule versions were preserved. Login and updated CSS returned HTTP 200. The walkthrough used QA Supervisor; an idle session may require signing in again. The separate older runtime on 8765 was not changed.

### Actual photographs: the ten-photo development set

Ten distinct existing package photographs were visually annotated. Seven mainly show fronts; the rest have cropped or unreadable declarations. This is **unblinded development evaluation by one agent**, not an independent representative benchmark. The latest actual OCR run is retained in `real-label-evaluation-rescan-final`; images and annotations stayed unchanged.

| Readable field | Targets | Original baseline accepted exact | Earlier context checkpoint | Current accepted exact | Current structured candidate exact |
|---|---:|---:|---:|---:|---:|
| Brand/sub-brand | 7 | 4 | 7 | 6 | 7 |
| Commodity | 7 | 4 | 6 | 6 | 6 |
| Visible rupee amount | 2 | 0 | 0 | 0 | 2 |
| Total | 16 | 8 | 13 | 12 | 15 |

The original baseline had six incorrect accepted values. The earlier context checkpoint, latest actual run and subsequent extraction-only replay have **zero wrong accepted values among the 16 scored targets**. The latest result has four abstentions, including review-only outputs. Accepted means an extraction status, not officer approval or a legally complete declaration.

The current brand safeguard deliberately holds Maggi's correct candidate for review because its photographed commodity text is not reliably read. The other six readable brands remain accepted. This reduces automatic acceptance by one while preserving the original brand candidate. It also prevents a date-close-up fragment in the additional dataset from establishing product identity. No expected brand names or filenames supply the decision.

Both difficult front prices remain structured review candidates: Parle 10 and Kurkure 20, with unverified currency. Neither is an accepted primary MRP or a price-arithmetic input. Parle's low-confidence `=10/-` alternative and Kurkure's symbol-shape hypothesis are retained for inspection; they do not establish complete statutory MRP declarations.

The **134 other field/photo pairs remain unknown and unscored**. There are no readable positive normalized date, batch or quantity targets in this set. Latest actual runtime was 4.16–10.67 seconds per image, median 6.94 seconds on this machine; this is one local run, not a throughput promise. See the [latest actual scored report](C:/Users/offic/OneDrive/Desktop/sih/out/real-label-evaluation-rescan-final/REPORT.md) and [actual-run findings](C:/Users/offic/OneDrive/Desktop/sih/out/rescan-final-validation/FINDINGS.md). Its metrics match the [older uncertainty checkpoint](C:/Users/offic/OneDrive/Desktop/sih/out/real-label-evaluation-after-uncertainty/REPORT.md).

### Four additional real photos: dates, codes and prices

Four more photographs had **14 literal printed-text targets and five unambiguous structured targets frozen before OCR**. They include two Commons photographs and two Nestlé India publisher photographs. Ambiguous dates/codes are literal-only; no quantity was inferred from price arithmetic or metadata. Source attribution, publisher annotations and reuse restrictions remain in the [dataset status](C:/Users/offic/OneDrive/Desktop/sih/data/evaluation/additional-real-labels/STATUS.md).

The latest actual run recovered **10/14 literal targets in selected OCR and 12/14 among all OCR candidates**, compared with 9/14 in both measures at the older uncertainty checkpoint. It correctly accepted **4/5 structured targets**: both prices and both unit prices, with **zero wrong accepted among those five** and one batch abstention. The literal and structured denominators differ; the seven date literals provide **zero normalized-date validation targets**. Missing or ambiguous dates are not counted as correct dates.

On the reflective can, an alternate-orientation pass now retains all three frozen literals—`13/04/2023`, `12/04/2024` and `103A`—among candidates. Only the first date is selected literally, and both stamped rows require review because credible readings conflict. Batch `103A` is present in an OCR alternative but remains absent from structured extraction because the parser does not accept `B:` as a batch cue. The photograph does not establish manufacture-versus-packing, unambiguous date order, market or printed country of origin.

The tin's printed `OCT/26` is still read as `0CT/26`, including a high-confidence reading. The safeguard retains the raw token, original scores and image region but requires review; it does not invent a corrected month or expiry date. The Canadian `13 JN 02` date is still misread. Its spurious brand text, including `AUANT`, remains a review candidate with no accepted brand name, outside the five-target scoring denominator.

The [latest four-photo report](C:/Users/offic/OneDrive/Desktop/sih/out/additional-real-label-evaluation-rescan-final/REPORT.md) retains raw OCR, candidates, original-coordinate boxes and scoring evidence. Source, models, annotations and original-photo hashes passed the [combined integrity checks](C:/Users/offic/OneDrive/Desktop/sih/out/rescan-final-validation/VERIFICATION.json). The [older uncertainty run](C:/Users/offic/OneDrive/Desktop/sih/out/additional-real-label-evaluation-after-uncertainty/REPORT.md) remains a comparison, not the current recognition result. The Commons licence terms and Nestlé reuse restrictions/source-added red rectangles remain relevant; these are not an unrestricted benchmark dataset. Foreign or unknown market context is not Indian legal ground truth.

### Retained generated dotted controls

The latest actual OCR/extraction run used the **unchanged original generated pixels**, including the current orientation and uncertainty safeguards:

| Control | Correct structured targets | Correct automatically accepted | Wrong accepted |
|---|---:|---:|---:|
| Clean generated label | 5/5 | 5/5 | 0 |
| Moderate dotted label | 5/5 | 1/5 | 0 |
| Severe sparse label | 1/5 | 1/5 | 0 |

The clean `BATCHAB1234` reading correctly yields batch `AB1234`. The strict selected-literal token-boundary score remains 4/5 because cue and value are joined; candidate and structured recovery are 5/5. On the moderate image, price, manufacturing date, expiry date and batch stay review-only. Severe damage still needs a clearer image. These metrics match the [older joined-batch checkpoint](C:/Users/offic/OneDrive/Desktop/sih/out/retained-ocr-after-batch/FINDINGS.md); [latest retained-control results](C:/Users/offic/OneDrive/Desktop/sih/out/retained-ocr-rescan-final/results.json) validate the later actual source. The same run also repeats the real Parle front photo: one correct structured price candidate, zero accepted. It is already in the ten-photo set and is not an additional independent photo.

An earlier broader generated benchmark recovered 104/109 candidate values across 24 variants, with 93/109 uncontested. Those figures belong to that earlier source and must not be combined with these denominators. The installed default RapidOCR alphabet lacks the rupee glyph; visual currency hypotheses do not prove INR. Representative independent multilingual, difficult-print and physical-device validation remain needed.

**Actual run versus current extraction:** all three `rescan-final` runs preceded the last supplementary-summary consistency fix. After that fix, a [separately recorded extraction-only replay](C:/Users/offic/OneDrive/Desktop/sih/out/rescan-supplementary-replay/FINDINGS.md) processed all 18 retained OCR records from these sets. Every scored metric and full serialized extraction was unchanged; original OCR, images, annotations, models and source integrity were verified. No recognizer was invoked during replay. This gives current extraction evidence without presenting replay as fresh recognition. These evaluators do not test the legal engine, report/UI, deployment or complete rescan workflow, and zero wrong accepted applies only to their scored targets.

Supporting evidence:

- [Review-save and editor verification](C:/Users/offic/OneDrive/Desktop/sih/out/review-save-qa/VERIFICATION.md)
- [Rule administration browser results](C:/Users/offic/OneDrive/Desktop/sih/out/rule-admin-ui-qa/verified/verification.json)
- [Interface verification](C:/Users/offic/OneDrive/Desktop/sih/out/interface-qa/VERIFICATION.md)
- [Current focused interface results](C:/Users/offic/OneDrive/Desktop/sih/out/interface-qa/review-release/summary.json)
- [Current complete integration output: 1,505 passed](C:/Users/offic/OneDrive/Desktop/sih/out/geography-drilldown-all-tests.txt)
- [Current geography release verification](C:/Users/offic/OneDrive/Desktop/sih/out/geography-installed-qa/VERIFICATION.md)
- [Current desktop coordinate drill-down visual](C:/Users/offic/OneDrive/Desktop/sih/out/geography-installed-qa/final/geography-drilldown-desktop.png) and [mobile coordinate drill-down visual](C:/Users/offic/OneDrive/Desktop/sih/out/geography-installed-qa/final/geography-drilldown-mobile.png)
- [Historical image-quality release verification](C:/Users/offic/OneDrive/Desktop/sih/out/quality-installed-qa/VERIFICATION.md)
- [Historical quality comparison sheet](C:/Users/offic/OneDrive/Desktop/sih/out/quality-installed-qa/final/quality-guidance-comparison.png)
- [Historical indexed-read release verification](C:/Users/offic/OneDrive/Desktop/sih/out/performance-installed-qa/VERIFICATION.md)
- [Current repository performance comparison](C:/Users/offic/OneDrive/Desktop/sih/out/performance-installed-qa/final/performance-verification.json)
- [Current processing-worklist comparison](C:/Users/offic/OneDrive/Desktop/sih/out/performance-installed-qa/final/performance-jobs-verification.json)
- [Earlier complete integration output: 1,462 passed](C:/Users/offic/OneDrive/Desktop/sih/out/rescan-accepted-integration-tests.txt)
- [Earlier complete integration output: 1,315 passed](C:/Users/offic/OneDrive/Desktop/sih/out/final-review-integration-tests.txt)
- [Earlier complete integration output: 1,059 passed](C:/Users/offic/OneDrive/Desktop/sih/out/manual-handoff-integration-tests.txt)
- [Report pagination verification](C:/Users/offic/OneDrive/Desktop/sih/out/report-pagination-qa/VERIFICATION.md)
- [Approved QA PDF](C:/Users/offic/OneDrive/Desktop/sih/out/live-approval-qa/report.pdf) and [editable report](C:/Users/offic/OneDrive/Desktop/sih/out/live-approval-qa/report.docx)
- [Targeted rescan browser and report verification](C:/Users/offic/OneDrive/Desktop/sih/out/rescan-browser-qa/VERIFICATION.md)
- [Multi-image and fused-region reconciliation evidence](C:/Users/offic/OneDrive/Desktop/sih/out/multi-image-reconciliation-qa/FINDINGS.md)
- [Current clean-installed acceptance receipt](C:/Users/offic/OneDrive/Desktop/sih/out/geography-installed-qa/final/acceptance.json)
- [Historical operational-observability verification](C:/Users/offic/OneDrive/Desktop/sih/out/observability-installed-qa/VERIFICATION.md)
- [Operations and release acceptance](C:/Users/offic/OneDrive/Desktop/sih/docs/OPERATIONS.md)

The exported examples use explicit QA review data. They are not approved government records. Local OCR after installation does not provide an offline phone application or deferred mobile synchronization.

## 16. Troubleshooting

| What you see | What to do |
|---|---|
| Sign-in screen | Use the provisioned account. An expired session does not delete saved records. |
| Too many sign-in attempts / HTTP 429 | Stop retrying and wait for the indicated retry period; the limiter uses a 15-minute window. Then check the account details or contact the administrator. |
| Cannot preview an image | Use Retry image check. HEIC has a server fallback; if decoding still fails, export JPEG/PNG and check size/pixel limits. |
| Camera unavailable/denied | Use file upload, or use a supported browser and appropriate camera permission. |
| Device location unavailable/denied/timed out | Continue without coordinates, or check browser/site location permission, device location services and secure/loopback access before pressing Record device location again. Clear recorded location if a previous reading should not be submitted. |
| Low-resolution warning | Move closer and use the original full-resolution image. |
| Checking image quality… | Wait for the preview request; analysis stays disabled while checks are pending. |
| Image check unavailable | Retry image check. If the browser already has a normal preview, analysis can continue with the warning and assess quality again. HEIC needing server decoding requires a successful preview or conversion. |
| Apply crop disabled | Enter integer bounds inside the rotated original, at least 16 pixels wide and high. |
| Rescan has few available image slots | The parent's retained images count toward the combined 12-image limit. |
| Original inspection changed | Review the latest original revision and start a fresh linked capture. A saved draft cannot silently switch to a later parent revision. |
| Original evidence is missing or changed | Restore the matching retained files before creating a linked inspection. The original record remains available for documenting the problem. |
| Different photos disagree | Compare all retained candidates and sources; add a clearer close-up or make a reasoned correction. A conflicting quantity or date cannot establish an automatic exemption. |
| Selected images disappeared after a reload | Use Inspect → My saved drafts if you explicitly saved them. Unsaved changes require selecting the files again. If Analyze was already accepted, check Processing before uploading a duplicate. |
| Draft save quota reached | Delete an unneeded saved draft or reduce its original images. Limits are 10 drafts, 100 MB per draft and 300 MB per account. |
| Draft unavailable or expired | Use the owning account and current saved list. Drafts expire 30 days after the last save; accepted inspection records are separate. |
| Draft changed in another tab | Resume its latest revision. Stale saves and deletes are refused to preserve newer work. |
| OCR readings need verification | Compare the marking and add a targeted close-up. The warning may identify conflicting readings or suspicious characters in a single reading; do not invent a replacement to clear it. |
| A readable brand appears only as a candidate | Check its source image. A logo without reliable product context in that image can require review. Verify the printed identity through Correct a declaration → Brand; another image's commodity text does not automatically validate it. |
| No MRP/date found | Check omitted faces and print quality before treating text as absent. |
| No physical height result | Add valid scale evidence and measured panel context; image DPI alone is insufficient. |
| Analysis still running | Read the stage; multiple difficult images can take longer. Return to the saved job. |
| Processing says no matching jobs | Select All jobs or Saved inspections. The default view contains unfinished and failed uploads only. |
| Saved-job link is unavailable | Open Processing under the account that submitted the upload, or an authorised supervisor account. A malformed link should be replaced using the saved list. |
| A price appears only in Label intelligence | It is a review candidate. Check the amount and currency in the image, then correct the primary retail-sale-price declaration with source evidence and a reason. |
| Analysis failed | Read the error and use Retry saved images when offered. |
| Cannot submit | Resolve every pending potential/uncertain finding; add a review reason. |
| Cannot approve | Use an independent supervisor who did not create, submit or materially contribute to any retained revision. |
| Saving review… and other controls are disabled | Wait for the current save. The record's review forms are locked together to prevent overlapping writes. A confirmed save reloads the latest revision at the relevant section. |
| Record changed elsewhere / HTTP 409 | Keep the current entries and use Open latest record in a new tab. Review the newer revision before copying still-needed changes into it. Do not overwrite another reviewer's work. |
| Review save times out or reports an interrupted connection/server error | Entries remain in the current tab. Open latest record in a new tab and check the revision and action first: a missing confirmation does not prove the save failed. Retry only after checking. |
| Session expired while saving | Use Sign in and check the record in a new tab. Copy unsaved entries into the newly signed-in record and save there; the old tab has the previous session token. |
| Correction source image does not load | Use Retry source image. If sign-in expired, sign in and reopen the record. If evidence integrity fails, restore the matching original; correction remains blocked until a valid source loads. |
| Correction rectangle is rejected | Enter four whole-pixel bounds inside the loaded source dimensions, with Right greater than Left and Bottom greater than Top. Blank, fractional or out-of-image bounds are invalid. Select the rectangle again after changing images. |
| Evidence integrity failure | Have the administrator restore matching retained evidence; do not replace it with another image. |
| Dashboard looks empty | Check Dataset/date filters and the server port/runtime database. |
| Zero violations but Needs review | Evidence or approval remains unresolved. Review the pending findings. |
| Search results look too broad | Combine date/dataset/manufacturer filters or use the exact Rule ID. A query of Rule 7 searches cited rules. |
| No Rule versions link | It is administrator-only. Inspectors can use the read-only Rules page. An older process must restart before newly installed routes are available. |
| A category filter returns no records | Check dataset/date filters and the Confirmed/Suggested category basis. The dropdown includes supported legal categories and retained older/custom labels. |
| A flagged-rule search returns no records | The selected rule itself must have Violation or Advisory. An Exempt or Pass result for that rule does not match, even if another rule has a flag. |
| A recent capture appears under yesterday | Capture-date filtering is UTC; India is 5 hours 30 minutes ahead. Check the displayed UTC time. |

## 17. What to improve next

The next implementation pass should follow this order. Items already implemented above are not counted as future work merely because they were absent from an earlier checkpoint.

**Delivered review fixes:** successful saves reload the current revision at the relevant section; review forms lock together while saving; failures keep entered values and provide recovery links. Both correction editors require a loaded source image and valid whole-pixel bounds and offer **Retry source image**. The primary editor also rejects stale image callbacks and restores the prior selection after a cancelled drag. Sections 6 and 16 explain the current workflow; the earlier manual-refresh workaround is no longer needed.

**Delivered identity safeguard:** an implicit name without reliable product context in its own image stays a visible review candidate. Explicit brand cues and supported product fronts can still be accepted. In the retained-OCR replay, this held a correctly read Maggi logo whose commodity wording was already uncertain, while preserving the six other accepted brands. This tradeoff needs further validation on unfamiliar labels; section 15 records the separately measured test results.

**Delivered close-up and multi-image safeguards:** declaration-specific capture links, saved focus/revision, retained original rule/date basis, parent/child navigation, earlier-correction comparison and report references now support the rescan workflow. Conflicting quantities, prices, same-role dates and identities retain their candidates for review. A clearer photograph does not automatically supersede conflicting retained readings; an audited correction may still be needed. Package identity across images is assumed, not independently verified. The next work is broader device and unfamiliar-package validation.

**Delivered finding wording:** unresolved web, PDF and editable-report headings describe insufficient evidence or the need for verification. The original machine message and evidence remain in the stored JSON. The latest 12-page rescan PDF was visually checked; broader office-editor compatibility remains future validation.

| Priority | Improvement | Concrete completion check |
|---|---|---|
| 1 | Complete legal acceptance and category coverage. | Independent source review, full historical applicability, remaining special-category rules and a package-context form covering necessary exception facts. |
| 2 | Validate identity extraction on additional untouched labels. | Evaluate contextual brand/commodity selection on unfamiliar layouts, including legitimate logo-only fronts and readable cues. Report accepted values, wrong acceptance and retained review candidates separately; do not treat a candidate as a verified identity. |
| 3 | Improve rupee-symbol and dotted MRP/date/batch reading. | Joined batch cues are supported, but severely damaged print and ambiguous dates remain difficult. Test additional independently annotated back/side/close-ups and currency symbols; preserve source readings and report exact values, wrong acceptance and abstentions separately. |
| 4 | Validate capture on real devices. | Test the existing blur/glare guidance, HEIC fallback, keyboard crop controls and explicit location permission across browsers, actual cameras and difficult packages; measure false quality warnings and GPS accuracy/denial behavior. |
| 5 | Extend review and capture acceptance. | Exercise the delivered save recovery and geometry checks across physical devices and interrupted connections. Extend full correction/rescan and administrator journeys across browsers and the installed release; local browser import/rollback and installed draft restoration have passed. Processing, filters and QA account-separated approval/export already exist. |
| 6 | Validate physical metrology. | Actual printed scale cards and measured labels across devices, package shapes and lighting; report measurement errors. |
| 7 | Extend rule administration. | Visual diff and test-impact preview before selecting a draft; named legal review attached to the version. |
| 8 | Finish report and interface validation. | PDF heading pagination is fixed and representative pages were inspected. Verify broader Word/LibreOffice compatibility, accessibility, physical-device behavior, digital signing and archival-format conformance. |
| 9 | Extend deployment acceptance beyond this machine. | The current wheel passed clean-venv OCR/draft/approval/export/restore acceptance outside the checkout. Validate another machine, actual service/TLS setup and realistic concurrent workloads. The optional-Tesseract-disabled acceptance belongs to the earlier wheel. |
| 10 | Add the reference's wider platform capabilities. | Separate tested milestones for vector artwork, marketplace ingestion, offline mobile sync, external registries and advanced analytics. |

### Feature-by-feature improvement map

Use this map to choose a future improvement after trying the corresponding controls. These are proposed changes or further validation, not extra controls already available in the app.

| Feature you tried | Where to find it | How we can make it better |
|---|---|---|
| Accounts and permissions | Account name; administrator People & access | Add separately tested auditor/citizen/brand roles, district/state access boundaries and stronger sign-in options. |
| Camera and multiple images | Inspect → Package images | Validate physical phones and add guided face coverage with clearer missing-panel prompts. |
| Optional device coordinates | Inspect → Inspection details → Record device location | Validate consent wording, permission denial, GPS accuracy and retention policy on real field devices; consider role/jurisdiction controls before wider use. |
| Rotation, crop and zoom | Each image preview | Test touch gestures, HEIC and coordinate handling across devices; make the analyzed crop unmistakable. |
| Capture quality | Preview warnings and Measured image indicators | Calibrate warnings against human readability and show declaration-specific recapture guidance. |
| Saved capture drafts | Inspect → My saved drafts | The current installed release restored and resumed a saved draft. Extend interrupted-save and cross-device checks; design offline synchronization separately. |
| Processing and retry | Processing; Open progress | Measure realistic concurrent loads and improve long-running-job guidance without inventing progress percentages. |
| Capture lanes | Inspect → Capture source | Build separate marketplace URL, citizen submission and vector-artwork workflows beyond uploaded images. |
| Package facts and exemptions | Confirm package context; saved Package facts | Capture the remaining legally relevant exception facts and have category specialists review applicability. |
| OCR and primary declarations | Declarations extracted → field evidence | Improve rupee and difficult-print recognition; evaluate new brands, layouts and scripts using frozen independent annotations. |
| Dates and batch codes | Label intelligence | Expand independently annotated real close-ups and unfamiliar code formats while retaining unresolved date interpretations and original OCR. |
| Ingredients and allergens | Ingredient and allergen review | Validate complete multi-panel ingredient coverage, match types and multilingual statements. |
| Barcode and product identity | Inspection particulars; Product history | Evaluate more barcode types and integrate authorized registry checks; retain uncertainty in product matching. |
| Evidence and corrections | Evidence images; correction editors | Validate the delivered save, image-load and rectangle safeguards across touch devices; make comparison of original and corrected text easier. |
| Finding decisions | Findings → Reason & observed evidence | Improve case triage and navigation between unresolved findings while retaining reasons and evidence. |
| Rescans | Finding or intelligence card → Capture a close-up; Linked capture history | Validate the delivered focus/revision and comparison workflow on physical packages and interrupted connections; measure how often a better image resolves the specific field. |
| Submission and approval | Officer review & approval | Test complete journeys with two human reviewers and clarify contribution-based approval restrictions. |
| Physical type measurement | Measurement annexe; scale-card capture | Validate against physical measurements across devices, angles and supported package shapes. |
| Rule definitions | Rules | Complete official-source, amendment and category coverage with named legal sign-off. |
| Rule administration | Administrator Rule versions | Add visual differences and test-impact previews before version selection. |
| Reports and draft notices | Record header download links | Verify office-editor compatibility, archival conformance and authorized signing/service workflows. |
| Repository and history | Repository; Product history | Test large datasets; improve matching, saved filters and longitudinal quantity/price review. |
| Dashboard | Dashboard → dataset/date filters → Recorded inspection coordinates | The rounded point/table drill-down is delivered. Add an approved boundary layer, map accessibility research, geographic case comparison and richer trend analysis while preserving approved-result denominators, privacy and dataset separation. |
| Bench and Scenarios | Bench; Scenarios | Add difficult, legally reviewed cases and keep generated tests separate from measured real-photo accuracy. |
| Audit and evidence integrity | Audit log; Immutable revision history | Add an independently verifiable custody/export process and test operational recovery. |
| Installation, backup and API | Operations guide; authenticated API reference | The current release passed local clean installation and restore. Test another machine, HTTPS/service configuration and realistic workloads. |

For support, each page/API response includes an `X-Request-ID`. Service logs use that value to connect the browser request, queued background analysis and report events without recording uploaded text or paths. An unexpected server response shows the ID in a generic message. Give that ID to the deployment operator; it is a diagnostic reference, not a password or proof that an action succeeded. See the operations guide for retention and proxy-log requirements.

Follow **Inspect → Review evidence → Correct/rescan → Decide → Submit → Independent approval → Report → Repository → Dashboard**, then explore Bench and Rules. Use the feature map to choose the next improvement after trying each workflow.
