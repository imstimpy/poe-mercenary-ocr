---
name: support-coverage-refresh
description: Refreshes this project's support-icon pipeline -- rebuild the harvested support-icon catalog from every real capture, regenerate the missing/weak-coverage report, re-validate the experimental image classifier's accuracy, and (optionally) generate a local review page for manually identifying crops the pipeline couldn't resolve on its own. Use this whenever the user asks to refresh, rerun, update, or check support icon coverage or the support catalog, wants the latest numbers on which (icon, tier) combinations are still missing, asks how many supports are covered, wants to know the classifier's current accuracy, or wants to manually identify/label unresolved support crops -- especially after new captures have been added to captures/ or the __captures_* archives. Also trigger on phrasing like "re-harvest supports", "update the support coverage report", "how's support coverage looking now", or "help me identify these unknown supports".
---

# Support coverage refresh

Four scripts. Steps 1-3 always run in that order, each depending on the
previous one's output; step 4 is optional and only needed when the user
wants to manually identify still-unresolved crops. None of them take
arguments. This whole pipeline is read-only with respect to
`capture_pipeline.py` -- nothing here touches production code or gets
wired into the live capture path.

**Before running step 1, check `git status` on `assets/harvested_supports/`
if there's any chance manual identification work is in progress.** This
step hard-deletes and rebuilds every top-level PNG in that folder from
`captures/` + `tier_notes.json` + `manual_labels.json` -- it does NOT
read whatever is currently sitting in the folder. A real incident this
project hit: the user manually renamed several `unlabeled_*.png` files
to their identified names directly on disk (not through
`manual_labels.json`), then this step ran again and silently deleted
that work with no way to recover it (the folder wasn't committed to
git at the time). If in doubt, ask before running step 1, or confirm
`manual_labels.json` already has whatever the user identified.

## Why this exists

`assets/harvested_supports/` is a labeled reference catalog built from
real `warrant.txt` ground truth (the exact copied in-game item text,
not OCR) -- see `AI_RAMBLINGS.md`'s "Warrant-text alignment for
skill/support ground truth" for the full history. It goes stale every
time new captures are added, since those captures might resolve
previously-missing (icon, tier) combinations or add more validation
data for the experimental classifier. This skill is the "make it
current again" button.

## Steps

### 1. Rebuild the catalog

```bash
cd tools
python harvest_support_icons.py
```

Scans every capture in `captures/` and every `__captures_*/` archive,
using each one's `warrant.txt` as ground truth first (falls back to
inferring from `warrant_generated.txt` only when no real `warrant.txt`
exists). For crops text-narrowing alone can't finish resolving, it also
cross-checks `capture_pipeline.match_support_icon()` (the same
classifier already validated at 100% in step 3) against the
structurally-possible candidates, trusting it only when the two agree
-- real impact the first time this ran: 71 of 108 stuck crops resolved
with zero human input. Writes to `assets/harvested_supports/`
(`manifest.json` plus one reference image per resolved `(icon, tier)`
key, with near-duplicate noise filed under `variants/`).

**Known gotcha**: this step deletes and rebuilds `assets/harvested_supports/variants/`
before repopulating it. On this machine, OneDrive sometimes holds a
lock on a subdirectory inside `variants/` and the script's own
`shutil.rmtree` call fails with `PermissionError: [WinError 5] Access
is denied`. If that happens, don't treat it as a real bug -- clear the
lock yourself and retry:

```powershell
Remove-Item -Path "assets\harvested_supports\variants" -Recurse -Force -ErrorAction Stop
```

(PowerShell's `Remove-Item` succeeds against this lock more reliably
than Python's `shutil.rmtree` does.) Then re-run
`harvest_support_icons.py` from `tools/`. This can take more than one
try if OneDrive is actively syncing -- keep retrying `Remove-Item` /
`harvest_support_icons.py` until it completes cleanly.

Capture the run's own summary numbers (capture directories scanned,
unique crops found, how many resolved from ground truth vs. inference
vs. still ambiguous) -- they go in the final report to the user.

### 2. Regenerate the coverage report

```bash
python generate_support_coverage_report.py
```

(Still in `tools/`.) Reads the manifest `harvest_support_icons.py` just
wrote, plus `definitions/supports.json` / `skills_by_mercenary.json` /
`supports_by_skills.json`, and rewrites `assets/support_icon_coverage.md`
-- which `(icon, tier)` combinations are covered, which are still
missing, and for each missing one, an example (skill, mercenary) to
hunt for plus a breadth number (how many distinct skills could roll
it -- the actual difficulty signal, since a support offered by 100
skills is far easier to stumble into than one offered by 2).

Capture the printed `N/159 covered, M missing` line for the final report.

### 3. Re-validate the experimental classifier

```bash
cd ..
python tools/match_support_icon.py
```

(Back at the project root -- this script imports `capture_pipeline`,
which resolves its own asset paths relative to the current directory,
so it must run from the root, not from `tools/`.) Loads the reference
catalog from step 1, then re-derives validation crops directly from
every real `warrant.txt`-backed capture (independent of which capture
happened to become a given key's reference image) and reports accuracy.

This is a standalone report/validation tool, not production code --
nothing it does affects what `capture_pipeline.py` actually recognizes
during a live capture. Don't wire its output into anything; just report
the numbers.

Capture the "Overall (icon + tier)" and "Icon family only" accuracy
lines, the "Tier-badge secondary check" line (helped/hurt/net -- a
second-opinion check that reads the roman-numeral badge directly
rather than relying on the whole-crop embedding, since that alone
under-reads tier specifically), and the misclassification breakdown
(same-icon-wrong-tier vs. genuinely-different-icon), for the final
report.

### 4. Manual identification (optional -- only when asked)

```bash
python generate_manual_labeling_page.py
```

(In `tools/`.) Reads the manifest step 1 just wrote, generates
`assets/harvested_supports/manual_review.html` -- a local, offline
page (never published anywhere; these are real GGG game assets, see
`THIRD_PARTY_NOTICES.md`) showing every still-unresolved crop next to
clickable buttons for its already-narrowed candidate list. Tell the
user to open it directly in their own browser (relative image paths
only resolve correctly from that folder). It produces JSON to paste
into `manual_labels.json` -- merge it in by hand or ask them to paste
the result back to you, then re-run step 1 to pick up the new labels.
Never suggest renaming the crop files directly instead -- that's the
exact mistake the warning above is about.

## Reporting back

Summarize concisely, in this shape:

- Harvest: capture dirs scanned, unique (icon, tier) crops found, how
  many resolved from ground truth vs. still ambiguous/inferred.
- Coverage: `N/159 covered, M missing` (and whether this changed from
  before, if the user has a prior number to compare against or if
  `assets/support_icon_coverage.md`'s git history / this session's
  memory has one).
- Classifier: overall accuracy and icon-family-only accuracy, and
  whether misclassifications are tier-only confusions (expected, a
  known limitation -- see `AI_RAMBLINGS.md`) or something new.

Don't re-derive or re-explain the full analysis history behind these
numbers -- `AI_RAMBLINGS.md`'s "Warrant-text alignment for skill/support
ground truth" and "Local embedding model as the eventual
support-matching approach" sections already have it. Point there if the
user wants the why, not the what.
