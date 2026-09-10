# Arrow layouts — labels that point at their values

Indian flexible packaging routinely prints the mandatory declarations as a
column of labels joined to a column of values by arrows:

    NET QUANTITY  ---->  400 g
    BATCH NO.     ---->  A-26-01-07-26

The variable data is inkjet-coded separately from the pre-printed labels, so the
two drift out of vertical register. On the pack in this set the value column sits
roughly one row above the labels, which means following an arrow from a label
lands on the **next field's** value. The recogniser also merges such a row into a
single line, so a label arrives welded to a value that belongs to another rule.

This set exists to characterise that failure and to hold the fix honest.

## Redistribution

**The images are deliberately untracked.** They are photographs of a commercial
retail package; the label artwork is the manufacturer's copyright and no
redistribution licence has been established, so `data/evaluation/arrow-layouts/images/`
is git-ignored. `annotations.v1.json` and `scripts/evaluate_arrow_layouts.py`
are tracked, so the protocol and the expected values reproduce from a checkout
even where the pixels cannot. Supply your own photographs at those filenames to
re-run it.

## Protocol

Declarations were read by eye from the original photographs and written into
`annotations.v1.json` **before** any extraction run was scored. One annotator,
unblinded, one package. This is far too small for an accuracy claim and must
never be quoted as one; its denominators are kept apart from the real-photograph
sets in `out/real-label-evaluation-*` and from the generated benchmarks.

## Outcomes

Four outcomes are scored, because they are not equally bad:

| outcome | meaning |
|---|---|
| `correct` | the declared value was recovered and may feed a finding |
| `candidate` | the declared value was recovered but held for review — the officer is shown the right number and asked to confirm it |
| `abstain` | nothing usable was recovered |
| `WRONG` | a value was reported and it is not the one on the package |

Only `WRONG` is a true failure. An abstention costs an officer a correction; a
wrong reading costs a wrongly cleared or a wrongly accused package.

## Result

Run `python scripts/evaluate_arrow_layouts.py`.

| | correct | candidate | abstain | WRONG |
|---|---:|---:|---:|---:|
| before | 3 | 0 | 9 | 0 |
| after | 3 | 3 | 6 | 0 |

Three declarations moved from "nothing recovered" to "the right value, shown to
the officer for confirmation". Nothing became wrong. The net quantity is not
promoted to a finding, and should not be: it is a 0.54-confidence reading on a
package whose own printed arrows point at the wrong values, so confirming it is
the officer's call.
