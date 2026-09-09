# Recorded demonstration results

## What is stored here

`manifest.json` maps the SHA-256 of a set of photographs to a recorded analysis
of that exact set. When those exact files are uploaded, the console returns the
recording instead of spending about forty seconds recognising them again.

Each `<name>.json` was produced by `scripts/record_demo_fixture.py`, which runs
the ordinary pipeline with replay switched off and stores whatever it decided.
Nothing in a recording is written or edited by hand. Re-produce any of them
with the same command; the result should match, and if it does not, the pipeline
has changed and the fixture should be re-recorded.

## Why this is not a fake result

Three things keep the distinction real, and all three are tested in
`tests/test_demo_fixtures.py`:

* **It fires on nothing else.** The key is the hash of every image in the set.
  A different pack, a re-saved JPEG, a crop, a subset, an extra frame -- none of
  them match, and all of them take the live path. A judge who photographs their
  own packet gets the real pipeline.
* **It says what it is.** The replayed analysis carries `demo_fixture`, the
  console shows a banner above the findings, and the first warning on the record
  names the recording and the date it was made. The provenance is a field on the
  saved inspection, so it survives into the JSON export and the PDF.
* **It expires.** The recording stores the rule pack version and the recogniser
  it was made under. If either has moved on, the fixture is refused and the live
  pipeline runs, because a stored result from an older pack is not what this
  build decides today.

`TULA_DEMO_FIXTURES=off` disables replay entirely. Use it to show a judge the
same photographs going through the live pipeline and reaching the same result.

## The photographs

`images/` holds five photographs of one retail packet of Nakoda Foods Laung Sev,
400 g, taken by the project team of a packet they bought. They are the subject
of a regulatory screening exercise, which is what a Legal Metrology inspector
does with a package in a shop.

This is a different case from
`data/evaluation/additional-real-labels/`, where two catalogue images from a
manufacturer's own media library carry an explicit no-redistribution licence and
are therefore untracked. Those are the manufacturer's artwork redistributed as
artwork; these are photographs of a physical object taken for inspection. The
distinction is recorded here so it is a decision rather than an oversight.
