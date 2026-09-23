---
name: campaign-review
description: Runs the isolated campaign shadow-ground-truth reviewer (tools/harvest_campaign_review.py) -- scans captures_campaign/ only, merges any answers copied from the review page's clipboard output into manual_truth.json, and regenerates the review page for whatever's newly captured or still unresolved. Use whenever the user asks to run/refresh the campaign review, process a new campaign capture, "run the campaign harvest", or wants to record their tooltip-read identification of a just-captured sub-68 mercenary's supports. This is a single fast command, not the full support-coverage-refresh pipeline -- never touches assets/harvested_supports/, manual_labels.json, tier_notes.json, or the reference embeddings.
---

# Campaign shadow ground truth review

One command, run from the project root:

```bash
python tools/harvest_campaign_review.py
```

Each run does two things in order:
1. Checks the clipboard for JSON copied from a previous
   `assets/campaign_review/review.html` session and merges any valid
   answers into `assets/campaign_review/manual_truth.json`.
2. Rescans `captures_campaign/` fresh (only that directory -- never
   `captures/` or any `__captures_*/` archive) and regenerates
   `review.html` for whatever's newly captured or still unresolved.

Safe to run repeatedly with nothing new to do -- it just reports
`0 need review` and exits.

## Why this is separate from support-coverage-refresh

That pipeline (`harvest_support_icons.py` + friends) is exactly what
this data is meant to audit -- candidate icon recognition, the
embedding classifier, the whole reference catalog. Running THIS
reviewer never touches any of that: candidates shown are anchored purely
to what the contributing skill(s) can structurally roll
(`definitions/supports_by_skills.json`), never to icon similarity, and
answers are recorded to a completely separate file
(`assets/campaign_review/manual_truth.json`), not `manual_labels.json`.
The point is an independent check, not another input to the same
system it's checking (see AI_RAMBLINGS.md's writeup on validating
low-level/Tier I coverage, which is currently almost entirely
self-confirmed by the classifier rather than checked against anything
independent).

## The live capture-and-review loop

Intended to run WHILE a campaign encounter is on screen, per capture:

1. In `capture_pipeline.py`, choose `[C]` at startup (or already running
   in campaign mode) so the capture saves to `captures_campaign/`
   instead of `captures/`.
2. Press the capture hotkey over the paused encounter.
3. Run this skill / `python tools/harvest_campaign_review.py`.
4. Open `assets/campaign_review/review.html`. For each crop, hover the
   real support in the game's own UI to read its exact name and tier,
   then click the matching button (candidates are pre-narrowed to that
   skill's real possible rolls) or type it if the wording differs
   slightly.
5. Click "Copy to clipboard".
6. Run this skill again -- it merges what you just copied and
   regenerates the page for anything left (including new captures taken
   since step 3, if more encounters happened in between).

## Reporting back

Summarize concisely: capture dirs found, skipped counts (if any --
worth a second look if non-zero, same "not a genuine gap" caution as
`harvest_support_icons.py`'s own skipped/dropped counts), how many were
merged from the clipboard this run, and how many still need review.
Don't re-derive the reasoning for why this stays separate from the main
harvest -- point to this file's own "Why this is separate" section or
`AI_RAMBLINGS.md` if the user wants the full history.
