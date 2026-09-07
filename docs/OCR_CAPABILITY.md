# What the recogniser can and cannot read

Measured 7 September 2026 with `scripts/benchmark_difficult_labels.py --out out/ocr-audit-2026-09-07`.
Twenty-four generated labels carry known printed strings; recovery below is exact normalised
value-string recovery of five fields — net quantity, MRP, manufacturing date, expiry date and batch
code. **This measures recognition of generated print, not legal accuracy and not field extraction.**
Real photographs are reported separately in [the user manual's testing section](USER_MANUAL.md#15-tests-actually-performed);
their denominators are not merged into these.

Engine: RapidOCR 1.4.4, PP-OCRv4 detection and recognition exported to ONNX, CPU only.

## Headline

**105 of 109 known printed values recovered (96.3%).** Every failure is in one case.

Two columns matter and they are not the same thing:

- **found** — the value appears in the recognised text at all.
- **trusted** — it also survives the confidence and conflict filter, so it may feed a finding.
  A value that is found but not trusted is retained as a review candidate; it cannot establish a
  violation or an absence on its own. That gap is the system working, not a defect.

| Case | Found | Trusted | Notes |
|---|---:|---:|---|
| clean | 5/5 | 5/5 | |
| low_resolution | 5/5 | 4/5 | MRP held for review |
| tiny_text | 5/5 | 1/5 | four fields held for review |
| tilted_13_degrees | 5/5 | 5/5 | |
| rotated_90 | 5/5 | 5/5 | |
| rotated_180 | 5/5 | 3/5 | |
| rotated_270 | 5/5 | 5/5 | |
| **dot_matrix_mrp_dates** | **5/5** | 1/5 | dot pitch 3; all four coded fields held for review |
| **severely_sparse_dot_matrix** | **1/5** | 1/5 | dot pitch 4 — **the one real failure** |
| mixed_hindi_english | 6/6 | 4/6 | Devanagari net quantity recovered |
| misaligned_declarations | 5/5 | 5/5 | cue and value on different baselines |
| stacked_keyword_value | 4/4 | 4/4 | keyword and value on separate lines |
| blurred | 5/5 | 5/5 | Gaussian σ 2.5 |
| low_contrast | 5/5 | 5/5 | |
| shadows | 5/5 | 5/5 | strong lighting gradient |
| glare | 5/5 | 5/5 | specular highlight over the price |
| complex_background | 5/5 | 5/5 | noise plus sinusoidal texture |
| perspective | 5/5 | 5/5 | off-axis capture |
| curved_baseline | 5/5 | 5/5 | cylindrical pack warp |
| missing_mrp | 4/4 | 4/4 | |
| ambiguous_numeric_date | 5/5 | 5/5 | `08/09/26` retained as ambiguous |
| multi_side_front / _back | 2/2, 3/3 | 2/2, 3/3 | |
| blank | — | — | control: nothing recognised, nothing invented |

## The three things people ask about

**Misaligned characters — handled.** `misaligned_declarations` puts the cue and its value on
different baselines and horizontal offsets; `stacked_keyword_value` separates them onto different
lines entirely. Both recover fully and neither needs review, because the spatial layout pass
associates a cue with its value by position rather than assuming they share a line.

**Geometry and poor visibility — handled.** Tilt, all four cardinal rotations, perspective, a curved
cylindrical baseline, blur, glare, shadow gradients, low contrast and a textured background all
recover 5/5. Small text recovers 5/5 but correctly drops most fields to review.

**Dot-matrix MRP and dates — handled at normal density, fails when very sparse.** This is the honest
split, and it matters because inkjet-coded MRP, MFG, EXP and batch codes are near-universal on
Indian packaging.

At dot pitch 3 — typical drop-on-demand coding — all four coded fields are read *exactly*:

```
conf 0.54  review  "MRP Rs 180.00"
conf 0.54  review  "MFG 08/2026"
conf 0.54  review  "EXP09/2027"
conf 0.54  review  "BATCH AB1234"
```

Every one is correct, and every one is flagged for review at confidence 0.54. That is the intended
behaviour: correct, but not trusted enough to convict without an officer looking.

At dot pitch 4 — faded, worn or low-density coding — it breaks:

```
conf 0.54  review  "βP Bs 1S0CC"      (MRP Rs 180.00)
conf 0.54  review  "BFG 0572926"      (MFG 08/2026)
conf 0.54  review  "0923"             (EXP 09/2027)
conf 0.54  review  "BATCHAB1224"      (BATCH AB1234 — one digit wrong)
```

**It fails safe.** Nothing is accepted as a declaration, all four regions are marked as conflicting
candidates, and the capture warning names the cause and the remedy:

> Many disconnected small ink marks suggest dot-matrix printing, damaged characters or image noise.
> Capture a sharper close-up of the stamped MRP/date/batch.

A wrong batch code (`AB1224` for `AB1234`) reaching a notice would be worse than no reading at all,
so the design refuses it. The targeted-rescan workflow exists for exactly this: request a close-up of
that one declaration and re-read it.

## Why the current fix does not go further

A 2×3 morphological close before upscaling already bridges one-pixel printing gaps
(`ocr/preprocess.py:reconnect_ink_variant`), which is why pitch 3 succeeds. Wider kernels were
measured against the failing case on 7 September 2026:

| Bridging kernel | Fields recovered | Effect |
|---|---:|---|
| none (raw) | 0/4 | `βP Bs 1S0CC` |
| 2×3 (current) | 0/4 | `B4RP Bs 1SOCC` |
| 3×3 | 0/4 | `BFG 052026` |
| 3×5 | 0/4 | closest structurally: `MFG0572026 EXP092327 BATCH A81214` |
| 4×4 | 0/4 | `MFG52026` |
| 3×7 | 0/4 | degrades clean text: `Manuftctured`, `Simple Foodks` |
| 5×5 | 0/4 | `BAT:H A812.4` |

Larger kernels recover the *shape* of the fields but never the exact digits, and they corrupt text
that currently reads perfectly. **No kernel change was adopted**: it would trade a correct refusal
for a confidently wrong reading. This is a recogniser limitation, not a preprocessing one.

## What would actually close the gap

In priority order, for a future version.

1. **Fine-tune the recognition model on synthetic dot-matrix print.** This is the right use of the
   project's training budget and it does not violate the CPU-only inference constraint: PP-OCRv4's
   recognition head can be fine-tuned on GPU and re-exported to ONNX, leaving the serving path
   unchanged. The training data already exists in this repository — `tula.labgen` composes labels
   with known ground truth and `benchmark_difficult_labels.py` renders the dot-matrix degradation,
   so labelled examples at every pitch, fade level and dot density can be generated in bulk.
   Coder fonts are a small, closed visual domain; this is the highest-yield change available.
2. **Constrain the decoder for coded fields.** Once a region is located — and detection succeeds even
   when recognition fails — MRP, MFG, EXP and batch have tight grammars: digits, `/`, `-`, a
   two-digit month, a two- or four-digit year. Beam search restricted to that alphabet and shape
   collapses the search space and rejects readings like `0572926` that no date can take. This needs
   access to per-character logits, which the current RapidOCR wrapper does not expose, so it means
   calling the ONNX recognition model directly.
3. **Learned super-resolution before recognition** on the located coded region only. Restricting it to
   one small crop keeps the CPU cost tolerable; applying it to a whole 4 MB photograph would not.
4. **Ask for a better photograph.** Already implemented, and for a worn code on a physical package it
   is sometimes the only correct answer.

## Limits of this measurement

These are generated labels with a single font family and a synthetic dot-matrix model. Real coded
print varies in ink bleed, substrate absorption, curvature and wear in ways this does not reproduce.
The five-field denominator is small and fixed. Nothing here is a held-out benchmark, an independent
accuracy estimate, or evidence about any real product. Reproduce with:

```powershell
python scripts/benchmark_difficult_labels.py --out out/ocr-audit-<date>
```
