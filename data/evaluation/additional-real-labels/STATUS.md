## Redistribution and version control

The two Nestlé photographs (`nestle-india-tin.png`, `nestle-india-box.jpg`) and the tin crop
`visual-crops/can-raw-900-1500.png` are **excluded from version control**. Their source terms permit
extracts for private non-commercial use but not unrestricted dataset redistribution, and publishing a
repository is redistribution. They remain on the original working machine so
`scripts/evaluate_additional_labels.py` can be reproduced there; the retained results, annotations,
hashes and source manifests in this repository record what was measured without republishing the
pixels. Re-acquire them from the URLs in `additional-sources.json` under the same terms if the
evaluation must be repeated elsewhere.

The two Wikimedia Commons photographs remain tracked: CC BY-SA 3.0 and CC BY-SA 4.0 permit
redistribution with the attribution recorded in `sources.json`.

# Frozen annotations — baseline and uncertainty rerun completed

Four real photographs were visually inspected and annotated before OCR on 7 September 2026. The first actual OCR/extraction evaluation and a subsequent rerun after the uncertainty safeguards were frozen have now completed. Both used the unchanged originals and annotations; the source and model versions for each run are retained with its evidence.

`annotations.v1.json` freezes **14 readable literal targets**: seven date fragments, three codes, two rupee amounts and two unit prices. **Five targets qualify for normalized structured scoring**: the B-prefixed `103A` code, prices 600 and 775, and unit prices 1.26/g and 1.94/g. The freeze timestamp and annotation SHA-256 are recorded in `FROZEN.json`: `3d8047ca20bbf2d1f1f58e6d999da559a59dd666d0e5944dd4ada31a024ce0f8`.

`FROZEN.json` preserves the historical pre-OCR checkpoint, including `ocr_has_run: false`; it is not the current evaluation status. No annotation expectations were changed after seeing OCR output.

| Completed run | Literal selected / any same-pass candidate | Structured accepted exact | Wrong accepted among five structured targets | Structured abstentions |
|---|---:|---:|---:|---:|
| [First actual baseline](../../../out/additional-real-label-evaluation/REPORT.md) | 9/14 / 9/14 | 4/5 | 0/5 | 1/5 |
| [Rerun after frozen uncertainty changes](../../../out/additional-real-label-evaluation-after-uncertainty/REPORT.md) | 9/14 / 9/14 | 4/5 | 0/5 | 1/5 |

Both prices and both unit prices were accepted exactly. The explicit batch `103A` remained unrecovered. Literal recognition is unchanged: three of seven date fragments, two of three codes, both prices and both unit prices were recovered. These small, fixed denominators do not establish general accuracy or validate all emitted fields.

The rerun improves review handling: the Canadian date-cue fragment `AUANT` is retained as a brand candidate with no accepted name, and the tin's literal OCR error `0CT/26` now requires source-image review. No corrected `OCT/26` reading or expiry meaning was invented. The brand change is a qualitative correction outside this dataset's five structured targets. All seven date fragments remain literal-only; **no normalized-date accuracy is scored**.

Full per-photo OCR candidates, source boxes, extraction provenance, scores and hashes are retained in the [baseline results](../../../out/additional-real-label-evaluation/results.json) and [rerun results](../../../out/additional-real-label-evaluation-after-uncertainty/results.json). The [baseline findings](../../../out/additional-real-label-evaluation/FINDINGS.md), [before/after findings](../../../out/uncertainty-fix-validation/FINDINGS.md) and [verification receipt](../../../out/uncertainty-fix-validation/VERIFICATION.json) document failures, limitations and unchanged source/model/input checks. The earlier ten-photo dataset is reported separately; its denominators are not merged into these results.

| Original photograph | Source context | Provenance and reuse limits |
|---|---|---|
| `best-before-canada.jpg` | Canadian-labelled box; product origin is not visible | Photographer CambridgeBayWeather, Wikimedia Commons, CC BY-SA 3.0; details in `sources.json` |
| `manufacture-expiration.jpg` | Product market/origin unknown; source camera location in the UAE is provenance only | Photographer Gaurav Dhwaj Khadka, Wikimedia Commons, CC BY-SA 4.0; details in `sources.json` |
| `nestle-india-tin.png` | Published for India by Nestlé India | Copyright Nestlé or its licensors; site terms permit private noncommercial extracts, not unrestricted dataset redistribution; details in `additional-sources.json` |
| `nestle-india-box.jpg` | Published for India by Nestlé India | Same source and reuse limits as the tin photograph; details in `additional-sources.json` |

The Nestlé photographs already contain publisher-added red rectangles around codes. Those markings are retained and may make recognition easier. No local source-image alteration or generated imagery was used. Files in `visual-crops` are plain inspection aids, not additional photographs or evaluation inputs.

Date fragments with ambiguous ordering, century or manufacture/packing/expiry meaning remain literal-only targets. Unlabelled codes are not assigned a batch role from the source web page. No net quantity is visible, and none is inferred by dividing prices. Unknown or cropped fields are excluded rather than counted as correctly absent. These are single-agent, unblinded development annotations, not independently adjudicated truth or legal/safety verdicts.

**Both actual evaluations are complete; no evaluation process is in flight.** The bounded changes addressed general uncertainty and identity acceptance without changing the frozen targets, supplying expected text to recognition, or retraining a model. Recognition failures remain visible in the retained evidence.

The original source bytes, URLs, attribution, dimensions, timestamps and hashes are retained in the two source manifests. Two earlier Commons downloads returned HTTP 429; Open Food Facts discovery was blocked by robots.txt; the FSSAI document attempt returned HTTP 403. These blocked sources were not retried and are not evaluation inputs. Applicable attribution/license conditions remain in effect; none of the sources endorses Tula.
