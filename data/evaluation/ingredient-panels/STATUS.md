# Ingredient panels — a set the other real-photo sets could not provide

The two existing real-photo sets are front-of-pack shots and code close-ups. Not one of their
fourteen photographs shows an ingredient list, so neither of them can exercise ingredient reading
or allergen screening at all. This set exists to close that gap.

Seven photographs, all from Wikimedia Commons under licences that permit redistribution with
attribution, so unlike the two Nestlé photographs these can stay in version control and the
evaluation reproduces from a public checkout. Attribution is recorded in `sources.json` and must
travel with the images. None of the photographers or publishers endorses Tula.

## Redistribution

| Photograph | Author | Licence |
|---|---|---|
| `scrapple-can-back.jpg` | Loudfan | CC BY-SA 4.0 |
| `white-pudding.jpg` | O'Dea | CC BY-SA 4.0 |
| `marrowfat-peas.jpg` | Not separately identified on the file page | CC BY 4.0 |
| `tomato-soup.jpg` | Not separately identified on the file page | CC BY 4.0 |
| `confectionery-tartrazine.jpg` | Sokolikmawwer0 | CC BY-SA 3.0 |
| `tortilla-chips.png` | Cindyy28 | CC BY-SA 4.0 |
| `habanero-condiment.jpg` | Xyzerb | CC0 |

## Protocol

Every photograph was opened and read by eye, and its ingredient text and expected allergen outcome
written into `annotations.v1.json`, before `scripts/evaluate_ingredient_panels.py` existed. One
lexicon change was made between reading the images and the first run — the word `dairy` was added
to the milk category, because `habanero-condiment.jpg` carries a facility statement worded
"contains dairy, tree nuts, and eggs" and the lexicon had no entry for it. That change is recorded
inside the annotations rather than buried in a passing number: this set has never scored the
lexicon as it stood before.

Single annotator, unblinded, seven photographs. Not independently adjudicated truth, and far too
small for an accuracy claim.

## Results

Run `python scripts/evaluate_ingredient_panels.py`; full per-photograph output including the
ingredient text actually recovered is retained in
[`out/ingredient-panel-evaluation/results.json`](../../../out/ingredient-panel-evaluation/results.json).

| | count |
|---|---:|
| Photographs | 7 |
| Ingredient statement recovered by OCR | 3 |
| Annotated allergen events | 5 |
| Recovered with the correct evidence kind | 1 |
| Recovered with the wrong evidence kind | 1 |
| Missed | 3 |
| **False positives** | **0** |
| **Matches on a `must_not_report` list** | **0** |
| Negative controls clean | 4 of 4 |

### What the failures actually were

Every one of the five misses and mis-classifications is a recognition failure, not a screening
failure. On each label where the ingredient text was recovered, the screen reported what the
annotation said it should.

* **`scrapple-can-back.jpg` — gluten missed.** OCR recovered eight fragments from the whole can:
  `Reese`, `SORAPPLE`, `SERVING SUGGESTIONS`, `with eggs.`, `INGREDIENTS`, `spices.`,
  `DISTRIBUTED BY`, `U.S.A.` The heading was read; the list beneath it was not. Aged, low-contrast
  print on a curved surface photographed at a distance.
  Worth noting on the credit side: `with eggs.` comes from the *serving suggestion* above the
  ingredient heading, and was correctly not reported as an ingredient.
* **`habanero-condiment.jpg` — milk found, but called an ingredient rather than cross-contact;
  tree nuts and egg missed.** The facility line came back as
  `lity that contains dairy, tree 9" 0९%`. The word `facility` was truncated to `lity`, so the
  cross-contact cue did not fire and the match was classified as an explicit declaration. `tree`
  survived but `tree nuts` did not, and matching a bare `tree` would be wrong. No cue vocabulary
  was added afterwards to rescue this: nothing that would have helped was missing, the text was.
* **`tortilla-chips.png` — no ingredient statement read.** OCR returned
  `HOLEGRANWHITECORNWTECOR` and similar: word boundaries lost, and the `INGREDIENTS:` prefix
  dropped entirely, so there was no cue to anchor a block. A 471 x 97 crop of curved print.
* **`tomato-soup.jpg` and `confectionery-tartrazine.jpg` — nothing reported, by design.** The
  ingredient cue recognises English and Devanagari. A Dutch, Czech or Russian block is not read.
  The tomato soup does carry a real allergen list (`bevat tarwe (gluten), ei, gerst (gluten),
  soja, selderij`), and none of it is reported. Intended behaviour, and still a limitation.

### The honest headline

The deterministic lexicon did not produce a single false positive across seven real labels, and it
did not fall for either trap the annotations set for it — a serving suggestion that names eggs and
butter, and a `GLUTEN FREE` badge. What it cannot do is read text the recogniser did not return.
On this set the allergen screen is bounded by OCR, not by its vocabulary.

## What this set still does not cover

Every photograph is a European or North American package. No comparably licensed photograph of an
Indian ingredient panel was found, and Open Food Facts — the obvious source — disallows API access
in its `robots.txt`. Indian labels print the same allergen vocabulary in English, but a set drawn
from them would be the better test and this one should not be described as one.
