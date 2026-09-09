# Which duties this pack screens, and which it does not

Two different questions get asked about coverage, and they have different
answers. This document separates them.

1. **Does the pack screen every mandatory declaration in Rule 6?** Yes.
2. **Does the project hold every instrument listed on the Department of
   Consumer Affairs legal-metrology page?** No — 13 of roughly 40.

Neither answer is improved by blurring them together. The rule pack is
`2026.09.10-expanded-1`; the source audit behind it is
[LEGAL_SOURCE_MATRIX.md](LEGAL_SOURCE_MATRIX.md).

## 1. The mandatory declarations

Rule 6(1) lists what a retail package must declare. Every item has a rule, and
several items have more than one because a single clause carries more than one
duty — which is why the findings list looks weighted towards quantity and
price. Rule 6(1)(c) is both "declare a quantity" and "declare it in the
prescribed unit symbol"; Rule 6(1)(e) is "declare a price", "declare only one",
and "say it includes taxes". Those are five separate things an inspector checks,
and five separate things a package can get wrong.

| Duty | Clause | Rule |
|---|---|---|
| Maker's or packer's name and address | 6(1)(a) | `LMPCR.R6.1.A.MANUFACTURER` |
| Country of origin, imported goods | 6(1)(aa) | `LMPCR.R6.COUNTRY_OF_ORIGIN` |
| Common or generic name | 6(1)(b) | `LMPCR.R6.1.B.GENERIC_NAME` |
| Net quantity | 6(1)(c) | `LMPCR.R6.1.C.NET_QUANTITY` |
| Prescribed unit symbol | 6(1)(c) + General Rules | `LMPCR.R6.1.C.UNIT_SYMBOL` |
| Month and year of manufacture/packing | 6(1)(d) | `LMPCR.R6.1.D.DATE` |
| Retail sale price declared | 6(1)(e) | `LMPCR.R6.1.E.MRP_PRESENT` |
| Price stated inclusive of taxes | 6(1)(e) + 2(m) | `LMPCR.R6.1.E.MRP_FORM` |
| Only one retail price | 6(3), 18(2A)/(3) | `LMPCR.R6.1.E.MRP_SINGLE` |
| Consumer-care contact | 6(2) | `LMPCR.R6.1.F.CONSUMER_CARE` |
| Unit sale price declared | 6(11) | `LMPCR.R6.1.F.UNIT_PRICE_PRESENT` |
| Unit sale price computed correctly | 6(11) provisos | `LMPCR.R6.1.F.UNIT_PRICE_ARITHMETIC` |
| Declarations grouped on one panel | 2(h), 8(1) | `LMPCR.R7.GROUPED` |
| Minimum letter height by panel area | 7(2), Table I | `LMPCR.R8.1.MIN_HEIGHT` |
| Minimum height of quantity numerals | 7(2), Table I | `LMPCR.R8.2.NETQTY_HEIGHT` |
| Hindi or English | 9(4) | `LMPCR.R9.3.BILINGUAL` |

Four further screens go past presence to the *form* a declaration takes, and to
one duty a food package owes under its own labelling regime:

| Duty | Clause | Rule |
|---|---|---|
| Quantity measured by weight, volume, length or number | 6(1)(c) + 13 | `LMPCR.R6.1.C.DIMENSION` |
| Price stated in Indian currency | 6(1)(e) | `LMPCR.R6.1.E.CURRENCY` |
| Address carries name, locality and PIN | 6(1)(a) + 10 | `LMPCR.R10.ADDRESS_COMPLETE` |
| Food declares a best-before / use-by date | 6(1)(d) referral; FSS Labelling 2020 | `LMPCR.R6.1.D.EXPIRY_FOOD` |

The last is gated on a **confirmed** commodity category and stays `UNVERIFIED`
until an officer supplies it. That is deliberate: whether a best-before duty
exists at all is a food-law question, and a packaged-commodities screen that
guessed the commodity was food would be inventing the duty it then enforces.

