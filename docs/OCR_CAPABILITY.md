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

---

# When the recogniser was not the problem

Measured 10 September 2026 on one photographed packet of Nakoda Ratlami Sev, three
frames, with `scripts/evaluate_arrow_layouts.py`.

The complaint that prompted this was that almost every rule returned
`INCONCLUSIVE` on real photographs even where the declaration was plainly
visible. The recogniser was not at fault. It read `NAKODA FOODS MARKETING PVT.
LTD.` at 0.98, `care@nakodafoods.com` at 0.98, `Product Of INDIA` at 0.96 and
`PKD ON:01-JULY-26` at 0.98. Everything downstream of that then found a reason to
distrust what it had been given.

## What was actually wrong

Each of these was independently sufficient to make most of the pack undecidable.

| Where | What happened |
|---|---|
| `analyse.py` | Any declaration held for review marked **its whole frame unreadable**, which flipped scan-level legibility and turned every unrelated presence check inconclusive — reporting "only 76 lines were legible" about a capture that read 76 lines fine. One false reading suppressed the entire scan. |
| `intelligence.py` | **Every two-digit year** was treated as unresolved. Indian packaging prints `JULY-26`. The century is resolved deterministically by the parser, so nothing was left open by it — but the date declaration, and every rule gated on dated applicability behind it, was undecidable on essentially every real pack. |
| `normalizers.py` | `BETWEEN 10.00 AM TO 6.00 PM`, on a line that also says `MFG. DATE`, parsed as **October 2000**. That phantom date conflicted with the real one and withheld the Rule 6(11), 6(1)(aa), 6(2) and Rule 3/26 gates. |
| `normalizers.py` | `made in a facility that processes peanuts` parsed as the country **"A Facility That"**, conflicting with `Product Of INDIA` on the same panel. |
| `normalizers.py` | The unit price `0.34 perg` did not parse: it carries no currency symbol of its own (it follows the MRP's `135/-`) and the recogniser closed the gap in `per g`. |
| `pipeline.py` | Two **cue-only fragments** with no date behind them — the words "MFG. DATE" inside a sentence asking customers to quote it, and a 0.02-confidence `MFD` fragment — outranked a packing date read at 0.98 and marked the event uncertain. |
| `pipeline.py` | A contact block's confidence and conflict were taken over **every** line swept beneath its heading, so an FSSAI licence number at 0.54 withheld a manufacturer name at 0.98, a street at 0.87 and a PIN at 0.96. |
| `layout.py` | `distance()` tolerates a candidate one character-height above the anchor, for side-by-side rows. Consecutive lines of a paragraph overlap by more than that, so an address block walked **upwards** into an oil-code legend, and a phrase split across a line break joined backwards. |
| `pipeline.py` | `Manufactured&Marketedby` — no spaces — matched no manufacturer cue. `CUSTOMER` / `CARE` split across two lines matched no consumer-care cue. |
| `context.py` | Nutrition exclusion was per-line and vocabulary-based. `SaturatedFat` is one token so `\bfat\b` misses it; `Salt (as NaCl)` is in no nutrient list; `12.30g` carries no nutrient word at all. Each was read as the pack's identity or its net quantity, and every pattern added to stop one promoted the next cell in the table. Replaced with a **region**: the table's extent is located from a few anchors, and membership is decided by geometry. |

## Result on the same three photographs

| | before | after |
|---|---|---|
| Declarations correct | 3 | **11** |
| Held as review candidates | 3 | 0 |
| Abstained | 9 | 1 |
| **Wrong** | **0** | **0** |
| Rules decided (of 17) | 5 | **11** |

Every remaining undecided rule now names one specific thing that would settle
it: confirm the commodity category, confirm import status, confirm the package
shape, or re-photograph with the scale card. The console lists those as actions
with a count beside each, derived by re-running the pack with the fact assumed
rather than from a hand-written table.

One declaration is still correctly withheld: the consumer-care telephone
number's digits genuinely have competing OCR readings, and showing an officer
one of two conflicting numbers is worse than showing none.

## The one reading that needed more than a fix

The net quantity `400g` is inkjet-coded and scores 0.540, below the 0.55 floor,
on a pack whose printed arrows are out of register — following the arrow from
`NET QUANTITY` lands on the next field's value. Neither the score nor the layout
settles it. What settles it is the label's own arithmetic: Rule 6(11) makes the
unit sale price the retail price over the net quantity, so an MRP of 135 against
a printed 0.34 per gram can only be a 400 g pack. `_price_corroborates_quantity`
checks that the reading falls inside the interval the printed unit price's
rounding admits, and records the corroboration on the declaration. Two
independent sources agreeing is not a guess; one faint reading on its own is.

## Regression

Unchanged on both retained sets, run the same day:

* ten photographs — 14 selected exact, 12 accepted exact, 15 candidate recoveries, **0 incorrect accepted**
* four photographs — 10 of 14 literal, 12 of 14 candidates, 4 of 5 structured, **0 wrong accepted**

## Limits of this measurement

**One package.** Three frames, one annotator, unblinded, annotated before the
runs were scored. This characterises a specific failure mode on a specific
layout; it is not an accuracy claim and the denominator is far too small to be
one. The regression sets above are what guard against having traded their
correctness for this one. Reproduce with:

```powershell
python scripts/evaluate_arrow_layouts.py --out out/arrow-layout-<date>
```

---

# Reading the scale off the package's own barcode

Added 10 September 2026. `metrology.from_barcode`, wired into `analyse` as scale
source (c) and into the scale-free sweep's bounds.

Rule 7(2) is in millimetres; a photograph has none. Until now the only automatic
scale was the declared quantity plus a bulk-density prior — which fails exactly
where it is most needed, because on inkjet-coded flexible packaging the declared
quantity is the thing that could not be read.

Almost every retail package carries a second object of regulated size: its
barcode. EAN-13 and UPC-A are 95 modules between the outer bar edges, EAN-8 is
67; the module is 0.330 mm at nominal magnification; and GS1 permits retail
point-of-sale symbols between 80% and 200% of nominal. Measuring the symbol's
pixel width therefore brackets millimetres-per-pixel, with a k=2 interval equal
to exactly the range the standard permits.

Measured on the demonstration pack: the EAN-13 spans 273 px, giving
**0.0919–0.2297 mm/px**. Independent of the quantity prior, so the two fuse; the
sweep bracket they imply is intersected rather than replaced, and brackets that
disagree widen the sweep instead of narrowing it.

**What it buys.** Where the quantity is unreadable the sweep would run 15–600 mm;
the barcode narrows it to about 48–185 mm, a tenfold reduction, and a narrower
sweep decides packages a wider one must abstain on. Where the quantity *is*
readable — as on this pack — the prior is often tighter (74–180 mm here) and the
intersection changes nothing. It is insurance for the hard case, not an
improvement on the easy one.

**What it cannot buy.** It is Tier C and stays Tier C. `R8.1` and `R8.2` require
Tier B, so this can never sustain a height violation; the engine's tier gate
enforces that whatever any estimate claims. A bracket from a printing standard
is evidence about labels in general. A prosecution needs a measurement of this
label, which means the scale card in the frame.

Rotated symbols are rejected: the horizontal span of a tilted barcode is
foreshortened, and a foreshortened ruler reports a package smaller than it is —
the direction that invents shortfalls.
