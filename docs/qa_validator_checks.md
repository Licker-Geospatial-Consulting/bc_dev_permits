# Dashboard QA - validator checks

Reference for the automated data-quality checks run by the internal **Review mode** on the permit
dashboard (`tools/dashboard/`). They are computed client-side over the loaded `permits.json` when a
reviewer enters Review mode (the `runQA()` function in `dashboard.template.html`).

The checks are a **triage layer, not pass/fail**: they rank the most-suspect permits so a human looks
at them, and some will have false positives (the thresholds are deliberately loose). "No flag" does
not mean "verified"; it means "nothing tripped".

## What counts as "flagged"

A permit is in the flagged queue (the `Flagged only` filter, the `X / Y reviewed` progress) when **any**
of these is true:

1. `needs_review` - the harvester itself asked for review, or
2. `extraction_confidence < 0.60` - low parse confidence, or
3. **one or more validator checks below fired.**

In the grid's **Flags** column: a **number** = how many validator checks fired (sort descending to
triage these first); a **dot** = flagged only by `needs_review` / low confidence, with no validator
check. Rows with a validator flag also get a coloured left stripe (red = a high-severity check).

## The validator checks

| # | Check | Severity | Fires when | Why / what to look for |
|---|-------|----------|-----------|------------------------|
| 1 | **Height / storeys ratio** | high | `height_m / number_of_stories` is **< 2 or > 6** m per storey (typical is ~2.5-4.5) | A ratio far outside this usually means `height_m` or `number_of_stories` was mis-parsed - e.g. a *variance* amount captured as the height, or storeys read wrong. Check the source text / drawings. |
| 2 | **Unit mix does not sum to total** | medium | `unit_mix` values sum to a number **different from `units_total`** (rows with a `single-family-lot` entry are skipped, since lots are not counted in `units_total`) | Either the mix or the total is wrong, or a unit type is missing from the mix. |
| 3 | **Floor area per unit out of range** | medium | For **residential** permits, `floor_area / units_total` is **< 15 or > 600** m2/unit (typical is ~30-200) | Usually a unit/area mismatch, or a mixed-use row where the floor area is commercial but the units are residential (so the ratio is meaningless). |
| 4 | **Floor-area statistical outlier** | high | `floor_area` is above **Q3 + 3 x IQR** for its development class (computed per class over the whole dataset) | A genuine mega-project, or a unit bug - most often **square feet stored as square metres** (an ~10x inflation). Confirm against the source. |
| 5 | **Point outside municipal boundary** | high | The mapped point falls **outside the municipality's boundary polygon** (with a ~1 km tolerance, and only for exact - non-geocoded - points) | A geocoding error, or a reference/approximate coordinate that landed in the wrong place. |

Notes:

- A **completeness check** - "residential permit with no units, floor area or unit mix" (low severity)
  - exists in the code but is **commented out by default**, because it is noisy (many early-stage
  rezonings legitimately have no quantities yet). Un-comment it in `runQA()` to include it.
- The harvester's own `needs_review`, low-confidence and approximate-location signals are **not** added
  as validator flags; they are shown on each card's Source line and still put the permit in the
  flagged queue (see above).

## Using the checks in Review mode

- **Flags column (grid)** - sort descending to work the highest-confidence problems first.
- **Checks section (card)** - lists the exact messages for the selected permit, with severity colour.
- **Conflicts section (card)** - any stored HTML-vs-PDF disagreement ("page says X, document says Y").
- **Stats view** - distribution histograms; click a bar to open exactly those permits in the grid, and
  the "Largest" list names the extreme-value permits directly.
- **Mark reviewed + comment** - persists locally and is written to `review_actions.json` on **Export**,
  which is what gets handed back to the pipeline.

## Tuning

All thresholds are inline in `runQA()` (checks) and `isFlagged()` (the flagged set) in
`tools/dashboard/dashboard.template.html`: the 2-6 m/storey band, the 15-600 m2/unit band, the
`Q3 + 3*IQR` fence, the 0.60 confidence cutoff, and the ~0.01 deg (~1 km) boundary tolerance.