Two rules are deliberately inert and keep their identifiers so historical
records still resolve: `LMPCR.R9.2.NOT_ON_BOTTOM` (the cited clause does not
create a bottom-placement offence) and `LMPCR.R11.MRP_ROUNDING` (the rounding
provision's commencement chain is unresolved — see below).

### Size and height are screened, and usually cannot be decided from a photograph

`R8.1` and `R8.2` are implemented against the full Table I, including the 2.0 mm
first blown/formed cell from the November 2017 corrigendum. They ask whether the
printed lettering is tall enough for the panel's area, which is a question in
millimetres — and a photograph contains no millimetres. So they need either

* the printed scale card in frame, which makes the capture Tier B and lets the
  measurement carry a violation; or
* `src/tula/rules/scale_free.py`, which sweeps every package size the declared
  quantity permits and reports a result only where the answer is the same across
  the whole range. Where it is not, it says nothing rather than guessing.

Both also need the package shape confirmed, because the statutory principal
display panel of a cylinder is not its photographed rectangle.

## 2. The instruments on the DoCA page

The Department's index lists roughly 40 items — the 2011 Rules and their
amendments, corrigenda, guidelines, advisories and standard operating
procedures. **Thirteen are individually retrieved and audited** as sources
`S2`–`S14` in the source matrix: the base Rules, the 2015, 2017 (with
corrigendum), 2021, unit-price-basis, extension-to-31.12.2023, 06.10.2023,
24.10.2025, 02.12.2025, 13.02.2026, 27.04.2026 and 29.05.2026 instruments.

**Roughly 27 are not.** Not retrieved: the three 2011 amendments and their
corrigendum, both 2011 Guidelines, both 2012 amendments, the 2013 amendment,
both 2014 amendments, the 2016 amendment, the garments/hosiery advisory, five of
the six 2022 amendments, seven of the nine 2023 amendments, the fuel-capacity
and agricultural-produce advisories, the 10.07.2023 medical-device provisions,
and the edible-oils standard operating procedure.

### Which of those gaps could change a screening outcome

Most cannot. The Guidelines are implementation guidance, the fuel-capacity
advisory concerns service manuals, and the edible-oils SoP is a weighing
procedure. Two gaps are real:

* **The Rule 6(11) commencement chain.** Unit-price commencement was deferred
  repeatedly across 2022 and 2023 — precisely the amendments not held. The
  `effective_from` of `2024-01-01` on both unit-price rules is inferred from the
  ends of that chain (`S8`, `S9`). It is probably right; it is asserted, not
  proven, and it is live in two rules.
* **Consolidation.** The 2012–2016 amendments touched Rule 6 and the Schedules,
  so "base 2011 plus 2015 plus 2017" is not a guaranteed faithful consolidation.

Closing those two means retrieving and auditing roughly 14 documents, not 27.

## What a passing screen does and does not mean

A rule that returns `PASS` has passed the narrow screen described in its own
message, on the evidence captured, under the pack version recorded on the
finding. It is not a certificate for the package, it does not decide duties
under the Food Safety and Standards Act, the Drugs and Cosmetics regime, the
Medical Devices Rules or State excise law, and it does not establish who is
liable. Independent legal sign-off on the pack remains open.

---

# Can the lettering height be measured automatically?

This is the question the height rules keep raising, so it is worth answering
squarely. **A photograph contains no absolute length.** Nothing in software can
change that: the same pixels are a large pack far away or a small pack close up.
Every route to millimetres is a route to some object of known size in the frame.

There are four, and only the first two need nothing from the officer.

| Route | Needs | Assurance | What it can decide |
|---|---|---|---|
| The package's own barcode | nothing | Tier C | advisory; a clearance the sweep proves size-invariant |
| Declared quantity + density prior | a readable net quantity | Tier C | same |
| The printed LM Scale Card in frame | the card | **Tier B** | a violation |
| Device depth / ARCore | a depth-capable phone | **Tier B** | a violation |

## What now happens with no help at all

`metrology.from_barcode` reads the scale off the barcode. EAN-13 and UPC-A are
95 modules wide, EAN-8 is 67, the module is 0.330 mm at nominal magnification,
and GS1 permits retail symbols between 80% and 200% of nominal. Measuring the
symbol's pixel width therefore brackets millimetres-per-pixel — an interval,
not a number, whose k=2 span is exactly the range the standard permits.

That bracket does two things:

1. It **fuses** with the declared-quantity prior in `metrology.fuse`. They are
   independent — one comes from a printing standard, the other from a density
   assumption — so together they are tighter than either, and disagreement
   between them inflates the error bar rather than being hidden.
2. It **narrows the scale-free sweep**. `scale_free` decides a height rule
   without any scale by testing it across every package size the evidence
   allows and answering only where the answer is the same throughout. On a pack
   whose quantity cannot be read, the sweep would otherwise run from 15 mm to
   600 mm; the barcode narrows that to roughly 48–185 mm, a tenfold reduction,
   and a narrower sweep is decisive on packages a wider one must abstain on.

On the demonstration pack the barcode bracket is *wider* than the quantity prior
(48–185 mm against 74–180 mm), so intersecting them changes nothing there. It
earns its place on the packages where the quantity is the thing that could not
be read — which is the common case on inkjet-coded flexible packaging, and the
case the whole extraction effort above was about.

## What it deliberately cannot do

The barcode route is Tier C and stays Tier C. Rules `R8.1` and `R8.2` require
Tier B, so a barcode-derived scale can never sustain a height violation, and the
tier gate in `rules/engine.py` enforces that regardless of what any estimate
claims. A bracket derived from a printing standard is evidence about labels in
general; a prosecution needs a measurement of *this* label.

So the honest answer to "can it be automatic?" is: **the measurement, yes, and
it now is. The conviction, no.** For that an officer puts the printed scale card
— one page, printed once, carried in a folder — in the frame, and the capture
becomes Tier B. That is not a gap in the software. It is what it costs to be
able to say a number in court.
