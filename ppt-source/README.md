# Deck source

Everything behind `SIH2026-Tula-PS26034-Idea-Presentation.pptx`.

## Rebuilding

`build_deck.py` opens the official template, deletes its instructions page and
draws all six slides as native PowerPoint vector shapes, so nothing is a
screenshot and every element stays editable and sharp in the PDF.

    python ppt-source/build_deck.py

The three values only the SIH portal can supply are constants at the top of
`build_deck.py` -- `TEAM_NAME`, `TEAM_ID`, `THEME`, and `BADGE` for the oval on
slides 2-6. Set them and re-run, or just type over them in PowerPoint.

Requires `python-pptx`.

## Editable diagram sources

The slides use native shapes, but the same two structures are kept here as
draw.io sources. To open either: draw.io -> Arrange -> Insert -> Advanced ->
Mermaid, then paste the file contents.

- `01-verdict-decision-flow.mmd` -- how one photograph becomes one of seven
  verdicts, and which gate can stop it. Slide 2's central argument drawn in
  full, including branches the slide compresses.
- `02-pipeline-architecture.mmd` -- the six-stage pipeline and the evidence
  store beneath it, with per-stage components spelled out.
