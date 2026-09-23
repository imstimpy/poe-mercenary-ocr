# AI Ramblings

This file carries implementation context that isn't captured anywhere else in
the repo — real analysis, dead ends, and decisions from the project's
development history that would otherwise only exist in chat transcripts. The
audience is an implementor (human or agent) picking this project back up, not
an end user — the various `README.md` files stay human-consumable summaries;
the reasoning and raw findings behind them live here. It is NOT a restatement
of what's already well-documented in code docstrings; read those first:

- `README.md` — what this is, requirements, how to run it.
- `mercenary_tracker_plan.md` — the original phased build plan.
  **Caution: this file is explicitly stale.** It describes gem detection as
  "Phase 3: presence only, no reference library needed" — the project has
  since gone through a full identity-matching investigation (raw
  correlation → position search → perceptual hashing → ORB) and landed on
  a presence+type-lookup stopgap, none of which is reflected in that
  document's phase descriptions. Trust `assets/README.md` and this file
  over the plan's phase status.
- `assets/README.md` — the current status of gem/currency/scarab/skill
  reference assets and what's actually live in production. Short and
  structural by design; the full gem identification investigation (what was
  tried and failed, with real numbers) that used to live there has moved to
  this file's "Gem identity/presence matching" section below.
- `tests.py`'s module docstring — what each test suite actually validates
  and why, including which suites are blocking vs. informational.
- Every non-obvious constant/threshold in `capture_pipeline.py` has an
  inline docstring explaining the real data behind it (e.g.
  `GEM_PRESENCE_THRESHOLD`) — read those before changing a number that
  looks like it could just be "tuned."

## Gem identity/presence matching: full research history

Moved here from `assets/README.md`, which now carries only the short,
current-status version of this (see its "Known limitations" and "What's
actually live" sections). Everything below is the investigation that led
there — dead ends, real numbers, and reasoning, kept for whoever picks this
back up.

### Reference asset sourcing

Known-good icon assets are used in `match_icon` as references for
`capture_pipeline.py`'s image recognition. There are three possible sources:
in-game screen captures, official GGG CDN assets, and game asset
extractions. Comparing same-same in-game screen captures (same resolution,
same source) affords the "simplest" comparison path via direct pixel
comparison, but is the least portable — any difference in graphics settings
(resolution, AA, lighting, etc.) between the client computer and the
original source affects recognition. Official assets or game-extractions add
portability but require resolution scaling and/or conceptual comparison —
gems in particular receive additional rendering effects (e.g. a gold glow)
making same-different comparisons demanding. Preliminary checks show
official GGG CDN assets (pre-composed artwork for GGG websites) are worse
matches than in-game captures. Game-extracted currency/scarabs/skills all
appear to be rendered without additional effects (what you extract is what
you see in-game); gems are composed from multiple layers then receive
additional rendering effects in-game that aren't stored as art and can't be
recreated outside the game engine; skill supports are unverified.

#### `compose_gem_reference.py` was silently clipping real detail off composites

Found by direct visual inspection, not a test failure: `dark_bargain_of_trarthus.png`
and `spectral_throw_of_trarthus.png` in `assets/pending_gems/` (composited
from raw sprites `skeletalchains.png`/`ghostlythrow.png`) were visibly
missing parts of the gold overlay symbol -- a skull/bone shape and a
ghostly hand/claw shape respectively, both looking cut off at the edges
compared to what a real capture shows. Root cause, confirmed directly:
`compose_gem_reference()` sized its output canvas to the base gem
piece's own dimensions (`composite = base.copy()`), on the assumption
(true for Siege Ballista, the one gem this script was originally built
and validated against) that the base is always the larger of the two
sprite-sheet pieces. False for both of these real gems -- their overlay
is taller (Spectral Throw's is also wider) than their base, which made
the centering offset go negative in that dimension. `Image.alpha_composite`
doesn't error on a negative/out-of-bounds offset -- it silently clips
whatever falls outside the canvas, so real overlay pixels were being
lost with no warning of any kind, for as long as this script has existed.

**Fixed**: the output canvas is now sized to whichever piece is larger in
each dimension (not just the base), with both pieces centered
independently within it -- see the script's own docstring for the exact
mechanism. Re-composited both real cases and confirmed visually: both
now show the complete overlay shape with no visible clipping. Also
corrected the script's default output directory (`assets/gems` ->
`assets/pending_gems`) to match how these composites are actually
organized in practice (pending/not-yet-validated, same as every other
extracted-but-unvalidated asset) -- the old default was stale relative to
real usage. Regression-tested at the time (asserting the output canvas
is never smaller than either source piece) using the two real sprites on
hand -- see the "Update" below for why that test was later removed
again.

**Update: all 12 raw sprite sheets are now on hand (see
`assets/gem_raw_asset_names.json`) and every composite has been
re-verified.** Re-ran `compose_gem_reference.py` (fixed version) for all
12 gems and visually inspected each output. Real result: **the clipping
bug was NOT limited to the original 2 -- most of the 12 were affected**,
some substantially:

```
raw asset             composite      base           overlay        clipped under OLD script?
chainstrikegem        51x60          49x60          51x57          no (overlay smaller both dims)
heavystrike            70x60          49x60          70x52          YES -- width (70 vs 49, 21px)
shockwaveslam          59x60          49x60          59x59          YES -- width (59 vs 49, 10px)
rainofblades           47x53          47x52          46x53          YES -- height (1px)
blastraingem           47x52          47x52          46x45          no
crossbowtotemgem       50x52          47x52          50x33          YES -- width (3px, minor)
spectralspiralgem      66x52          47x52          66x51          YES -- width (19px)
thrownshield           47x62          47x52          43x62          YES -- height (10px)
ghostlythrow           48x59          47x52          48x59          YES -- both dims (already documented)
skeletalchains         50x65          50x56          47x65          YES -- height (already documented)
stormcall              50x61          50x56          46x61          YES -- height (5px)
purge                  60x64          50x56          60x64          YES -- both dims (10px/8px)
```

10 of 12 were losing real overlay detail, not just the 2 originally
reported -- this was a systemic bug affecting most of the gem set, not
an edge case. All 12 re-composited and visually confirmed clean (full
overlay shape visible, no truncation) after the fix. Still not live in
production either way (`match_icon`'s identity matching isn't what
`process_capture()` actually uses -- see "Disambiguation strategies"
below) -- but any PAST finding in this file that scored one of these
composites before this re-run (the sparkle-based `pending_gems`
composites referenced throughout the embedding-model and
compositing-approach sections below) was almost certainly built on a
clipped input, given how common the bug turned out to be. NOT the
separate "no-sparkle" composites from a different project's own sprite
sheets discussed just below, which used that project's own distinct
3-panel layout and comparison code, not this script.

**Update: the `tests.py` regression suite for this (`run_gem_composite_regression_test`,
initially 2 cases then expanded to all 12) was removed again.**
`compose_gem_reference.py` is a standalone offline help script for
sourcing gem reference candidates, not part of the live capture
pipeline, and none of its output is used in production (see above) --
running it once, fixing a real bug, and visually confirming the fix was
the valuable part; a permanent suite re-checking a tool that isn't
otherwise being touched was assessed as test noise rather than ongoing
protection worth the upkeep. The fix itself is unaffected -- this only
removed the regression test, not the code change.

#### Open finding: a sparkle/glow overlay is real, and required, but not sufficient on its own

The in-game rucksack slot renders a warm sparkle/glow effect over every
Trarthus gem, which the raw extracted sprite doesn't include at all
(the sprite is the bare item art, no slot-presentation effects). A
separate 80x80 sparkle asset (`assets/pending_gems/sparklebackground.png`)
was found that visually matches this glow
closely, and confirmed empirically to matter: compositing a gem sprite
*without* it produces close to zero discrimination between different
real gems (correct-gem and wrong-gem match scores collapsed to within
0.01 of each other) -- adding the sparkle backdrop and searching for
the correct alignment recovered a real margin (~0.067) between the
correct and an incorrect gem.

That margin is still below the 0.94/0.025-margin threshold
`match_icon()` currently requires, and only appeared after a manual
position search -- a single fixed (e.g. centered) placement of the
sprite relative to the sparkle was not enough. In other words: this
isn't yet a "drop the composited PNG in and go" workflow. It likely
needs the same multi-scale/position search already tracked as a TODO
(see `mercenary_tracker_plan.md`) before an extracted-asset reference
can reliably replace a screen-captured one, rather than being a
separate, smaller gap on its own.

#### Follow-up: a real, DERIVED glow signal (not an approximation) can be isolated by differencing against a real capture, once properly aligned

Rather than guessing at an overlay asset (`sparklebackground.png`) and
adding it, the reverse is also possible: take a real capture (which
already has the true glow) and a precisely-known glow-free composite
(the other reviewed project's exact 3-panel crop, no approximation
needed), align them properly, and subtract to isolate exactly what the
glow contributes.

Alignment matters enormously here -- a real test on Spectral Helix
found the best-fit scale was 0.78x the composite's native size, not
1.0x. Correcting for that alone raised the real-vs-composite score from
0.86 (naive same-size overlay) to 0.9153 -- within ~0.025 of
`match_icon()`'s 0.94 floor, using nothing but the multi-scale search
already validated for gem presence above. Once aligned, the pixel
difference (real minus composite) is a clean, physically coherent
signal, not noise: the sparkle dots appear at exactly the positions
visible in the real capture's background, plus a subtler warm tint
across the gem's own silhouette (the bare composite reads slightly
cooler/darker than the real capture everywhere, not just at the edges).

This is a real, DERIVED glow specific to this one real capture, not a
general-purpose overlay yet (n=1: one gem, one real sample). Two
things would need checking before treating it as one: (1) does this
same derived glow, added to a DIFFERENT gem's bare composite, improve
that gem's match score too (a generalization test this project hasn't
run) -- if the glow is a shared game-engine effect applied identically
to every Trarthus gem, it should; (2) does it hold up across multiple
real captures of the same gem, given the real same-gem variance already
documented above. Worth pursuing before the sparkle-overlay question is
either confirmed or abandoned for good -- this is meaningfully
different evidence than the original approximated `sparklebackground.png`
attempt, using a real signal instead of a guess.

## Known limitation: this system is calibrated to one specific machine

`is_gem_present()` (the presence check that's actually live in
production -- see "Disambiguation strategies" below) still compares
pixel intensities under the hood (normalized cross-correlation against
the known gem references), so it inherits the same underlying
sensitivity: different GPU/driver, texture filtering, anti-aliasing,
color profile/calibration, display scaling, or in-game graphics
settings can all shift the exact pixel values a screenshot captures,
even at identical resolution and even for the literal same icon in the
same game. It's a coarser question than identity matching (see above
for why that distinction matters), so it needs less precision to stay
reliable -- but it isn't immune to this category of difference, just
more tolerant of it.

Practical implication: every screen-captured reference image in this
folder is implicitly calibrated to the machine that captured it, the
same way `definitions/mercenary_regions.json`'s pixel coordinates are
calibrated to one screen resolution/UI scale. This system was built
for, and has only been validated against, a single player's own setup
-- it isn't expected to transfer cleanly to a different machine without
recalibrating (i.e. rebuilding the reference set from that machine's
own captures) any more than the region coordinates would transfer to a
different resolution. Noted here as a real, understood constraint
rather than something currently being engineered around.

## Disambiguation strategies

**In use now**: presence detection + mercenary-type lookup (the
stopgap -- see `is_gem_present()`/`resolve_gem_presence()` in
capture_pipeline.py). `match_icon()`'s fine-grained identity matching
no longer determines production Gem 1-4 output. This resolves 10 of 12
gems with no image-matching risk at all (a mercenary's rucksack can
only contain its own signature gem); the two-gem Blade Ambusher case
is left as `"Unknown (Blade Ambusher)"` rather than guessed -- see
`tests.py` suite 5, a standing TODO for this specific case.

**Update: the Blade Ambusher case is no longer a standing TODO.**
`disambiguate_blade_ambusher_gem()` now resolves it via embedding-based
image matching (see "Adopted: embeddings for gem PRESENCE" above, the
"Follow-up: the one remaining known failure..." entry) -- `"Unknown
(Blade Ambusher)"` is now only the fallback for a genuinely unresolved
reading (margin too tight), not the default outcome. All 12 gems now
resolve: 10 via risk-free type lookup, 2 via image matching.

**Confirmed: the Blade Ambusher case can't be resolved via skills/
warrant text either, only image matching.** `definitions/
skills_by_mercenary.json` shows Blade Ambusher's (and Infamous Blade
Ambusher's) `SecondaryCount` is 2 against a Secondary pool of exactly
2 entries -- "Spectral Throw of Trarthus" and "Spectral Helix of
Trarthus" -- so every single Blade Ambusher always has BOTH signature
skills equipped, confirmed directly against a real warrant (Eli, the
Contemptible). Unlike every other mercenary where the skills list
narrows things down, here it carries zero disambiguating information
for which gem actually dropped -- ruling out a shortcut around real
image identification for this specific case.

**First real Spectral Throw of Trarthus sample obtained** (previously
zero real captures existed anywhere in the project -- only the
unvalidated game-extracted composite in `pending_gems/`), from a real
Blade Ambusher rucksack (`captures/20260912_163151/rucksack_bottom_right.png`,
now also `assets/gems/spectral_throw_of_trarthus.png` and
`test_data/gems/spectral_throw_of_trarthus.png`). First real
`match_icon()`-style score against the actual disambiguation target:

```
1.0000  spectral_throw_of_trarthus  (self)
0.9387  bladefall_of_trarthus
0.8934  spectral_shield_throw_of_trarthus
0.7468  blast_rain_of_trarthus
0.7430  spectral_helix_of_trarthus
0.7319  sunder_of_trarthus
0.7128  siege_ballista_of_trarthus
```

**Update -- validated against real same-gem variance, not just one
reference photo.** A second, older capture archive (`__captures_20260910/`)
turned up 6 more independent real Spectral Helix captures (all bottom_left
rucksack quadrant, confirmed genuinely distinct files, not duplicates of
the existing reference) plus a second independent Bladefall capture.
That's enough to check the finding above against real same-gem spread
instead of a single sample on each side:

- Spectral Helix same-gem variance (7 independent real captures, 21
  pairwise comparisons): tightly clustered `0.9824-1.0000`.
- Spectral Throw vs. EVERY one of those 7 real Helix samples:
  `0.7352-0.7430` -- remarkably consistent, and the gap down from
  Helix's own same-gem floor (0.9824) is enormous (~0.24). The clean
  separation isn't a fluke of which one Helix reference happened to be
  used -- it holds across real same-gem variance on the Helix side.
- Spectral Throw vs. BOTH independent Bladefall samples: 0.9387,
  identical to 4 decimal places both times. The near-collision isn't an
  artifact of one particular Bladefall reference photo either -- it's
  reproducible, confirmed on the Bladefall side (still n=1 on the
  Spectral Throw side -- a second independent Throw sample would be the
  next thing to get, to check the collision isn't one-sided).

**Conclusion so far**: for THIS specific pair, plain normalized
cross-correlation (the exact mechanism `match_icon()` already uses)
separates Spectral Throw from Spectral Helix cleanly and reliably --
better than the general "same-gem/different-gem overlap" finding above
would predict. The real blocker to using this for the Blade Ambusher
case is architectural, not that the pixels are indistinguishable:
production only runs `is_gem_present()` today, never `match_icon()`'s
identity check (see "In use now" above). The Bladefall near-collision is
a separate, real, reproduced risk worth watching for specifically in
future Blade Ambusher/Bladecaster captures, independent of whether the
Helix/Throw disambiguation itself gets built.

**Update: the second independent Spectral Throw sample this section
called for has arrived, from live production use, not a manual hunt.**
Capture `20260914_091453` (Cal, the Woebegone, a real Blade Ambusher
encounter) was correctly resolved end to end by the live pipeline --
`is_gem_present_in_rucksack` flagged `rucksack_bottom_right`, and
`disambiguate_blade_ambusher_gem` correctly called it
`spectral_throw_of_trarthus` -- with neither function tuned against
this specific capture beforehand. Added to
`test_data/gems/variance/spectral_throw_of_trarthus/` and wired into
`tests.py` (`_blade_ambusher_disambiguation_cases`, `_spectral_throw_samples`,
`_bladefall_samples`) so both the disambiguation suite and the collision
watch below auto-scan it. Real result: the disambiguation suite is now
9/9 (was 8/8, all Helix-side), and the collision watch's WORST margin
across all 2x2 Throw x Bladefall pairs is `0.0557` (this new sample vs.
the canonical Bladefall reference) -- essentially confirming the original
single-sample estimate (`0.0613`), not revealing it was too pessimistic.
**The collision is real and reproducible, now checked from both sides
with more than one sample each** -- correct ranking every time, but
consistently thin.

**Follow-up: multi-scale/position search (`_icon_similarity_score_multiscale`,
validated for presence detection and shown to fix a real Siege Ballista
identity-ranking failure -- see this file's "Open action items" below) does NOT
help this specific collision, and narrows the margin slightly.** Tested the real Spectral
Throw session (glued whole rucksack, real neighboring content) against
both independent Bladefall samples:

```
                        fixed-position   multi-scale/position
throw-vs-self:              1.0000            0.8455
throw-vs-bladefall:         0.9387            0.8069
margin:                     0.0613            0.0386
```

The ranking stays correct (Throw still outscores Bladefall) and the
result is reproducible (identical `0.8069` against both independent
Bladefall samples, same pattern as the original fixed-position
finding) -- but the margin got smaller, not larger, under the method
that helped Siege Ballista. **Real, useful negative data**:
multi-scale/position search is not a uniform improvement across every
case -- it fixed a framing/alignment problem for Siege Ballista, but
for this pair the collision looks like a genuine visual similarity
(both green/gold gems, similar overlay style) that a better alignment
search doesn't resolve, since better alignment probably helps a
correct match and a near-miss collision roughly equally when the
underlying content really is similar. Don't assume multi-scale search
generally widens margins -- check each case.

### Embedding-model evaluation (ResNet18, first real test)

Following the evaluation plan in this file's "Local embedding model"
section: `torch`/`torchvision` were confirmed installable in this
environment (a prior blocker -- see below -- turned out to be
specific to an earlier sandbox, not a general constraint). A frozen,
ImageNet-pretrained ResNet18 (no fine-tuning -- penultimate-layer
features, compared by cosine similarity) was tested against real data
in two separate ways, with two different outcomes:

**In-domain differentiation (real captures only, no game assets):
clean, strong separation.** Every real mss-captured gem sample
currently in the project (27 same-gem pairs across all 7 gems with
more than one real sample, 126 different-gem pairs) was embedded and
compared:

```
Same-gem pairs      (n=27):  0.9601 - 1.0000
Different-gem pairs (n=126): 0.7240 - 0.8745
```

Zero overlap -- every different-gem pair scores below the same-gem
floor. This extends the Spectral Throw/Helix result above to the full
real dataset: embeddings discriminate real gems from each other at
least as cleanly as `match_icon()`'s existing normalized cross-
correlation does for real-vs-real, with no meaningful improvement
demonstrated over what's already in production for this specific
comparison (both approaches already separate real captures cleanly;
this doesn't fix anything that was broken).

**Cross-domain (real capture vs. the `pending_gems` composite): fails
the same way ORB did.** Same-gem cross-domain scores (Helix:
`0.7520-0.8004`, Throw: `0.6881`) sit BELOW different-gem same-domain
scores (`0.8366-0.8745`) -- the exact failure this project's ORB
investigation already documented ("matching a real capture against a
game-extracted asset of the same gem scores lower than real-vs-real
different-gem comparisons"). Overlapping ranges, by this project's own
standard, means "doesn't work" -- a legitimate negative result, not a
setup mistake. **Caveat**: this used the existing `pending_gems`
composite, built with `compose_gem_reference.py`'s sparkle overlay --
see below for why that composite itself is now suspect, independent of
which comparison method is thrown at it.

### A different project's compositing approach suggests the sparkle overlay was the wrong fix

While investigating the missing gold-glow overlay (it isn't sitting
alongside the other gem 2D art -- may not be a static sprite at all,
possibly a shader/particle effect with no `.dds` to extract), a
separate, independently-built PoE mercenary-tracking tool (downloaded
by the project owner for reference, not part of this repository --
its own bundled PoE game assets belong to Grinding Gear Games the same
way ours do, so nothing from it is copied into this repo; only the
*technique* is described here, the same way `match_icon()`'s own
min_score/min_margin thresholds already cite "a similar independently-
built PoE mercenary tool" as their source without reproducing it) was
reviewed for comparison. Two things stood out:

1. **Its raw extracted gem sprite sheets are a clean 3-panel strip**
   (glyph | blank | colored stone, each panel a square, sheet width =
   3x height) -- and it composites by cropping the stone panel as the
   base canvas and alpha-compositing the glyph panel on top. No sparkle,
   no glow, no third asset of any kind. Its own thresholds for the
   resulting match are 0.94 min_score / 0.025 min_margin -- identical
   to `match_icon()`'s, which is either the same source `match_icon()`'s
   docstring already credits, or independent convergence on the same
   numbers.
2. Its own docs are honest that this is thin: "Positive real-image
   coverage remains small; review unfamiliar gem matches as the dataset
   grows" -- one confirmed real gem match in its own test suite, not a
   broad validated set. Its approach shouldn't be assumed correct just
   because it ships.

Re-tested directly against our real data anyway, since that's a
stronger bar than either project has independently cleared: composited
the same way (stone + glyph, no sparkle) from that project's own raw
sprite sheets, scored with THIS project's own `_icon_similarity_score`
against every real capture on hand.

```
                          same-gem          best different-gem
Helix composite:      0.8615 - 0.8822        0.7182 - 0.7382   -- real separation, ~0.12-0.14 margin
Throw composite:      0.7520 (n=1)           0.8520 - 0.8618   -- WRONG DIRECTION (scores higher vs real Helix than vs its own gem)
Bladefall composite:  0.7813 (n=1)           0.8495 - 0.8663   -- WRONG DIRECTION (same failure)
```

Compared to our existing sparkle-based `pending_gems` composite, the
no-sparkle version scores dramatically higher against real Helix
captures (`0.8615-0.8822` vs. `0.6407-0.6505` -- roughly +0.22) and
against real Throw (`0.7520` vs. `0.6252`) -- strong evidence the
sparkle overlay (or at least this project's specific approximation of
it, `sparklebackground.png`) was making the composite worse, not
better, and the "sparkle is required" conclusion in the Open Finding
above should be treated as unconfirmed rather than settled.

But the no-sparkle version isn't a clean win either -- it works for
Helix (real separation, though still below `match_icon()`'s 0.94
absolute floor) and fails outright for Throw and Bladefall, where the
composite scores HIGHER against the wrong real gem (Helix) than
against its own. Suspect cause, not yet confirmed: this project's
comparison functions (`_icon_similarity_score` et al.) do a single
fixed-position, fixed-scale (48x48) comparison with no sliding-window
search, while the reviewed project's matching does exactly the
deferred multi-scale/position search this project's own plan (see
`mercenary_tracker_plan.md`) has never implemented -- scanning a whole
region across many scales via `cv2.matchTemplate` rather than resizing
two same-size images and comparing once. A generically gem-shaped blob
at the wrong scale/alignment could plausibly produce exactly this kind
of scale-independent "everything looks like Helix" confound. Worth
confirming with real multi-scale/position search before trusting either
the promising Helix result or the Throw/Bladefall failures as final.

**Bottom line**: neither the sparkle-overlay hypothesis nor the
embedding-model hypothesis is confirmed to solve the cross-domain gap
on its own. What did change today: the sparkle overlay looks more like
part of the problem than the fix, and the missing piece may well be the
multi-scale/position search this project has deferred since its
original plan, not a different comparison algorithm.

### Embeddings for gem PRESENCE (not identity) -- a real, validated fix candidate for the third GEM_PRESENCE_THRESHOLD misfire

Separate from the identity-matching work above: the third real
false-positive `GEM_PRESENCE_THRESHOLD` misfire predicted in that
constant's own code comment happened (see `capture_pipeline.py`) --
capture `20260912_214055`'s Exalted Orb (`rucksack_top_left`, real
currency, not a gem) scored 0.7568 under raw pixel correlation,
outright ABOVE the 0.755 threshold. Combined with the existing real
gem floor (0.7541, `test_data/gems/top_left.png`), the real gem and
real non-gem ranges now OVERLAP -- no threshold value separates them
anymore. A failing regression test was added
(`test_data/currencies/exalted_orb.png`, caught automatically by
`run_gem_presence_tests()`).

The same frozen ResNet18 embedding approach evaluated above for
identity matching was tried on this different, coarser question
(is this ANY gem, not which one) against every real gem/non-gem
sample currently in the project:

```
Real gem floor (embedding):        0.9250  (test_data/gems/top_left.png)
Real non-gem ceiling (embedding):  0.7568  (test_data/scarabs/scarab.png)
Exalted Orb specifically:          0.6585  -- not close, cleanly below the non-gem ceiling
```

Real separation, ~0.17 margin -- and it happens to also fix the OTHER
standing known failure at the same time: `test_data/gems/top_left.png`
(a real gem) currently fails `is_gem_present()` under pixel correlation
(a pre-existing, separately-tracked bug -- see `tests.py`'s "Failing"
list) but scores a comfortable 0.9250 under embeddings, safely clear of
every real non-gem score.

**Update -- this was not an isolated near-miss, and the scale of the
real problem is much bigger than one threshold nudge away.** Prompted
by a second unrelated false positive (`20260913_050511`, a currency
item scoring 0.7827 against a mercenary type that DOES have its own
gem, so this one silently logged a wrong-but-plausible `Storm Call Of
Trarthus` rather than being caught by the "no gem pool" safety net),
every capture in both archives was scanned programmatically for
GUARANTEED false positives (`is_gem_present() == True` on a mercenary
type with no gem pool at all -- structurally impossible to be real).
That search alone turned up 6 more, previously undiscovered:

```
captures/20260911_123214, top_left       (Bloodletter, no gem pool)   0.7613
captures/20260912_164442, bottom_right   (Swiftblade, no gem pool)    0.7826
__captures_20260910/20260910_061237, bottom_right (Bladebitter)       0.7561
__captures_20260910/20260910_073517, top_left     (Eruptor)           0.7812
__captures_20260910/20260910_143227, top_left     (Withertouch)       0.7812
__captures_20260910/20260910_144153, top_left     (Striker)           0.7613
```

Not scattered noise -- two visual patterns repeat: a bronze/copper
mask-like currency item appears 3 times, and the exact same silvery
swirl orb from the `20260913_050511` incident appears twice more.
All 8 (these 6 plus the 2 originals) were added to what was then
`test_data/non_gems/` (since reorganized into `test_data/currencies/`
and `test_data/scarabs/`, see below), bringing it to 18 real
samples. Re-running the same comparison on this much harder,
now-representative set:

```
                        real gem floor    real non-gem ceiling    non-gems exceeding the gem floor
Pixel correlation:      0.7541            0.7827                  8 of 18  (44%)
Embedding (ResNet18):   0.9250            0.7790                  0 of 18  (0%)
```

Pixel correlation isn't a threshold-tuning problem anymore -- it's
wrong on nearly half of real non-gem examples now on hand. Embeddings
hold up cleanly on the exact same, much harder set, with the margin
only narrowing slightly (~0.146 vs. the earlier ~0.17 estimated from
a smaller sample).

**This is a validated candidate fix, not yet adopted.** The real
tradeoff: switching `is_gem_present()` to embeddings would make
`torch`/`torchvision` a hard production dependency, not the optional/
experimental role they've had this session -- the same multi-hundred-
MB-to-2GB+ install-burden concern already discussed for the identity-
matching case applies here too, now for a function that runs on every
single capture rather than an edge case. Whether that tradeoff is
worth it for fixing two real bugs (one false positive, one false
negative) is a real decision, not a technical one -- logged here rather
than decided unilaterally.

**Update: the ONNX-export option (item 4 in the untried-approaches list
above) is no longer untried -- exported and validated directly, real
dependency-size numbers included, prompted by three more real
presence-detection misses (Siege Ballista x2, Spectral Helix -- see
"Multi-scale/position search for gem PRESENCE" above) making the
`torch` tradeoff worth actually checking rather than leaving
theoretical.** The frozen ImageNet ResNet18 (same recipe as above:
`model.fc = torch.nn.Identity()` for 512-d penultimate features) was
exported via `torch.onnx.export` and loaded with `onnxruntime`
(`CPUExecutionProvider`):

- **Numerically identical to the torch model**: max per-element
  difference between torch and ONNX embeddings across a real sample
  set was `2.7e-7` -- floating-point-precision noise, not a
  meaningfully different model.
- **Real separation reconfirmed on a bigger, fresher dataset than the
  original evaluation** (35 real gem images -- all 7 canonical
  references plus every real variance/quadrant/Blade-Ambusher sample
  on hand -- vs. 62 real non-gem images, `test_data/currencies/` +
  `test_data/scarabs/`, both grown since the original 18-sample test):
  real gem floor `0.9202`, real non-gem ceiling `0.8273`, **0 of 62
  non-gems exceeding the gem floor**. Different exact numbers than the
  original smaller-sample test (expected -- bigger dataset, and this
  run's preprocessing choices weren't verified byte-identical to
  whatever the original one-off evaluation script used), but the same
  clean, zero-overlap conclusion, now on real data more than 3x the
  size.
- **The dependency-size claim is real, not assumed**: installed
  `torch` alone is `546MB` on disk; installed `onnxruntime` is `46MB`
  -- roughly 12x smaller, and that's before counting `torchvision` on
  top of the `torch` figure. `onnx`/`onnxscript` (needed only to
  perform the export itself) do NOT need to ship to end users -- only
  `onnxruntime` does, confirming the export-once/ship-light premise
  actually holds and isn't just theoretical.
- **Export needs two files, not one**: `torch.onnx.export`'s current
  (dynamo-based) exporter writes the graph (~93KB) and the weights
  (`<name>.onnx.data`, ~44.7MB) as separate companion files -- both
  need to ship together for `onnxruntime` to load the model; a naive
  check of just the `.onnx` file's size would have badly
  under-estimated the real weight.
- **Fast enough for live use**: scoring all 97 real images (gem +
  non-gem) against the 7 references took ~1 second total on CPU --
  no concern for a keypress-triggered capture.

Not yet wired into `is_gem_present_in_rucksack()` -- this confirms the
ONNX path is a real, working way to get the validated embedding fix
without the full `torch`/`torchvision` production dependency, which
was the main open objection to adopting it. The remaining decision is
the same integration work either path would need (replacing the
threshold-based call sites, deciding whether identity matching adopts
the same approach at the same time), not a new technical unknown.

**Update: adopted.** After three real presence-detection misses in one
session (Siege Ballista x2, Spectral Helix), the decision landed on
switching rather than continuing to chase pixel-correlation thresholds.
`tools/export_gem_embedding_model.py` (new) builds
`assets/models/gem_embedding_resnet18.onnx` (+ `.onnx.data`) and a
precomputed `gem_reference_embeddings.json` "database" of the 7
reference gems' embeddings -- generated once by whoever has `torch`/
`torchvision`/`onnx`/`onnxscript` installed (`requirements-dev.txt`,
not part of normal setup), committed to the repo, then never
regenerated unless `assets/gems/` changes. `capture_pipeline.py` only
ever loads the exported `.onnx` file via `onnxruntime` (now a normal
`requirements.txt` dependency) -- no `torch` import anywhere in
production code.

Real, unexpected simplification found while wiring this in: plain
isolated per-quadrant crops, with NO windowing or multi-scale search at
all, correctly resolve all three real failure cases (0.91-0.92,
comfortably clear of the ~0.827 real non-gem ceiling) -- a CNN
embedding's built-in translation/scale robustness (global average
pooling) makes the whole `_glue_rucksack_region`/
`_icon_similarity_score_multiscale` windowing mechanism unnecessary for
presence detection. `is_gem_present_in_rucksack()` is now just four
independent `is_gem_present()`-equivalent calls, one per quadrant, no
gluing. Both `_glue_rucksack_region` and `_icon_similarity_score_multiscale`
are kept in the codebase, unused for presence now, since they're still
relevant to the separate identity-matching improvement idea above.

Real effect on `tests.py`: the `GEM PRESENCE` suite went from 5 real
failures to 0 (all 10 positive + 62 negative real examples correct),
and `RUCKSACK PRESENCE` stayed 18/18 despite dropping the windowing
entirely. The two new real capture crops added as regression data while
investigating this (`test_data/gems/variance/spectral_helix_of_trarthus/
20260915_170339.png`, `.../siege_ballista_of_trarthus/20260915_105105.png`)
surfaced one PRE-EXISTING, separate bug while being added:
`disambiguate_blade_ambusher_gem` also fails on the new Spectral Helix
sample (wrong gem, Spectral Throw, actually scores higher --
`0.6856` vs `0.7592` -- so it correctly declines to guess rather than
answering wrong, but still fails to resolve). Real, reproduced, but a
DIFFERENT function (identity disambiguation between the two specific
Blade Ambusher gems, not presence) -- left as the one remaining known
`tests.py` failure, not fixed as part of this switch, since it's the
identity-matching problem this section's own "remaining decision"
paragraph above already flagged as separate.

Attribution: torchvision's pretrained ResNet18 weights are BSD
3-Clause licensed (confirmed directly from the installed package's own
`LICENSE` file, matching torchvision's own repository license) -- see
`THIRD_PARTY_NOTICES.md`.

**Follow-up: the one remaining known failure flagged above (`disambiguate_blade_ambusher_gem`) is now also fixed, same embeddings switch.**
Before touching production code, ran the same embedding scorer against
every real Blade Ambusher sample on hand (10 total: the 2 canonical
`test_data/gems/blade_ambusher/` crops, 7 Spectral Helix variance
samples, 2 Spectral Throw variance samples -- including the exact
capture that broke pixel correlation, `20260915_170339`) and confirmed
it ranks all 10 correctly before committing to the switch, per the
user's own condition for doing this. Pixel correlation had ranked that
capture's two candidates backwards (Spectral Throw `0.7592` over the
true answer Spectral Helix `0.6856`); embeddings rank it correctly
(Helix `0.9158` vs Throw `0.8894`), with margins across all 10 real
samples ranging `0.0264` (this same capture -- correct now, but the
tightest real margin on hand) to `0.1197`.

`disambiguate_blade_ambusher_gem` now scores the crop against the two
Blade Ambusher entries in the same `gem_reference_embeddings.json`
"database" `is_gem_present` already uses, via the same `_embed_crop`,
instead of `_icon_similarity_score`. `min_margin` dropped from `0.1` to
`0.02` -- the old value was calibrated for pixel correlation's score
scale (real gap ~0.24 between same-gem and cross-gem scores) and
doesn't transfer to embeddings' much tighter clustering (0.85-1.0
across all 10 samples here). Unlike `GEM_EMBEDDING_PRESENCE_THRESHOLD`,
there's no confirmed real WRONG-ranking case on hand to calibrate a
proper floor/ceiling midpoint against -- `0.02` is set well below the
worst observed CORRECT margin rather than validated against a real
failure case, so this is a lower-confidence bound than presence
detection's, worth revisiting once more real Spectral Throw variance
turns up (n=2 now, still much thinner than Helix's n=7). `tests.py`'s
`BLADE AMBUSHER DISAMBIGUATION` suite went from 9/10 to 10/10 --
`RESULT: no regressions` across the whole suite for the first time
since this investigation started.

**Follow-up question worth logging: does this embeddings-based
disambiguation generalize to gem pairs beyond Helix/Throw, or did
Blade Ambusher just get lucky?** Ran the same scorer against all 19
real variance samples across all 7 gem types (each against the best of
the other 6 references, not just its own pair) before assuming the
answer. All 19/19 still rank correctly, but the margins are NOT
uniform, and Helix/Throw (the pair actually shipped) is not the
tightest one: **Siege Ballista vs. Blast Rain is a tighter real
collision** -- one real Siege Ballista sample (`20260915_105105`)
margins in at only `0.0169` against Blast Rain as runner-up, another
at `0.0250`, both tighter than Helix/Throw's own worst case (`0.0264`)
and the first one would actually fail the `min_margin=0.02` just
adopted for Blade Ambusher. The reference-to-reference cosine
similarity matrix confirms it's not a fluke: Siege Ballista/Blast Rain
sit at `0.8805`, one of the closest pairs among all 21 reference
combinations, comparable to Helix/Throw's own `0.8891`.

Not a live production risk today -- Siege Ballista (Sniper) and Blast
Rain (Flamequiver) both keep the 1:1 type->gem lookup in
`resolve_gem_presence`, so no image matching runs for either type at
all; disambiguation only exists for Blade Ambusher because it's the
one type with more than one possible gem. But it means the technique
doesn't generalize as a drop-in "any gem vs any gem" identity matcher
without more work -- if `match_icon()`'s broader identity matching is
ever revisited with embeddings (the still-open decision flagged
earlier in this section), Siege Ballista/Blast Rain is the next real
collision to solve, not a hypothetical one.

**Is this just "needs more captures"? Partly, but that undersells
it.** Siege Ballista has only 2 real variance samples on hand, Blast
Rain 4 -- both thin, the same position Blade Ambusher's Spectral Throw
side was in before a second real sample (`20260914_091453`) confirmed
its ranking held. More real captures are the necessary next step,
since a margin can't be calibrated without seeing real same-gem and
cross-gem variance on both sides. But more data answers a more
fundamental question first, not just "what number to pick": does a
safe, non-overlapping margin exist for this pair AT ALL, or do the two
distributions genuinely overlap the way pixel correlation's real-gem
and real-non-gem scores did for presence detection (see "Adopted:
embeddings for gem PRESENCE" above) -- a case no fixed threshold could
ever fix, margin-based or not, until the scoring method itself
changed. With only 2-4 samples per side, that question is still open;
it can't be answered by picking a number, only by collecting more real
Siege Ballista and Blast Rain captures and re-running this same check.

### Multi-scale/position search for gem PRESENCE -- implemented and live

Tested in two stages, since the first one turned out to be misleadingly
optimistic.

**Stage 1 (synthetic, misleading): pad each isolated icon crop with a
black border, then search across scales/positions within that padding.**
Real gem floor rose to 0.8208, real non-gem ceiling fell to 0.7966 --
0 of 18 non-gems exceeded the floor, a big apparent improvement over
fixed-position pixel correlation's 8 of 18 failures. But black padding
has no real content to accidentally match against, so this measures
best-case behavior, not real behavior.

**Stage 2 (real, decisive): reconstruct the actual whole rucksack from
real captured content instead.** `definitions/mercenary_regions.json`
confirms the four rucksack quadrants perfectly tile the parent
`rucksack` box with zero gaps (`top_left.x1 == top_right.x0`,
`top_left.y1 == bottom_left.y0`, etc.) -- so the four already-saved
quadrant crops from any existing capture can be glued back into a real
137x137 rucksack region (a ~4px seam gap exists at the internal
borders, from each crop's own 2px cosmetic `INSET` margin -- minor,
filled with black in this test). This gives genuine neighboring
content (the other 3 real items in that rucksack) to search against,
not empty padding. Re-ran the same multi-scale search against every
one of the 8 real known-gem sessions and all 8 real known-false-
positive sessions, glued into whole rucksacks:

```
Real gem floor (whole-rucksack, multi-scale):        0.8209
Real non-gem ceiling (whole-rucksack, multi-scale):  0.7966
0 of 8 known false-positive rucksacks exceed the gem floor
```

Real separation holds even with real competing content -- genuinely
better than the synthetic test's optimism might suggest. The margin
(~0.024) is the same size that's already broken down twice in this
project's history (`GEM_PRESENCE_THRESHOLD`'s revisions), on a still-
small sample (8 vs. 8/9) -- treat `RUCKSACK_GEM_PRESENCE_THRESHOLD` as
a real but early calibration, not a settled number.

**Update: broken a third time, and this time it's worse than "the
margin shrank" -- a real gem now scores BELOW the previously-recorded
real non-gem ceiling.** `captures/20260915_170339` (Blade Ambusher,
`rucksack_top_right`) is a confirmed-by-eye real Spectral Helix of
Trarthus that `is_gem_present_in_rucksack()` missed entirely (all four
slots came back empty in the log). Checked directly: it scores
`0.7949` -- correctly ranked #1 among all real references (beating
`spectral_shield_throw_of_trarthus` at `0.7774`), so the identity
ranking logic is fine, but `0.7949` falls short of
`RUCKSACK_GEM_PRESENCE_THRESHOLD` (`0.8088`) by `0.0139`. That alone
would just be "the margin broke again" -- but `0.7949` is also BELOW
the `0.7966` real-non-gem ceiling this exact threshold was calibrated
against, meaning the two known distributions (real gem scores, real
non-gem scores) now provably overlap: there is no single fixed
threshold that correctly separates this real gem from that real
non-gem. A same-day second capture, `captures/20260915_170833`
(`rucksack_bottom_right`, confirmed-by-eye Sunder of Trarthus), scored
`0.8264` and was detected correctly -- so this isn't "the whole
mechanism is broken," it's that the absolute-floor approach itself
has reached its real limit as more genuine variance (per-gem, per-
capture) accumulates. The natural next thing to try, not yet done:
`match_icon()`'s own margin-based acceptance rule (clear an absolute
floor AND beat the runner-up by `min_margin`) rather than a bare
floor -- Spectral Helix's `0.0175` margin over its own runner-up here
is comparable to already-accepted match_icon margins, suggesting
"confidently best, even at a lower absolute score" might separate
real cases better than "high enough in isolation" does at this point.
Not implemented; flagged as the concrete next experiment, not decided
unilaterally, same as the embeddings-vs-threshold tradeoff logged
just above.

**Implemented**: `is_gem_present_in_rucksack()` (`capture_pipeline.py`)
is what `process_capture()` now calls instead of four independent
`is_gem_present()` calls. Per quadrant, not a single whole-rucksack
yes/no -- each of the four quadrants gets its own search window (its
own boundary plus `RUCKSACK_WINDOW_MARGIN` px, glued from real
neighboring quadrant content via `_glue_rucksack_region()`), so
multi-gem counting (up to 4 per encounter) still works exactly as
before. `is_gem_present()` itself is untouched and still used for the
single-crop tests above -- this is a new, separate entry point, not a
signature change to the old one. Regression-tested against all 17 real
known sessions (`test_data/rucksacks/`, `tests.py`'s
`run_rucksack_presence_tests`) -- 17/17 passing, including the exact
session that started this investigation (`20260913_050511`, previously
a false `Storm Call Of Trarthus`, now correctly empty).

**Performance -- real cost, worth knowing before tuning further:**

```
                                          per comparison   per full check
is_gem_present() (old, fixed-position):        0.36 ms         3.0 ms   (4 quadrants)
is_gem_present_in_rucksack() (new):             9.2 ms       ~120 ms   (4 quadrants x 7 refs x 7 scales)
```

Roughly 25-40x slower, but well under anything noticeable on a
keypress-triggered capture (not a real-time system). Two independent
knobs to tune if accuracy or speed ever need to move:

- `RUCKSACK_WINDOW_MARGIN` (currently 16px) -- validated identical at
  10/16/22/30px (the correct match never needed to reach past its own
  quadrant), so there's no known accuracy reason to change it, but a
  smaller value would cost less if this ever needs to be faster.
- `_ICON_MULTISCALE_FACTORS` (currently 7 scales, 0.85-1.15) -- more/finer
  scales cost roughly linearly more (measured: 41 scales ran ~5.7x
  slower than 7 for the same comparison) in exchange for potentially
  catching a match this narrower range misses; fewer/coarser scales
  would cost less at some unmeasured accuracy risk. Not empirically
  optimized at either end -- 7 scales is what happened to get validated
  first, not a proven optimum.

**Other options for gem PRESENCE specifically, not yet tried, logged
before starting on the first one:**

1. ~~**ORB feature matching**~~ -- tried, real negative result, logged
   in "Tried and failed" below. Did not inherit identity-matching's
   mixed results; it does noticeably worse here.
2. ~~**Multi-scale/position search**~~ -- tried, validated, implemented,
   and now live in production (`is_gem_present_in_rucksack()`). See
   dedicated writeup below.
3. **A small non-deep classifier** (e.g. `scikit-learn` logistic
   regression / small MLP on hand-engineered features -- color
   histogram, edge density) -- a much lighter install (~tens of MB) than
   `torch`, using the same real labeled data already on hand. Untested
   whether that little data is enough for even a small model to
   generalize.
4. **Ship an ONNX-exported version of the embedding model instead of
   torch** -- keeps the validated ~0.17 margin result above while
   swapping the end-user dependency from `torch`/`torchvision` (heavy)
   to `onnxruntime` (much lighter, inference-only). See the dedicated
   discussion elsewhere in this project's notes for the concrete
   end-user impact.
5. **Data-mine the full pool of possible rucksack non-gem drops**
   (currency/scarabs/etc. -- on the order of dozens of possible types,
   though which ones are actually eligible in a mercenary rucksack
   specifically isn't confirmed) directly from game files, the same
   `poe-dat-viewer` extraction workflow already used for gems, rather
   than relying on whichever non-gem items happened to be incidentally
   captured so far (`test_data/currencies/` and `test_data/scarabs/`
   -- see below for how that split happened -- and the Exalted Orb
   false positive above was exactly a type not yet represented there
   at the time). Would substantially broaden negative-class
   coverage for validating ANY of the approaches above, gem or ML --
   but carries the same cross-domain caveat as the gem game-assets
   (raw extracted sprites, no in-game rendering/lighting/UI framing) --
   useful for broadening which item TYPES get tested, not a full
   substitute for real `mss`-captured non-gem examples when actually
   validating a threshold.

**Tried and failed** (real data, `tests.py`/project history has the
numbers):

- Raw pixel correlation (MSE, then normalized cross-correlation): same-gem
  and different-gem scores overlap on independent real captures.
- + local position search, + excluding the diffuse glow (core-only
  mask): narrows same-gem variance but narrows different-gem
  separation by the same amount -- no threshold separates them.
- Perceptual hashing (difference hash): same result, overlapping
  ranges.
- ORB feature matching: separates different gems reliably *within one
  capture domain* (real-vs-real, or game-asset-vs-game-asset) -- but
  matching a real capture against a game-extracted asset of the same
  gem scores *lower* than real-vs-real different-gem comparisons.
  Edge-map correlation and CLAHE-normalized ORB were tried
  specifically to close this cross-domain gap and did not.
- ORB feature matching, for gem PRESENCE specifically (not identity --
  see the "Embeddings for gem PRESENCE" section above for why this
  distinct, coarser question is usually more tractable): tested two
  scoring schemes (Lowe's-ratio-test good-match ratio, and a simpler
  raw good-match count with a distance cutoff) against every real gem
  variance sample and every real non-gem sample on hand. Both overlap
  completely -- worse than pixel correlation's current near-collision,
  not better. Likely cause: these are tiny (48-96px) icons with smooth,
  low-texture surfaces (a colored gem, a plain currency orb) -- ORB's
  corner/keypoint detection doesn't have much to grab onto at this size,
  producing few and inconsistent keypoints (visually confirmed: the
  same real crop that's the hardest case for pixel correlation and
  embeddings, `test_data/gems/top_left.png`, also scored a bare 0 raw
  good-matches under ORB against every reference).

**Common thread**: every method tried compares pixel intensities or
local pixel-intensity patterns, directly or indirectly. That's
consistent with why each one failed on cross-domain comparisons
specifically -- the sparkle/glow/color-grading difference between a
real capture and a game-extracted asset changes pixel intensities
throughout the image, including around structurally-matching points.

**Next strategy**: a local pretrained visual embedding model (e.g. a
small `torchvision` CNN, comparing feature vectors by cosine
similarity) rather than another pixel-comparison variant. A hosted
vision-API alternative was considered and ruled out -- ongoing per-call
cost and an API-key/network requirement for every user, versus a local
model's one-time download and then free, offline, portable operation.
Opportunity cost: `torch`/`torchvision` (or a lighter ONNX Runtime
alternative) is a much heavier dependency than anything currently in
`requirements.txt`, and untested in this project so far -- worth
weighing against how often the Blade Ambusher case actually comes up in
practice before investing in it.

This domain is paused here rather than actively worked -- resume if
skill/support matching runs into the same cross-domain issue, since a
fix would likely serve both. **Update: no longer "not-yet-started"** --
skill identification is done and live (OCR, not icon matching -- see
"Skills: OCR validated across real data" below); support identification
has had extensive structural/relational investigation (see "Support
icons: many are shared by design" and `SupportCountTier` sections
below), though actual icon-matching CODE for supports still doesn't
exist yet, so the cross-domain question this section raises remains
untested for both.

## Explored but not built

These were investigated and validated with real data, but no code exists
for them yet. Worth knowing about before treating a fresh idea in this
space as novel — it may already have groundwork.

### Dynamic UI-anchor sizing (resolution/scale independence)

The current pixel schema (`definitions/mercenary_regions.json`) is
calibrated to one resolution/UI scale, same limitation documented for
image references in `assets/README.md`. The idea: some UI chrome elements
appear to be fixed in absolute screen position regardless of encounter
content — the info-circle icon (upper-left of the mercenary panel), the
close-circle icon (upper-right), and the "Lvl" text label were all
directly measured (via pixel centroid comparison) across three different
real screenshots and found identical within ~1px. Two of these can act as
both an anchor point and, via the distance between them, a measured scale
factor relative to the calibrated baseline — every other region's
coordinates could then be computed as a proportional offset instead of
hardcoded pixels, the same way the *other* independently-reviewed PoE
mercenary tool (mentioned in `mercenary_tracker_plan.md`'s TODO section)
does it via OCR-anchored proportional regions.

**What's not yet done, and currently BLOCKED, not just pending**: this
was only validated at one resolution across different *content*
(different mercenaries, wager amounts, etc.) — never tested at a
genuinely different resolution or UI scale, which is the actual property
that would need to hold for this to solve anything. No anchor-detection
code exists. **Confirmed blocked**: the user has tried changing PoE's
resolution multiple times specifically to get this second data point,
and the game crashes every time -- not a one-off, a real repeated
failure across attempts. Getting a real different-resolution screenshot
this way is off the table for now. If this needs to move forward before
the crash is somehow resolved, the alternatives are: a different
machine/monitor already running PoE at another native resolution, a
borderless/windowed display mode instead of exclusive fullscreen (a
different code path in most games, sometimes crash-free when exclusive
fullscreen mode-switching isn't), or asking in the community for a
donated screenshot at a different resolution -- do NOT keep re-testing
the same in-client resolution-change path expecting a different result.

**Update: unblocked via exactly the "community-donated screenshot"
route above, and the real result is genuinely encouraging.** Three
real, independently-sourced mercenary-panel screenshots turned up at
resolutions other than this project's own 2560x1440 calibration: two
from the AdamZ-8113 `mercenary-dataset-collector` project's public test
fixtures (`merc15.png`, 2439x1418; `merc16.png`, 1701x1383 but visibly a
partial/cropped capture, not a full screen -- not usable for this test)
and one full-screen screenshot shared on Reddit (1920x1080, same 16:9
aspect ratio as this project's own calibration). None were saved into
this repo (public third-party images, reviewed for research only, same
ground rules as the AdamZ project's source code review elsewhere in this
file) -- reviewed in a scratch location only.

**The clean test (1920x1080, matching 16:9 aspect ratio)**: took this
project's own `"window"` region as a fraction of its calibration
screen (`x0=0.3398, y0=0.0542, x1=0.6531, y1=0.8438` of screen
width/height), scaled those same fractions directly onto the 1920x1080
image with no other adjustment, and cropped that predicted box. **The
predicted crop lines up with the real panel almost exactly** -- off by
at most a few pixels on any edge (the ornate corner scrollwork just
barely clipped on the top/left/right, comfortably within the margin
`INSET`-style cropping already tolerates elsewhere in this project).
This is real, if not exhaustive, evidence that PoE's mercenary panel
scales proportionally with resolution when aspect ratio is held
constant -- a plain `window_relative`-fraction model (already recorded
for every region in `mercenary_regions.json`, just never validated
against a second resolution until now) would likely transfer cleanly to
another 16:9 display without needing the more complex UI-anchor-
detection approach discussed above at all, at least for the common
same-aspect-ratio case.

**The messier test (2439x1418, aspect ratio 1.7203 vs. this project's
1.7778)**: the same proportional-fraction prediction was noticeably
LESS accurate here -- the predicted crop had a visible margin of
background art between the crop edge and the real panel on more than
one side, meaning simple width/height-fraction scaling overestimated
the panel's size for this off-aspect-ratio image. Not a clean
disproof, though: a screenshot found online may not be an untouched
native capture -- Discord/Reddit compression, video-export resizing, or
a thumbnail pipeline can introduce exactly this kind of small,
non-uniform aspect-ratio distortion (2439x1418's ratio is only ~3.2%
off true 16:9, consistent with a resize artifact rather than a
deliberately-chosen odd native resolution). Treat this one as
inconclusive, not as evidence against proportional scaling -- it just
isn't a trustworthy enough source image to draw a real conclusion from,
unlike the exact-16:9 Reddit capture.

**Not yet done**: no code changes were made from this -- this was a
research check to unblock a stalled question, not a call to build a
dynamic-region feature ahead of actual need (this project's own capture
pipeline only ever runs against one machine's own fixed resolution via
`mss`; multi-resolution support has no current consumer). Worth
revisiting if this project is ever meant to run on someone else's
machine at a different resolution -- the real result above suggests a
`window_relative`-fraction-based region system (detect the panel once
per resolution, or even just let a user supply their own resolution up
front) is a more promising starting point than full UI-anchor
detection, at least for same-aspect-ratio displays. A genuinely
different aspect ratio (ultrawide, 4:3, etc.) remains untested with any
real, trustworthy data.

**Check soon — a claimed prior conclusion here is undocumented and
shouldn't be trusted as settled.** A separate conversation reportedly
tested this same idea (moving rucksack capture from the fixed absolute
bounding boxes in `mercenary_regions.json` to a dynamic, UI-anchor-
relative model) and concluded it "wouldn't help," based on unspecified
preliminary testing -- but that investigation was never written down
here or in `assets/README.md`, so there's no way to check its
assumptions, what it actually tested, or whether it's still valid.
Treat it as NOT settled until re-investigated and documented properly.

This is now also a live candidate for fixing the real cross-domain
match confound found while reviewing a separate mercenary-tracking
tool (see this file's "Embedding-model evaluation" /
compositing sections above) -- that tool derives its rucksack region from an
OCR-detected anchor ("Recruit" heading text, or a "Lvl NN" fallback)
scaled proportionally, essentially the same "OCR-anchored proportional
regions" approach already credited to the *other* tool referenced in
`mercenary_tracker_plan.md`'s TODO section above -- quite possibly the
same tool. Concretely: our current rucksack quadrant crops come from
one fixed, hand-drawn bounding box per quadrant, calibrated once --
never verified to center the actual item consistently capture to
capture. If real position/size jitter exists there, it would affect
every fixed-scale/fixed-position comparison this project does (both
`match_icon()`/`is_gem_present()`'s pixel correlation AND any future
embedding approach equally) -- worth checking before concluding either
comparison method needs to be smarter, rather than the crop itself
needing to be more consistently centered.

Also validated in passing: the bottom-left button reliably reads "TAKE
ITEM" (not "Take All") and toggles to "CANCEL"; the bottom-right button
reads "REMATCH" and is expected to grey out on a repeat encounter, though
no real example of the greyed state has been captured yet to confirm.

### CTRL+PRTSC vs. `mss` capture-method mismatch

A significant, empirically-confirmed finding that isn't written into
`assets/README.md`'s history even though it directly motivated a full data
purge: manually-taken CTRL+PRTSC screenshots (used for most full-window
examples earlier in the project) and the actual script's `mss`-based
captures are **not the same data** — a CTRL+PRTSC capture of the same gem
was measured at 9-17% brighter across every RGB channel than the real
`mss` capture, likely a gamma/HDR-tonemapping difference between Windows'
screenshot tool and whatever capture API `mss` uses under the hood.

This was NOT the dominant cause of the gem-identity-matching failures
(later proven: independent real `mss`-vs-`mss` captures of the same gem
*still* failed to match reliably, with the capture-method confound fully
removed) — but it was real, and every gem reference/test image in
`assets/gems/` and `test_data/gems/` was subsequently rebuilt from
genuine `mss` exports for this reason. If any future test data or
reference gets sourced from a manual screenshot again instead of a real
`captures/` folder output, expect this same systematic bias to reappear.

### Warrant-text alignment for skill/support ground truth

**Update: built, and it works exactly as predicted below -- but only
after this exact insight was missed once already.** `tools/
harvest_support_icons.py` (see "Support icons: many are shared by
design" and `SupportCountTier` below for the analysis that preceded
it) was first written to identify a real support's icon by INFERENCE --
intersecting a skill's `PossibleSupports` pool across every capture a
crop appeared in -- without re-reading this very section first, which
already said the real answer was sitting in plain text in `warrant.txt`
the whole time. That inference-only version got barely any captures to
a confident label (12 of 204 unique crops). Only after a direct
question ("the warrant gives exactly the right support... is the issue
that you're trying to [infer] when we should be [using ground truth]?")
did this section's own already-validated idea actually get wired in.
Real lesson, not just a process note: an idea marked "validated, not
yet built" in this file is a liability, not just a TODO, if the next
person building the related feature doesn't reread it first -- which is
exactly what happened here despite it being clearly written down.

Once wired in against the real capture corpus (80 captures with a
usable `warrant.txt`, 677 unique real (icon, tier) crops after
deduplication): **631 (93%) resolved to a single, zero-ambiguity label
straight from the text -- no image matching, no candidate list, no
guessing.** 33 more came back as GENUINE, ground-truth-CONFIRMED
ambiguity -- not inferred as merely possible, but proven to have
actually happened: different skills' real warrants both landed on the
exact same pixel-identical crop for two or three different real
supports (e.g. `Gilded Jolt`/`Gilded Hopelessness`/`Gilded Secondary
Shots`, all `mercgoldsupportgem` at tier III -- the same global
icon-sharing phenomenon documented below, now confirmed with real
occurrences instead of structural possibility). Only the remaining 13
crops, from the minority of captures with no usable `warrant.txt` at
all (just the OCR'd `warrant_extracted.txt`, skill names only), still
need the inference/candidate-narrowing fallback -- kept as a fallback
path, not the primary one anymore. A hand-editable `tier_notes.json`
(read tier off a saved crop by eye, since that's trivial for a human
and not yet built for code) narrows some of that inferred minority
further; a small interactive page (published as a Claude Artifact) was
built to make that tagging fast -- click the tier under each crop, get
back ready-to-paste JSON, no hash-typing required.

One more real bug caught building this: several labels (e.g. "Added
Cold") produced 3-4 near-duplicate reference crops instead of one --
same correct ground-truth label each time, just tiny capture-level
pixel noise keeping them from hashing identical. Not a labeling bug,
but worth keeping tidy: the harvester keeps only the most-often-seen
rendering as the primary file and files the rest under
`assets/harvested_supports/variants/` -- regenerated fresh (both the
top-level files and `variants/`) on every run, so this stays clean as
more captures accumulate rather than slowly filling up with redundant
near-duplicates.

**Update: the first version of that naming was itself misaligned with
this project's own conventions, caught directly** -- filenames were
built from a re-slugified DISPLAY name ("Added Cold" -> primary file
literally titled `Added Cold.png`; variants folder `added_cold_II`,
snake_case but still derived from the English name, not GGG's). Neither
matched `definitions/supports.json`'s own "icon" field convention
(GGG's real, already-lowercase-no-separator asset basename, e.g.
"addedcolddamage" -- note this ISN'T even the same string as a naive
slug of "Added Cold" would produce, since the icon's real name is
"AddedColdDamage.dds", not "AddedCold.dds"). Fixed: every reference
filename is now `<icon>_<tier roman>.png` (e.g.
`addedcolddamage_ii.png`), derived directly from `supports.json`, for
both the primary file and its `variants/<icon>_<tier roman>/` folder --
one consistent naming scheme end to end instead of three different
ones. `label` (the human-readable name) stays in `manifest.json` for
anyone reading it; it's just no longer baked into the filename.

Fixing this surfaced a real, independent gap while deriving the tier
suffix: guessing the roman numeral from the Name string (no Lesser/
Greater prefix -> assume tier II) is WRONG for every standalone entry
with no Lesser/Mid/Greater trio -- every "Gilded X" support has no
prefix at all but is tier 3, confirmed directly against
`mercenarysupports.json`'s own `Tier` field (`Gilded Jolt`'s raw `Tier`
is 3). `definitions/supports.json` didn't carry the real `Tier` field at
all before this -- only `icon`/`family` -- so anything needing tier had
to either guess from the Name string (this bug) or not know it.
`tools/extract_mercenary_skills.py`'s `get_support_details()` now
extracts `tier` (int) and `tier_roman` straight from the source data
alongside `icon`/`family` -- purely additive, confirmed all 265 existing
entries' `icon`/`family` values unchanged after regenerating. This also
fixed a second, latent bug the same naming rewrite surfaced: the
inference path's `identity()` deliberately collapses tier away (that's
the whole point of grouping Lesser/bare/Greater as one family), which
meant a resolved single-candidate identity could get treated as a
clean, tier-specific reference even when the actual tier of that
SPECIFIC crop was still unknown -- silently risking two genuinely
different real tiers of one family (e.g. tier II "Fire Penetration" and
tier III "Greater Fire Penetration") merging into the same file as if
one were just noisy near-duplicate of the other. Now a resolved
identity only becomes a written reference once its tier is ALSO known
(from a `tier_notes.json` entry, or because the identity has exactly
one real member at all, so there's no other tier it could be) --
otherwise it correctly stays in the ambiguous bucket instead of
guessing.

**Update: a much bigger real bug found the same way -- manual labeling
surfaced a case (Minion Damage vs. Minion Life both resolving to only
one harvested file) that didn't add up, and chasing it down found the
harvester was silently hiding roughly 4x more real ambiguity than it
was reporting.** Checked directly: pixel-diffing several real
`miniondamage_ii` crops against each other at every small alignment
offset (search a +/-2px window, keep the best) showed SAME-identity
pairs (Minion Life vs. another Minion Life) and CROSS-identity pairs
(Minion Life vs. Minion Damage) landing on the exact same small set of
diff values, including several cross-identity pairs at near-zero --
proof the two really do render pixel-identical once aligned, exactly as
the raw game data's shared `SupportFamily`-adjacent icon always implied.
The bug wasn't the icon lookup at all: real capture-to-capture jitter
(1-2px) was enough to keep MD5-exact hashing from ever putting these in
the same cluster, so each fragment only ever saw ONE ground-truth label
by chance of small sample size -- the "conflicting labels within one
exact hash" check (`ground_truth_ambiguous`, see above) had nothing to
catch, because the conflict only exists ACROSS hashes. The later merge
pass that groups same-`visual_key` fragments into one primary + variants
(built for the entirely different, genuinely-same-label case of "Added
Cold" capture noise) was blindly trusting whichever fragment had the
most occurrences as if it settled the question, silently discarding the
real ambiguity. Fixed: that merge pass now checks whether every fragment
sharing a `visual_key` actually agrees on label before merging -- if
they don't, the WHOLE group is reclassified as ambiguous (unioning in
candidates from any fragment that was already known-ambiguous too, not
just the confidently-labeled ones -- confirmed necessary on
`mercsilverstrintsupportgem_iii`, where 2 fragments confidently said
"Greater Physical as Extra" while 2 OTHER fragments already proved
"Greater Ironwood" also occurs there, and the first version of this fix
missed that case entirely by only comparing confident labels to each
other). Real effect on the numbers: ground-truth-confirmed ambiguity
went from 36 entries (all found within-exact-hash) to 140 entries across
12 distinct icon+tier groups once cross-hash merging was checked
properly -- the true ambiguity surface was there all along, just mostly
invisible to a clustering method that assumed real capture data hashes
more cleanly than it does.

The original description of the mechanism, written before any of this
was built, turned out to be exactly right:

A Mercenary Warrant's plain text (obtained by CTRL+C on the warrant
item after choosing to take it) lists every
equipped skill and its linked supports in a fixed, decodable order that
maps directly onto the visual skill/support region layout in a
screenshot:

- Skills, top to bottom in the screenshot, are in the same order as the
  warrant's skill list — and each already has its own name rendered as
  text right next to its icon, so this doesn't actually need the warrant
  at all (see "Skill-name OCR" below, already built).
- Supports have no visible text in the screenshot — only bare icons. But
  each skill's row of support icons is horizontally aligned with that
  skill's row in the skill list, and left-to-right order within the row
  matches the warrant's top-to-bottom order of that skill's linked
  supports. A skill with zero supports leaves its row visually blank
  rather than shifting the rows below it up.

This was confirmed exactly against a real capture: a warrant listing
support counts of 3, 0, 5, 4, 0, 2 (per skill, in order) matched the
screenshot's visible support-icon row groupings count-for-count,
including the two blank rows landing in the right places.

**Why this matters**, confirmed rather than just predicted now: warrant+
screenshot pairs (collected by occasionally choosing to take the
warrant instead of rucksack loot) give perfectly-labeled (icon crop,
support name) training/reference data for free, with zero manual
identification — the same category of problem gems had (bare icons, no
text, needed a reference library) but with an actual path to a large
labeled dataset instead of one manually-identified example at a time.
93% of real crops resolved this way with zero ambiguity, see the Update
above.

A key significance is that the skills can be identified easily using the text but could also be identified easily by their static square icons. The supports are in the same problem domain as gems with subtle differences failing difference margins using standard OCR techniques.

**Update: re-run against the full current capture history (282
directories across `captures/` and all 3 `__captures_*/` archives,
same scope the harvester always covered).** 919 unique (icon, tier)
crops now (was 677), 741 resolved from ground truth with zero ambiguity
(was 631), coverage up to 105/159 (icon, tier) keys (was 101/159) --
real, if modest, progress just from captures accumulated since the
last run. Also caught: none of the 8 `manual_labels.json`-confirmed
crops or 4 tier-narrowed inferred crops currently unlock any NEW
coverage beyond what ground truth already resolves -- they still
narrow/confirm identity within already-covered families, just don't
close any of the 54 still-missing gaps yet. The missing-coverage report
(`assets/support_icon_coverage.md`) is now regenerated by a real script
(`tools/generate_support_coverage_report.py`) instead of by hand --
that file's own footer had been asking for exactly this ("rerun the
query behind this file") since it was first written, but the query
itself was never saved as code until now. Also fixed one discrepancy
the more careful regeneration caught: `Lesser Ironwood` and `Lesser
Physical as Extra` genuinely share the same icon+tier
(`mercsilverstrintsupportgem_i`, confirmed directly against
`definitions/supports.json`) -- the original hand-written coverage doc
listed `Lesser Ironwood` alone with a breadth of 10, missing that it's
actually a 2-way collision with a combined breadth of 17.

### Local embedding model as the eventual support-matching approach

Follows directly from the above. Once enough warrant-paired captures
exist, the plan discussed (not started) is: use a local pretrained
visual embedding model (small `torchvision` CNN or similar, comparing
feature vectors by cosine similarity) rather than repeating the pixel-
correlation/ORB investigation that this file documents failing
for gems (see "Tried and failed" above). A hosted vision-API alternative was explicitly ruled out —
per-call cost and a hard network/API-key requirement for every user of
this tool, versus a local model's one-time download and then fully
offline operation. This is the same "next strategy" recommended
above for the Blade Ambusher gem-disambiguation TODO — a fix here
would very likely serve both problems, which is part of why gem
disambiguation was explicitly paused rather than pursued further.

Real cost tradeoff already discussed: `torch`/`torchvision` is a much
heavier dependency (hundreds of MB to 2GB+ depending on build) than
anything currently in `requirements.txt`, though it's a one-time install
cost with no ongoing fee, unlike a vision API. Worth prototyping only
once there's a real volume of warrant-paired data to validate against —
building it speculatively risks repeating the gem investigation's
trial-and-error without real data to ground it.

**Evaluation plan — steps 1-4 run for gems (not yet for supports, see
below).** Mirrors how ORB was validated for gems (see "Tried and failed" above),
since that approach actually produced a trustworthy answer rather than
an assumption:

1. ~~Get `torch`/`torchvision` installed somewhere with real network
   access~~ — done. The "this sandbox couldn't install it" blocker
   turned out to be specific to an earlier sandbox, not a general
   constraint; both installed cleanly with real network access when
   actually tried again.
2. ~~Pick a small pretrained model~~ — `resnet18` (ImageNet-pretrained),
   used frozen (no fine-tuning) as a penultimate-layer feature extractor.
3. Same-gem/different-gem same-domain, and same-gem cross-domain: all
   run against every real capture and the existing `pending_gems`
   composite currently in the project — see this file's
   "Embedding-model evaluation" section above for the actual numbers.
4. Result: in-domain (real-vs-real) separation is clean (27 same-gem
   pairs 0.9601-1.0000, 126 different-gem pairs 0.7240-0.8745, zero
   overlap) — but that's not an improvement over what `match_icon()`
   already does for real-vs-real. The case that actually matters,
   cross-domain (real vs. `pending_gems` composite), overlaps the same
   way ORB's did — a real negative result, logged above
   rather than discarded quietly, per the standard set here.
5. Not reached — cross-domain didn't show real separation, so per this
   plan's own step 5, trying it on warrant-paired support data isn't
   justified yet. Separately, review of a different independently-built
   mercenary-tracking tool (see "A different project's compositing
   approach" above) suggests the
   `pending_gems` composite itself may be the confound (a much simpler
   no-sparkle composite scored dramatically better against real Helix,
   though it failed outright for Throw/Bladefall) — worth re-running
   this same cross-domain step against a corrected composite before
   concluding embeddings themselves don't work here.

**Update: this is NOT the path that ended up working for supports.**
The sections below ("Support icons: many are shared by design" and
`SupportCountTier` actually counts distinct FAMILIES") describe what
actually happened once real support investigation started in earnest --
and it's a relational/structural analysis using data already extracted
from the game (icons, `SupportFamily`, per-skill `PossibleSupports`
pools), not an embedding model. No `torch` dependency, no image
comparison at all was needed to resolve ~78% of the icon-collision
problem down to a small, enumerable set of genuinely ambiguous cases.
Embeddings remain untested for supports and could still be relevant for
actual ICON MATCHING (turning a screenshot crop into a candidate name in
the first place, which none of the work below does yet -- it all
assumes the icon is already identified and reasons about what it could
mean) -- but the disambiguation-once-matched problem this section
originally worried about turned out to have a much cheaper answer than
expected.

**Update: the OTHER half of "why embeddings" -- needing a labeled
reference dataset to match against -- also turned out to have a much
cheaper answer**, and it's the one this section already named above
(warrant-text alignment), not embeddings: `tools/
harvest_support_icons.py` now builds that reference set directly from
real `warrant.txt` ground truth, no model, no manual labeling, 93% of
crops resolved outright. What's still genuinely unsolved, and where
matching (embeddings or otherwise) actually stays relevant, is the LIVE
capture case -- identifying a support crop from a fresh encounter where
the player takes rucksack loot instead of the warrant, so no ground
truth text exists for that specific instance and the crop has to be
matched against the now-real reference set this section worried about
not having.

**Update: built and tested (`tools/match_support_icon.py`), NOT wired
into `capture_pipeline.py` -- Part 2 from `harvest_support_icons.py`'s
own docstring, deliberately left as a standalone report/validation
tool per the user's explicit request.** Reused the SAME frozen ResNet18
embedding backbone already validated for gem presence
(`capture_pipeline._embed_crop`/`_load_gem_embedding_session`) rather
than training or exporting anything new -- it's a generic ImageNet
feature extractor, not gem-specific, so there was no reason to
duplicate it. Reference set: one embedding per top-level
`assets/harvested_supports/<visual_key>.png` file (105 of them, after
excluding 4 genuinely-unresolved `unlabeled_<hash>.png` placeholders
that aren't real identities and only added confusable noise when first
included by mistake). Classifies at the (icon, tier) level
("visual_key"), not the individual support NAME -- the actual decidable
target for an image method, since several real supports render
pixel-identical at the same tier by design and no image method can
ever split those (confirmed: 0 misclassifications landed on a known
pixel-identical sibling in this run, so that structural ceiling wasn't
even what showed up).

Validated against 1,971 real crops independently re-cropped from every
capture with a usable `warrant.txt` (137 of 282) -- not the files used
to build the reference catalog in the abstract; each validation crop
comes from its own capture's `supports.png`, using the harvester's own
grid math and ground-truth parser (`harvest_support_icons.load_grid`/
`parse_warrant_ground_truth`, imported directly rather than
reimplemented) so the "what should this be" side of the check is
exactly as trustworthy as the harvester's own labeling.

**Result: 100% icon-family accuracy (1971/1971), 88.3% exact
(icon+tier) accuracy (1741/1971).** Every single one of the 230 misses
-- zero exceptions -- guessed the right icon but the wrong TIER of that
same icon. The model never once confused two genuinely different
supports. This makes sense given how the embedding works: global
average pooling over a frozen backbone captures overall visual content
well but isn't very sensitive to a small, spatially localized detail
like a corner roman-numeral badge -- exactly the one piece of
information a support's tier badge is. Practical implication: an
actual production matcher shouldn't need a better embedding model or
more reference data to fix this -- icon identification is already
solved at 100%, and tier is a narrower, more targeted problem (reading
a small I/II/III badge in a fixed corner position) that likely wants a
cheap, separate pass -- pixel-region comparison on just the badge
corner, or literally OCR-ing the roman numeral -- rather than asking
the same whole-crop embedding to do double duty. Not attempted yet;
this is a report of where the gap is, not a fix, per the explicit
"build and report, don't wire in" scope this was done under.

**Update: attempted, and worth recording exactly how the first version
was misleadingly good before turning out mediocre.** Found the badge's
actual location the right way -- not by eyeballing one zoomed crop, but
by averaging MANY same-tier crops of the same icon (many `variants/`
samples) to cancel out real per-capture noise, then diffing those
averages across tiers. That isolates the one region that differs by
more than the natural same-tier noise floor: consistently
`y=[0.55,1.0], x=[0.35,1.0]` of the crop (bottom-right), confirmed
across 4 different icon families independently before trusting it as
fixed-position rather than icon-specific.

First bar-counting attempt (threshold the badge's gold color, count
tall-thin connected components, keep only components near the median Y
position) scored 8/8 on those same 4 families used to find the region
-- looked like a clean win. **It wasn't**: run against the FULL real
dataset (915 crops: every top-level harvested reference plus every
`variants/` sample, not just 4 cherry-picked families) it only scored
56.9%. Root cause, found by debugging one specific failure
(`addedcolddamage_ii`, scored 0 bars when it should score 2): some
icons have their OWN gold-colored decorative background art (a
crystal/spike flourish, in that case) inside the badge region, and the
median-Y clustering broke down exactly when spurious gold blobs and
real bars were roughly equal in count -- the median landed in the gap
between them, excluding both. Same trap as gem presence's threshold-
chasing and skill-noise-filtering earlier in this file: a rule that
looks perfect on a small sample doesn't mean it generalizes, and the
project's own established discipline (validate against the full real
corpus, not a handful of examples) caught it before it shipped as a
false "solved."

Refined the filter to require bars be TALL relative to the badge
region height (>=45%) and narrow in absolute width (<=3px, width < half
height) instead of clustering by position -- real bars are reliably
tall-and-thin regardless of which icon they sit on, where spurious
background blobs (measured directly: e.g. a 4px-tall flourish fragment
vs. 12-17px-tall real bars) are shorter. This raised standalone
accuracy to 75.2% across the same full 915-crop set -- better, but
still not clean; some icons' art apparently produces tall-thin gold
blobs too (`brutality_ii`/`_iii` consistently undercounts by exactly
one bar, a per-icon systematic bias, not random noise).

**Integrated as a genuine second opinion, not a replacement, and
measured the real net effect on the whole pipeline (not just the
badge check in isolation).** `resolve_tier_from_badge()` only
overrides the whole-crop embedding's tier guess when it gets a
confident 1/2/3 bar count AND the resulting `(icon, tier)` key actually
exists in the reference catalog; otherwise the embedding's own answer
stands. Re-ran `match_support_icon.py`'s full 1,971-crop validation
with this wired in: **88.3% -> 93.5% exact (icon+tier) accuracy,
1741/1971 -> 1843/1971 (+102 net correct).** The badge check disagreed
with the embedding 306 times -- helped 204, hurt 102 -- a real,
positive, but not clean trade (roughly 2:1 in its favor, not a fix).
128 genuine misses remain, still 100% same-icon-wrong-tier -- the
badge check narrows the gap it targets, it doesn't close it. Honest
framing for whoever picks this up next: two more standalone accuracy
numbers were reported along the way before landing here (8/8 on 4
families, then 56.9%, then 75.2% standalone) -- the number that
actually matters is the LAST one, measured on the full real corpus
with the check integrated into the pipeline it's meant to help, not
any of the earlier ones in isolation.

**Update: root-caused the remaining 128 misses instead of accepting
75.2% as the ceiling, and found a real, fixable, single-cause bug --
not diffuse noise.** Broke the misses down properly (an earlier pass
had a regex bug: `(i|ii|iii)` alternation matches "i" first even inside
"iii", silently corrupting the earlier direction analysis -- fixed to
`(iii|ii|i)`, longest-first). The corrected breakdown showed all 128
misses concentrated in just 12 of 105 icon families, always off by
exactly one bar, never two -- not randomly scattered, a real signal.
Traced the dominant case (`mercsilverdexintsupportgem`, 31 misses) down
to its exact pixel mask: the connected-component approach's own
anti-aliasing-bridging dilation would sometimes ALSO bridge a real bar
to an unrelated gold pixel from the icon's own art sitting one row
above it, merging them into an oversized blob that then failed the
width check and silently vanished -- undercounting by exactly one,
exactly the observed pattern. Root cause confirmed directly, not
inferred: printing the raw boolean mask as ASCII showed three cleanly
separated bar columns with a genuine one-row gap to the spurious
element above -- dilation-by-one-row can't tell "gap within one bar"
from "gap to something else" when both happen to be exactly one row
wide.

**Fixed by switching from 2D connected-component blobs to per-column
run-length counting** (`resolve_tier_from_badge()`, rewritten): for
each column in the badge region, find its longest unbroken run of
badge-gold pixels, and count columns whose run clears a height
threshold, grouped by adjacency. A stray gold pixel in some OTHER
column can never contaminate a real bar's own column's run length, so
this class of bug can't happen structurally, not just empirically.
Also caught and fixed a related bug the same way: the first height
threshold (45% of ROI height) was too strict, confirmed against a real
false negative (`addedcolddamage_iii`'s middle bar has a genuine
9-row run against a 22-row ROI, 40.9% -- just under 45%, not noise).
Swept 0.25-0.45 against the full 915-crop real corpus and found a
flat, stable plateau at 0.32-0.40 (99.5% either way) -- landed on
0.35 for margin on both sides, not because it's the single best value.

**Result, standalone: 99.5% (910/915).** Only one icon family
(`poison`) still fails, consistently, because its own art (a
fang/vial shape) happens to be tall, narrow, and gold-trimmed enough
to clear the run-length threshold as a false extra bar -- checked
whether narrowing the badge's X-boundary would exclude it without
regressing other icons, and it wouldn't: other icons' real leftmost
bar sits at almost exactly the same horizontal position poison's fang
occupies, so a blanket boundary change would trade this fix for a new,
different regression. Left as a documented, narrow limitation (1 of
105 real icon families) rather than special-cased -- same reasoning
this project has applied elsewhere against overfitting a general rule
to one example.

**Re-integrated and re-measured the WHOLE pipeline, not just the
badge check in isolation, same discipline as last time:** exact
(icon+tier) accuracy on the full 1,971-crop real validation set went
**93.5% -> 99.6% (1843/1971 -> 1964/1971)**. The badge check now
disagrees with the embedding 237 times -- helps 230, hurts only 7 (all
7 the same known `poison_ii`->`poison_iii` case) -- a dramatically
better hit ratio than the previous version's 204:102. Every one of the
7 remaining misses is exactly the poison edge case; zero unexplained
failures remain.

**Update: the user asked whether the poison edge case itself could be
resolved, and it could -- root cause was findable, not fundamental.**
Zoomed into both `poison_ii` and `poison_iii` directly and sampled the
actual pixel colors of the real bars versus the false-positive fang
trim. Both are "gold" by the existing color threshold, but they're
NOT the same kind of gold: real bars are a flat UI overlay (measured:
two near-constant colors, `(255,214,140)` and `(228,191,125)`, repeated
almost exactly down the entire bar with only the top/bottom edge pixel
differing from anti-aliasing); the fang trim is gradient-shaded 3D icon
art (measured: drifts from `(174,148,62)` to `(239,223,121)` and back
down to `(176,146,59)` along its own length -- a real, continuous
shading gradient, not noise). Computed each qualifying column's
combined per-channel std-dev after trimming 2px off each end (to
exclude the anti-aliased edge, which would otherwise look like drift
on a real flat bar too): real bar cores measured 0.0-1.7, the fang's
measured 31.7-56.6 -- a 20-30x gap, not a close call requiring careful
threshold tuning.

Added this as a second required condition in `resolve_tier_from_badge()`
-- a column only counts as a bar if its qualifying run is BOTH tall
enough (existing check) AND uniform enough in color (new check).
Validated against the full 915-crop real corpus before trusting it,
same discipline as every previous round: **100% (915/915)**, including
every previously-failing `poison_ii` instance. Re-integrated into the
full pipeline and re-measured end-to-end: **100.0% exact (icon+tier)
accuracy across all 1,971 real validation crops (1971/1971)** -- the
badge check now disagrees with the embedding 230 times and is correct
all 230 times, 0 wrong. Zero known failure modes remain in the
experimental classifier as of this update -- though "100% on every
real crop seen so far" is a claim about the data on hand, not a proof
that no icon could ever trip a similar false-positive in the future;
the classifier is still a standalone report/validation tool, not wired
into `capture_pipeline.py`, per the original scope this was built
under.

**Update: wired into `capture_pipeline.py` -- supports are live
production output now, not just a validation report.** Promoted
`match_support_icon()`, `resolve_support_name()`, and the tier-badge
logic (`_resolve_support_tier_from_badge`) directly into
`capture_pipeline.py`, following the exact same "reference catalog
built offline, matched live in production" split gems already use:

- `tools/export_support_reference_embeddings.py` (new) precomputes
  embeddings for every `assets/harvested_supports/` reference into
  `assets/models/support_reference_embeddings.json` -- unlike
  `export_gem_embedding_model.py`, this needs no `torch`, since it
  reuses the already-exported generic ResNet18 ONNX model via
  `onnxruntime` alone (the model was never gem-specific; only the
  *reference set* differs between gems and supports).
- `capture_pipeline.py` gained `match_support_icon()` (embedding icon
  match + badge tier override, same validated logic as
  `tools/match_support_icon.py`), `resolve_support_name()` (maps a
  resolved key to a display string, joining real icon+tier collisions
  with " or " rather than guessing), and `extract_support_names()`
  (subdivides an already-cropped `supports.png` into its fixed 6x5
  grid via `definitions/mercenary_regions.json`, ported as a direct
  copy of `tools/harvest_support_icons.py`'s own `load_grid()` math
  rather than importing it, since tools/ scripts are a separate
  offline-batch category from the live capture path).
- `process_capture()` now populates `record["supports"]`;
  `build_warrant_extracted_text()` prints each skill's supports right
  under it, row-aligned by INDEX against the raw (not known-pool-
  filtered) skill list, closing the exact gap that function's own
  docstring had flagged since it was written ("Supports... omitted
  entirely... unsolved as of this function").

Added a new confidence floor, `SUPPORT_MATCH_MIN_SCORE = 0.95`,
that `GEM_EMBEDDING_PRESENCE_THRESHOLD` didn't need a parallel
concern for: a live capture can show a support that's one of the 54
still-`unharvested` (icon, tier) combinations (see
`assets/support_icon_coverage.md`), and the classifier had never been
tested against a genuinely uncovered icon before this point. Checked
what real matches actually score first (700-sample real check: every
one landed at 0.981 or higher), so 0.95 leaves real margin below every
known-good case -- but unlike the gem threshold, there's no confirmed
real "wrong icon" score to calibrate a true floor/ceiling gap against,
since nothing genuinely uncovered exists in the real dataset to test
it with. Flagged plainly rather than glossed over: this is a real,
if reasoned, gap in an otherwise fully-validated pipeline.

Validated the actual wiring, not just the underlying logic already
proven in `tools/match_support_icon.py`: ran `extract_support_names()`
(the real production function) against every one of the 137 real
`captures/`/`__captures_*/` directories with a usable `warrant.txt`,
comparing its output directly against that file's ground truth --
**1,971/1,971 correct (100%)**, confirming the ported grid math
(`_load_supports_grid()`) produces byte-identical column/row
boundaries to `tools/harvest_support_icons.py`'s own `load_grid()`
and that nothing was lost or altered in translation from experimental
script to production function.

Not done, and explicitly out of scope for this pass: `record["supports"]`
doesn't feed `build_log_row()`/the TSV log at all yet, only
`warrant_extracted.txt` -- the same gap already flagged for skills
under "Open action items" ("skills/supports aren't in the TSV log...
only in warrant_extracted.txt").

**Update: real bug found on the very next real capture -- ambiguous
groups were being reported far more broadly than necessary, because
resolve_support_name() had no way to know WHICH skill was asking.** A
real Holy Relic capture showed `"Cooldown Recovery (Tier: 2) or Shock
Chance (Tier: 2) or DoT Multiplier (Tier: 2) or Chaos Penetration
(Tier: 2)"` and `"Greater Ironwood (Tier: 3) or Greater Physical as
Extra (Tier: 3)"` -- both real icon+tier collisions, correctly
detected as such, but reported as broadly as "every support that EVER
shares this icon+tier, across all 271 skills" rather than narrowed to
what Holy Relic itself can actually roll. Checked
`definitions/supports_by_skills.json` directly: Holy Relic's real
`PossibleSupports` includes `"Cooldown Recovery II"` but not `"Shock
Chance II"`/`"DoT Multiplier II"`/`"Chaos Penetration II"`, and
`"Greater Physical as Extra III"` but not `"Greater Ironwood III"` --
3 of those 6 candidates were never real options for this skill at all.

**Fixed**: `resolve_support_name()` now takes the row's own skill name
and culls a collision's candidate list against that skill's real
`PossibleSupports` (`_load_supports_by_skills()`, new) before falling
back to the full list -- only trusts the cull if it leaves at least one
candidate, so a genuine data mismatch shows the full honest list rather
than an empty or wrong answer. `extract_support_names()` gained a
`skill_names` parameter (row-aligned, same convention `record["skills"]`
already uses) to thread this through; `process_capture()` now computes
`skill_names` once and passes it to both `record["skills"]` and
`extract_support_names()`.

Re-validated across every real capture with a usable `warrant.txt`
(138 of them, 2,005 individual supports) with the fix live: **100%
correct, same as before the fix** -- but reported ambiguity dropped
from 432/2,005 (21.5%) to 104/2,005 (5.2%), a real ~76% reduction, with
zero new wrong answers. The 104 remaining are checked, not assumed,
genuine: e.g. Dominating Blow's `"Greater Minion Damage (Tier: 3) or
Greater Minion Life (Tier: 3)"` stays ambiguous because Dominating
Blow's own `PossibleSupports` really does include both -- the skill can
roll either, so no amount of skill-context culling can resolve it
further; only real image-level disambiguation could, the same
structural ceiling gems' Blade Ambusher case had. Two `tests.py`
fixtures updated to pass `skill_names` and reflect the now-narrower
(and in one case, fully resolved) expected output.

**Update: first live capture with the culling fix already deployed
(not something hand-picked to exercise it) came back a perfect
match.** `20260916_165945` (Toxicologist) -- `diff`'d its real
`warrant.txt` against the pipeline's own `warrant_extracted.txt`
directly: byte-for-byte identical once CRLF (the game's own clipboard
copy) is normalized against this project's LF write convention. Zero
ambiguity anywhere in this one, unlike the two curated fixtures above
-- real confirmation the fix doesn't just narrow already-known-hard
cases, an ordinary capture keeps working exactly as before. Added as a
third permanent `tests.py` fixture (`SUPPORT EXTRACTION` now 3/3).

### Support icons: many are shared by design, not a recognition failure -- and skill-context resolves most, not all, of it

Real extracted data (`tools/extracted_dats/mercenarysupports.json`, 266
support entries) settles two related questions raised while starting the
supports work in earnest, both checked directly rather than assumed.

**Tier variants of the same support always share one icon.** Grouped
every entry by its `SupportFamily` field (a numeric id GGG's own data
uses to group a support's Low/Mid/High -- Lesser/Normal/Greater --
rows) and checked `GemIcon`: 100% of the time, all tiers of one real
family use the identical icon path. The in-game I/II/III badge is
confirmed to be a UI overlay, not a separate asset, exactly as
suspected. `assets/support_raw_asset_names.json` was rewritten to key
by core name (`"Fire Penetration"`, not `"Fire Penetration II"`) with
one entry per real support rather than one per tier -- a support with
no `SupportFamily` (no Low/Mid/High siblings at all, e.g. `Greater
Curse Effect`, `Return`) keeps its own literal in-game `Name` string
verbatim, tier wording included, since GGG's own data doesn't always
strip it consistently for those (compare `Return`, no prefix, vs.
`Greater Curse Effect`, prefix baked in even with no siblings to
distinguish it from).

**But a much bigger, separate thing is also true: a shared icon is not
only a tier artifact.** Grouping ALL 266 entries by `GemIcon` (not just
within one `SupportFamily`) found roughly **45% of all supports (119 of
266) render with one of just six generic, attribute-coded placeholder
icons**, shared across entirely unrelated real supports:

```
MercGoldSupportGem.dds        -- 75 different "Gilded X" supports
MercSilverIntSupportGem.dds   -- 16 different supports (Curse Effect, Cooldown Recovery,
                                  Shock Chance, DoT Multiplier, Chaos Penetration, +3 others)
MercSilverDexIntSupportGem    -- 13 different supports
MercSilverStrSupportGem       --  6 different supports
MercSilverStrIntSupportGem    --  6 different supports
MercSilverStrDexSupportGem    --  3 different supports
```

Plus a few ordinary named supports doing it in miniature ("Minion
Damage"/"Minion Life" share `MinionDamage.dds`; "Combustion" shares
`ChancetoIgnite.dds` with the "Ignite Chance" tier family). This is a
hard ceiling, not a solvable recognition problem -- these icons are
pixel-identical in the game's own art, so no algorithm (classical CV or
the embedding approach evaluated above) can distinguish them by image
content alone.

**Tested directly whether per-skill context resolves this**, following
a real hypothesis: even if an icon is globally ambiguous across all 266
supports, a SPECIFIC skill's own `PossibleSupports` list (`definitions/
supports_by_skills.json`) is much narrower -- if no two of a skill's own
candidates ever share both the same icon AND the same tier (the tier
badge IS visible on the real icon, so icon+tier together is the real
resolving unit, not icon alone), then knowing which skill you're
looking at would fully resolve identity despite the global sharing.
Checked against all 225 skills with a non-empty support pool:

```
Skills with ZERO internal icon+tier collisions:      175 / 225  (78%)
Skills with at least one real internal collision:     50 / 225  (22%)
```

**The hypothesis holds for about 3 in 4 skills, not universally.** The
50 real collisions cluster into a small, recurring, enumerable set of
thematic pairs/triples rather than being spread evenly across the
support pool -- the same few confusable groups reappear across many
skills:

- `Minion Damage` vs. `Minion Life` (`MinionDamage.dds`) -- nearly every
  minion-summoning skill (Summon Skeletons, Summon Raging Spirit, Herald
  of Purity, Dominating Blow, Absolution, Raise Spectre of Transience,
  Mirror Arrow, Blink Arrow, ...).
- `Throwing Speed` vs. `Trigger Radius` (`MercSilverDexIntSupportGem`)
  -- nearly every trap skill (Bear Trap, all the *_Trap skills, Spectral
  Throw/Helix of Trarthus, Blade Trap).
- `Cooldown Recovery` vs. `DoT Multiplier` vs. `Shock Chance` vs.
  `Greater Lucky Lightning Damage` (`MercSilverIntSupportGem`, various
  2-3-way combinations depending on the specific skill) -- Stormcall,
  Desecrate, Vaal Reap, Molten Well, Oil Slick, several others.
- `Ignite Chance` vs. `Combustion` (`ChancetoIgnite.dds`) -- Fireball,
  Flameblast, Rolling Magma, Burning Arrow, and variants.
- `Freeze Chance` vs. `Brittle Chance` (`MercSilverDexIntSupportGem`) --
  Ice Nova and its variants, Eye of Winter.
- `Ironwood` vs. `Physical as Extra` (`MercSilverStrIntSupportGem`) --
  Holy Flame Totem, Shockwave Totem of Shocking.

**Practical implication**: for the 78% of skills with no internal
collision, icon + skill-context is enough to fully resolve support
identity once icon-matching itself works (no ML needed to distinguish
what's already unambiguous). For the 22% with a real collision, no
image-based approach can do better than narrowing to the small known
set above and reporting it as ambiguous (e.g. "Cooldown Recovery or
DoT Multiplier") rather than guessing -- same "don't guess, flag it"
discipline already used for gems (`"Unknown (Blade Ambusher)"`) and
skills (the unrecognized-row flag in `warrant_extracted.txt`).

**Implemented, then corrected**: `tools/extract_mercenary_skills.py`
first shipped this as `definitions/family_by_support.json` --
`{support Name: SupportFamily}`, the bare numeric id and nothing else.
Real feedback on it: unclear what actually consumes it, and the raw
values ("0", "15", null) are meaningless on their own, correctly
pointed out as a step away from this project's own stated "name-keyed,
not the game's raw ID structure" principle (`definitions/README.md`).
The underlying miss: `SupportFamily` was only ever a means to VERIFY the
icon-sharing hypothesis above, not something downstream code would
actually want to look up by itself -- the real load-bearing fact a
future icon-matching feature needs is each support's *icon*, for all
265 real supports, not just the ~9 with a hand-gathered asset in
`assets/support_raw_asset_names.json`. Replaced with
`definitions/supports.json` -- `{support Name: {"icon": ..., "family":
...}}` -- keeping `family` as clearly-labeled supplementary context
(directly comparable against another entry's, not meaningful alone)
rather than the sole payload. `Minion Damage`/`Minion Life` now read
straightforwardly as `{"icon": "miniondamage", "family": 40}` /
`{"icon": "miniondamage", "family": 44}` in one place -- same icon,
different family, self-explanatory without a second lookup.

**Update: `assets/support_raw_asset_names.json` itself was then removed
entirely**, once `definitions/supports.json` existed -- real
recognition, not just a rename: every value in the hand file was
already present, identical, in the auto-generated one, so it was pure
duplication with a staleness risk, not a second useful source of
truth. Confirmed there's a cheap way to recover its one distinct
purpose (which display names have a real gathered asset file) without
keeping a hand-maintained JSON in sync at all: compare
`definitions/supports.json`'s keys against what's actually sitting in
`assets/pending_supports/` directly. `tools/README.md`'s "Skill support
assets" section was updated to point at `definitions/supports.json`
instead, and to note supports are the one asset type (unlike currency/
gem/scarab/skill) where the name -> icon lookup itself is fully
automated, since `mercenarysupports.json`'s own row already carries
`GemIcon` directly -- no `_rid` chain to trace, unlike skills' three-file
hop through `grantedeffects.json`/`activeskills.json`.

**Update: `family`'s bare int replaced with its human-readable name.**
Real, immediate feedback on the first version of `definitions/
supports.json`: `"family": 15` (or `0`, or `null`) means nothing to a
reader on its own. `mercenarysupportfamilies.datc64` (a small, 53-row
`_rid -> Id` table -- `Id`s like "Cooldown", "MinionDamage",
"ShockChance") resolves that in one more hop, the same `_rid` lookup
pattern used everywhere else in `extract_mercenary_skills.py`.
`Minion Damage`/`Minion Life` now read as `{"icon": "miniondamage",
"family": "MinionDamage"}` / `{"icon": "miniondamage", "family":
"MinionLife"}` -- self-explanatory without cross-referencing a second
file to know what "40" and "44" meant. `family`'s actual USE hasn't
changed (still just equality comparison between two entries, per the
"how would family be used" discussion above) -- this only fixed how
legible the value is when a human is the one reading the file.

**Aside, a data-quality note found while checking this, not yet
acted on**: several skills' `PossibleSupports` lists in `definitions/
supports_by_skills.json` contain the exact same support-family block
listed twice verbatim (e.g. `Reap` lists the full `DoT Multiplier`
Lesser/Mid/Greater trio twice; `Boiling Blood` lists `Swift Affliction`
twice). Real, present in the extracted data, not an analysis artifact --
likely the raw game table has two separate weighted roll pools that
happen to both include the same support. Doesn't change identity
resolution (a duplicate entry doesn't create a new collision, it's the
same name twice), but would double-count if this list's length were
ever used to estimate roll probability or support-count plausibility.

### Icon-collision-by-family report: for each shared icon, which identities actually appear together on the same skill

A follow-on to the icon+tier collision check just above, but asking a
different question: not "does any one skill have an internal
collision" but, for each of the shared icons themselves, which pairs
of the different supports/families sitting behind that icon ever show
up together in the SAME skill's `PossibleSupports` at all (regardless
of tier) -- i.e. is the collision even a real possibility for that
pair, or are they never both on the table for any skill to begin with.
Re-derived directly from `definitions/supports.json` +
`definitions/supports_by_skills.json` rather than pulled from memory,
using the same `identity` concept as the `SupportCountTier` section
below (family name when non-null, else the corrected Lesser/bare/
Greater grouping for the `Trigger Radius`/`Leech` null-family gap
documented there, else the support's own literal Name).

Of the 266 real supports, exactly **7 icons** back more than one
distinct identity (matching the 6 generic icons already listed above,
plus `MinionDamage.dds`, which the plain-supports paragraph above only
mentioned "in miniature"):

```
mercgoldsupportgem.dds        -- 73 identities (69 "Gilded X" + CritChance,
                                  IncreaseAreaOfEffect, MultipleProjectiles, Pierce)
mercsilverintsupportgem.dds   --  8 identities (ChaosPen, Cooldown, DotMulti,
                                  Greater Curse Effect, Greater Lucky Lightning
                                  Damage, Maximum Shock Effect, Minion Caustic
                                  Death, ShockChance)
mercsilverdexintsupportgem.dds --  6 identities (Arcane Traps, Brittle Chance,
                                  FreezeChance, PhysGainAs, TrapThrowSpeed,
                                  [Trigger Radius])
mercsilverstrsupportgem.dds   --  2 identities (RageWarcry, StrikeRange)
chancetoignite.dds            --  2 identities (Combustion, IgniteChance)
mercsilverstrintsupportgem.dds --  2 identities (PhysGainAs, TotemDefences)
miniondamage.dds               --  2 identities (MinionDamage, MinionLife)
```

(`MercSilverStrDexIntSupportGem`, the 7th generic icon from the
paragraph above, backs only 3 raw entries that all resolve to ONE
identity once tier-siblings collapse -- no internal collision possible,
so it drops out of this report entirely.)

**Correction, caught by a direct user question minutes after this
section was first written**: the paragraph originally here claimed 59
skills have a `Gilded *` identity "colliding" with one of `CritChance`/
`IncreaseAreaOfEffect`/`MultipleProjectiles`/`Pierce`, using
`captures/20260914_091911` (Orb of Storms' `Gilded Jolt III` and
Stormcall's `Gilded Voltage III`, same warrant, visually identical gold
icon) as "the ambiguity in its purest form." Asked directly: how is
that a problem if the two just line up with different skills -- no
collision? Checking again, correctly, that's right and the original
claim was wrong. The bug: that 59-skill count came from "do both
identities appear anywhere in this skill's `PossibleSupports` list",
not from "do the two ever actually render with the same icon AND the
same tier within this one skill's own pool" -- the actual definition of
a real visual collision, already established and used correctly by the
"Tested directly whether per-skill context resolves this" check
earlier in this section. Those aren't the same question: some Gilded
supports carry a real, populated `family` -- e.g. `Gilded Jolt` and
`Gilded Voltage` both resolve to `family: "CritChance"` in
`definitions/supports.json`, same as the plain `Critical Chance` tiers
-- so `identity()` correctly collapses them together, and any OTHER
skill that also happens to offer a plain `Critical Chance` tier alongside its own
different Gilded slot got flagged as a "collision" even though the
plain tier renders with a completely different icon
(`increasedcriticalstrikes`, not `mercgoldsupportgem`) in that skill's
own pool -- no real ambiguity, just two unrelated identities that
happen to share a family label.

Re-run with the correct definition (same icon AND same tier, within one
skill's own `PossibleSupports`, exactly as the earlier per-skill check
already does): **`mercgoldsupportgem` (Gilded) and
`mercsilverstrsupportgem` have zero real collisions, across all 271
skills, full stop.** No skill has ever been found where two different
real supports render identically once you know which skill you're
looking at, for either of those two icons. The Orb of Storms/Stormcall
example is not a counterexample to anything -- it's just two different
skills that each independently offer one unique Gilded support, exactly
as the "deterministic per-skill icon->support mapping" hypothesis from
the top of this section predicts, and exactly why the icon looking
identical across them doesn't matter in practice: `warrant_extracted.txt`
generation already knows which row belongs to which skill.

The other 5 shared icons DO have real, verified same-skill same-tier
collisions -- but they're exactly the same 6 thematic pairs already
enumerated earlier in this section (Minion Damage/Life, Throwing Speed/
Trigger Radius, the Cooldown/DoT/Shock/Lucky-Lightning cluster, Ignite
Chance/Combustion, Freeze Chance/Brittle Chance, Ironwood/Physical as
Extra) -- this report doesn't add a new finding for those, only
re-confirms them with the icon+tier-exact method instead of the flawed
co-occurrence one. Net correction: **Gilded supports specifically are
never actually ambiguous in this pipeline** -- the "should ambiguous
supports even matter for price-checking" question raised by the user
that prompted this whole line of investigation turns out to be moot for
the single largest support category (69 of 266 real supports), since
none of them were ever really ambiguous to begin with once skill
context is used, which this pipeline always has.

### `SupportCountTier` actually counts distinct FAMILIES, not raw pool entries -- validated against 426 real equipped instances, one real exception found on audit

A real structural hypothesis, not obvious from anything documented so
far: `PossibleSupports`' flat list looks like N independent slots (e.g.
Blink Arrow has 18 entries), but grouping by `family` collapses it to
far fewer real choices -- Blink Arrow's 18 entries are only **6 distinct
families** (Cooldown, Duration, AttackSpeed, ProjectileSpeed,
MinionDamage, MinionLife), each contributing exactly its 3 tier
variants. The hypothesis: the game actually rolls N *families* (N
constrained by `SupportCountTier`'s range), not N raw list entries --
which would mean a skill can never equip two different tiers of the
same family at once, but CAN equip two different families that happen
to share an icon (exactly the Minion Damage + Minion Life case already
documented above).

**Checked directly against every real warrant this project has** (426
real equipped-skill instances across `captures/` and every archived
`__captures_*/` folder, not a sample):

```
Instances where the same family appears twice on one skill:        0 / 426
Instances where the distinct-family count falls OUTSIDE the
  skill's own SupportCountTier range (None=0, Low=1-2,
  Medium=2-3, High=3-5):                                            0 / 426
```

**Zero exceptions either way, at the time this was checked.** This is a
real, load-bearing fact about how the roll mechanic works, not just a
coincidence of the small sample checked earlier: `SupportCountTier`'s
range is a count of *families*, and a family, once used, is (almost)
never drawn again for that same skill instance. First real practical
use for `family` beyond human legibility (see the "how would family be
used" discussion above) -- if a skill's family budget is known and some
slots are already resolved, the remaining ambiguous slot(s) can be
constrained to families NOT already used, narrowing a genuine icon
collision further than skill-context alone does.

**Correction, found during a later audit pass of this file**: the
"same family appears twice" check above only excluded a repeat when
BOTH names resolved to the same non-null `family` value -- but two real
supports (`Trigger Radius`, `Leech`) have `family: null` on ALL their
tier variants in the raw game data despite clearly being real
Lesser/Mid/Greater trios (same name pattern, same icon -- see the icon-
collision report above for how this was found). That's a real data gap
in `mercenarysupports.json` itself, not a project bug. Re-checking with
that gap corrected for (treating `Trigger Radius`'s 3 tiers, and
`Leech`'s 3 tiers, as one identity each, the way `family` already does
for every other support) found **one real exception**:
`__captures_20260914/20260912_163151` (Spectral Throw of Trarthus, a
Blade Ambusher rucksack) has both "Trigger Radius" and "Greater Trigger
Radius" equipped simultaneously -- two tiers of the same real support,
which the hypothesis says should never happen. Checked whether this was
forced (not enough other families to reach the required count): no --
this skill's `SupportCountTier` is High (3-5) and it has 7 true distinct
families available once the Trigger Radius split is corrected, so 5
was reachable without repeating any family. The game rolled a repeat
anyway. **Revised claim: the "families never repeat" rule holds in
essentially every real case checked (1 exception in several hundred
real instances), not with zero exceptions** -- strong enough to use as
the primary heuristic, not strong enough to treat a repeat as
structurally impossible when resolving a genuine ambiguity.

**A related hypothesis, raised and checked separately**: `mercenarybuilds.json` carries an offensive/defensive archetype field this project otherwise ignores -- could a skill's archetype restrict it to only ONE of a pair like Minion Damage/Minion Life, rather than both being genuinely independent draws? Checked directly against real captures (visual inspection of `supports.png` cross-referenced against `skills.png`, since support-name OCR/matching isn't implemented yet -- see "Open action items" below): both minion-summoning skills in `captures/20260914_092050` (Absolution, Raise Spectre of Transience) show both a Minion Damage-family and a Minion Life-family icon equipped simultaneously, not just one or the other. **Refuted** -- no offensive/defensive restriction on this pair; consistent with the "no forced pairing, independent draws" finding immediately below, not a separate mechanic.

**Second question, also checked**: is there hidden weighting that
tends to bundle specific families together (e.g. "Minion Damage always
comes with Minion Life")? Compared real repeated observations of the
same skill across independent captures -- `Flame Dash` (12 real
instances) shows 4 different family combinations (`CastSpeed+Duration`,
`CastSpeed+Cooldown`, `Cooldown+Duration`); `Rallying Cry` (8 real
instances) shows 8 DIFFERENT combinations, no repeat at all;
`Lightning Warp` (7 instances) alternates between 2 combinations with no
obvious pattern. **No evidence of forced pairing** -- family selection
looks like independent draws from the skill's available family pool,
not a fixed bundle. Confirms the intuition that prompted checking this:
Minion Damage and Minion Life co-occurring (real, documented above) is
a coincidence of independent draws landing on both, not a designed
package -- so it should be treated as a genuine per-instance
possibility, not an "always together" rule that would let one imply
the other.

## Scarabs: impact on non-gem coverage for presence detection

Scarabs turned out to be a much bigger, more structured space than
"one problematic currency item" -- real investigation (prompted by
several real `is_gem_present()` false positives, see "Embeddings for gem
PRESENCE" above)
found scarabs vary along at least two independent axes: **visual
type/shape** (connected-leg, connected-leg+winged, several distinct
"unconnected leg" postures, winged, halo, winged-halo, horned) and
**domain** (league-specific -- each league has its own filigree symbol
on an otherwise-shared shape -- vs. a small, fixed "Miscellaneous" set
with no symbol). Colors vary loosely by league theme but aren't a hard
rule. Full color reference (a real PoE stash screenshot with the
Miscellaneous scarabs identified) was used to cross-check captured
samples -- not committed to the repo (a live gameplay screenshot, not
project-owned asset), but the resulting classification is captured in
`test_data/scarabs/scarab_*.png`'s current set (originally
`test_data/non_gems/`, split into `test_data/currencies/` and
`test_data/scarabs/` once scarabs needed their own taxonomy).

**What actually matters for presence detection, confirmed with real
scores**: shape
correlates with score risk -- connected-leg shapes score meaningfully
higher (`0.67-0.74`) than winged/halo/crown/horned shapes (`0.61-0.69`),
a real, actionable signal. **What doesn't appear to matter**: league
identity (filigree symbol) and Miscellaneous-vs-league domain -- no
evidence either changes score independent of shape+color. Practical
sampling rule that came out of this: cover each distinct *shape* once
(preferring a warm/gold-toned color, the other established risk
factor from earlier presence-detection work), not every league's
color variant of the same shape. Chasing full per-league coverage
would have been a large, low-payoff effort; this narrows it to a
tractable one.

Two confirmed-real-but-uncaptured gaps remain: a Miscellaneous
"bone/crown" shape and a Miscellaneous "winged" shape (both seen
directly in a real stash, matching shapes already captured for
league-specific colors) -- worth grabbing if encountered, not worth
actively hunting given the shape-not-domain finding above.

**Horned is confirmed Miscellaneous-only** (never league-specific) --
directly confirmed, not inferred, with exactly 7 color variants total
per the real scarab stash tab. 2 of 7 captured so far (`scarab_4`,
`scarab_30` -- blue and magenta, scores `0.6108`/`0.6306`, both
comfortably low and consistent with each other). The other visual
types don't have this kind of hard domain restriction -- horned is the
one confirmed domain-exclusive shape found so far.

**Completing the remaining 5 horned colors is a "nice to have," not a
priority** -- horned scarabs are among the rarer drops, so the
collection cost is real, while the two samples on hand already show
the same low-risk pattern every other shape's color variance has shown
(color shifts the score a little, not toward the boundary). Revisit
only if a future horned sample unexpectedly scores meaningfully higher
than `0.61-0.63` -- that would be the signal this assumption doesn't
hold and the rest of the set suddenly matters.

## Pending raw-asset trial: currency, scarabs, and skill-name OCR validation

Prompted by the addition of `assets/pending_currency/`, `assets/pending_scarabs/`,
and `assets/pending_skills/` (raw game-extracted icons, with each folder's
display-name -> raw-asset-name mapping recorded in the matching
`assets/*_raw_asset_names.json`). Two questions: (1) can these raw assets be
used directly for image comparison, without the compositing work gems
needed, since currency/scarabs/skills are documented above as rendering
without extra in-game effects? (2) is OCR still the right approach for
skill-name identification now that skill icon assets exist as an
alternative? Both were tested against real data; **neither was transitioned
into production code** — this is exploratory only.

### Currency: raw assets carry real signal, but aren't a clean drop-in yet

All 6 currencies with both a `pending_currency` raw asset and a real
`test_data/currencies` capture (Ancient Orb, Divine Orb, Exalted Orb, Orb of
Scouring, Orb of Unmaking, Regal Orb) were compared self-vs-self and
self-vs-every-other, using the existing `_icon_similarity_score`/
`_icon_similarity_score_multiscale` with no compositing at all:

```
                    fixed-position   multi-scale (best-fit scale)
Ancient Orb              0.8329        0.9078  (1.15)
Divine Orb               0.7577        0.9183  (1.10)
Exalted Orb              0.7401        0.8772  (1.15)
Orb of Scouring          0.9401        0.9434  (1.15)
Orb of Unmaking          0.7584        0.8536  (1.15)
Regal Orb                0.9198        0.9098  (1.15)
```

Multi-scale search helps substantially here too (same pattern as gem
presence detection), and every best-fit scale lands above 1.0 (1.10-1.15) --
the raw sprite consistently needs to be rendered larger than the real
in-game icon to align, a real, consistent, if unexplained, offset.

**Cross-comparison (each raw asset vs. all 6 real captures, multi-scale):**
5 of 6 raw assets correctly rank their own real currency #1. The exception:
Orb of Unmaking's raw asset scores higher against real Orb of Scouring
(0.8752) than against real Orb of Unmaking (0.8536) -- a genuine collision,
same category of near-miss as the gem Bladefall/Throw case. **Conclusion**:
real signal, promising as a supplementary/fallback data source, but not
accurate enough on this small sample (n=6) to drop in as a substitute for
real captures without further work (e.g. resolving the Scouring/Unmaking
collision, more samples).

**Follow-up: the stack-count digit is confirmed NOT masked out, but a
direct controlled test found no real evidence it drives cross-item
collisions.** Visually inspecting the real captures confirmed the first
half: the in-game stack-size digit is rendered as a plain, bold, uncovered
digit straight over the item art, no blotting circle or background behind
it, and the `pending_currency` raw assets have none at all (bare extracted
sprites) -- so it is genuinely unmasked, unaccounted-for content on the
real-capture side of every raw-vs-real comparison.

Whether it actually explains the Scouring/Unmaking collision was then
tested directly, not just inferred, once more real stack-count variance
turned up in `test_data/currencies/` (Ancient Orb: digit 1 x3, 2 x1, 3 x1;
Orb of Scouring: digits 4, 5, 1, 2, 3, 10, one sample each). Two controlled
comparisons, holding item identity constant to isolate the digit's own
contribution:

```
Ancient Orb self-variance (same real item, different sample pairs):
  same-digit pairs   (n=3):  mean 0.7157  (0.6542-0.7844)
  different-digit pairs (n=7): mean 0.7570  (0.6633-0.8996)

Ancient Orb vs. Orb of Scouring (different real items):
  same-digit pairs      (n=5): mean 0.6939  (0.5845-0.8015)
  different-digit pairs (n=25): mean 0.6882
```

**Neither test supports the hypothesis.** Same-digit pairs do NOT score
higher than different-digit pairs in either case -- if anything, Ancient
Orb's own same-digit pairs scored slightly LOWER than its different-digit
pairs, the opposite of what a shared-digit-inflates-similarity mechanism
would predict, and the cross-item same-digit vs. different-digit means are
within noise of each other (a huge per-pair spread, 0.58-0.80, dwarfs the
~0.006 difference between the two means). **Correction to the earlier
"plausible contributor" framing**: the digit is real and unmasked, but
whatever is actually driving the sample-to-sample variance seen in these
real currency comparisons (candidates not yet checked: which rucksack
quadrant each sample came from, and that quadrant's own decorative
border/vignette bleeding into the crop -- visually, `orb_of_scouring_5.png`
and `_8.png` look framed differently than the others) isn't demonstrated
to be the stack-count digit. Don't cite the digit as an explanation for
this collision without re-deriving it -- this correction supersedes the
original claim.

### Scarabs: raw-asset shape signal broadly corroborates the known taxonomy, one real limitation found

No real `test_data/scarabs` sample has a confirmed display-name mapping
(they're catalogued by visual shape/domain only -- see "Scarabs: impact on
non-gem coverage" above), so an exact identity trial isn't possible yet.
Instead, each `pending_scarabs` raw asset (referred to here by its own raw
internal filename, not a translated label -- see
`assets/scarab_raw_asset_names.json`) was scored (multi-scale) against
every real scarab sample to see whether its top real-world match lines up
with independently-established real shape identifications from earlier
taxonomy work:

```
raw asset                    top real match   score
altlesserscarabmisc.png      scarab_23.png    0.8189
greaterscarabmisc1.png       scarab_17.png    0.8077
greaterscarabmisc2.png       scarab_17.png    0.8038
lesserscarabmisc.png         scarab_23.png    0.8602
superscarab1.png             scarab_30.png    0.7964
tier4scarabmisc.png          scarab_29.png    0.8428
```

4 of 6 line up cleanly: both `greaterscarabmisc*` (connected-leg shape)
top-match `scarab_17` (the confirmed real connected-leg sample);
`superscarab1` (confirmed to be Horned Scarab of Bloodlines specifically)
top-matches `scarab_30` (a confirmed real horned scarab); `tier4scarabmisc`
(halo shape) top-matches `scarab_29` (a confirmed real halo).
`lesserscarabmisc` (legged/unconnected-leg shape) top-matches `scarab_23`,
itself confirmed misc "legged" -- also correct.

**Follow-up: directly inspected why `altlesserscarabmisc` (a winged shape)
top-matched `scarab_23` (a legged, non-winged shape) instead of a winged
sample -- confirmed a real detection weakness, not just a missing test
sample.** Visually, `lesserscarabmisc.png` and `altlesserscarabmisc.png`
share the identical body/legs/open-arms silhouette; the only difference is
a pair of wings on the `alt` version, occupying a small peripheral area of
the icon. Compared directly against each other: `lesserscarabmisc` vs.
`altlesserscarabmisc` scores `0.9096` fixed-position / `0.8003`
multi-scale despite the wing difference -- some discrimination exists (not
identical), but the margin the wings alone contribute is small relative to
the shared body dominating the score. `scarab_23` (visually a non-winged,
"legged" shape by direct inspection, matching `lesserscarabmisc` far more
than `altlesserscarabmisc`) scores `0.8602` against `lesserscarabmisc` but
only `0.8189` against `altlesserscarabmisc` -- consistent with `scarab_23`
genuinely being the non-winged shape, not a labeling error. Likely
mechanism: `_ICON_COMPARE_SIZE` is 48x48 -- a thin, peripheral wing
appendage spans only a small fraction of that resolution and is an easy
casualty of the resizing/anti-aliasing involved, while the large shared
body silhouette dominates the score regardless. **This is a real,
plausible resolution-driven limitation of whole-icon cross-correlation at
the current comparison size**, not simply a missing real winged sample
(though a missing sample is also true here -- no confirmed real winged
scarab exists in `test_data/scarabs` at all, so this axis has never
actually been validated against real capture data either way). Getting
real league-specific winged examples (offered, not yet done) would help
directly -- it's the only way to test whether the algorithm actually
ranks a true winged real capture below its winged raw asset, versus this
inconclusive raw-asset-vs-raw-asset/proxy comparison.

**Update: a confirmed real winged sample (`scarab_7.png`, a league-specific
`altlesserscarab` type) closes the gap this section flagged.** Head-to-head,
`scarab_7` scores higher against `altlesserscarabmisc` (winged) than
against `lesserscarabmisc` (non-winged), in the correct direction, for the
first time with a genuinely confirmed real winged sample rather than a
negative-control inference: `0.8064` vs. `0.7945` multi-scale (margin
`0.0119`), `0.7397` vs. `0.7354` fixed-position (margin `0.0043`). **The
wing IS detected** -- confirming the "weak but present" reading above,
not overturning it.

But margin is the operative word: in the full cross-comparison against
all 30 real scarabs, `scarab_7` is only `altlesserscarabmisc`'s 4th-best
real match (`0.8064`), still beaten by `scarab_23` (`0.8189`, legged),
`scarab_17` (`0.8119`, connected-leg), and `scarab_5` (`0.8086`,
connected-leg per earlier taxonomy) -- none of them winged. So the
signal that correctly separates winged-from-non-winged head-to-head is
real but small (~0.01-0.04), and gets buried by larger, unrelated
similarity (likely color/tone) from other real scarabs once the full
pool is in play. **Refined conclusion**: not "wings can't be detected" --
they can, narrowly -- but at `_ICON_COMPARE_SIZE`'s 48x48 resolution the
wing signal is too weak to reliably win a multi-way comparison against
real population noise. A finer comparison resolution or a
region-weighted approach (weighting the peripheral wing area more, not
just whole-icon downsampling) is the concrete next thing to try if this
axis needs to be reliable, rather than more real samples alone.

**Conclusion**: raw scarab assets carry real, usable shape signal --
5 of 6 now confirmed correct in direct head-to-head terms (the wing case
included, once `scarab_7` gave it a real ground truth), though the wing
case's signal is too weak to win a full multi-way comparison against
real population noise. Promising enough to revisit once more exact
real-vs-raw identity pairs exist. The wing case specifically suggests
whole-icon comparison at 48x48 may need a finer-resolution or
region-weighted approach before thin peripheral features (wings, small
overlays) can be trusted to discriminate reliably in a real multi-way
comparison, not just head-to-head.

### Skills: OCR validated across real data, a real psm bug found and fixed, now wired into production

Tested `extract_skill_names()` against a real capture's `skills.png`
region, checked against that same capture's real `warrant.txt` ground
truth. The very first real test (Infamous Warpriest,
`__captures_20260914/20260913_135120` -- `captures/` gets cleared/archived
periodically as new sessions are captured; this one moved, it wasn't
lost) got all 6 real skill names back exact, plus one spurious extra
line that didn't match anything and was left uncorrected rather than
mis-corrected into a real skill name.

**A second, much larger real test (7 fresh real captures, each with a
paired real `warrant.txt`) found a genuine bug in the OCR config, not
just noise.** `extract_skill_names()` was originally using `--psm 3`
(Tesseract's automatic full-page segmentation). On 6 of 7 captures this
read every real skill name correctly. On the 7th (Infamous Bloodletter,
`test_data/skills/infamous_bloodletter_corvil.png`), `--psm 3` silently
read only 2 of 6 real, clearly-legible skill names (`Determination`,
`Dash`) -- `Bloodthirst`, `Blood Mortar`, `Lacerate`, and `Perforate` were
dropped entirely, with no error or "didn't match" warning, because
Tesseract's layout analysis never segmented them as text rows at all.
Direct comparison of psm modes on that same image:

```
--psm 3:  'Determination', 'Dash'                                          (2 of 6 real skills)
--psm 6:  'Bloodthirst', 'Blood Mortar', 'Lacerate', 'Perforate',
          'Determination', 'Dash'                                          (6 of 6, correct)
```

`--psm 6` (assume a single uniform text block) was re-checked against all
7 real captures: identical, fully-correct results to `--psm 3` on the 6
that already worked, plus the fix on the 7th -- zero regressions found on
that sample. `extract_skill_names()` switched outright to `--psm 6`.

**Third round: a MUCH bigger real sweep (66 captures across `captures/`
and archived `__captures_*` folders, every one with a real paired
`warrant.txt`) found that the outright psm 3 -> psm 6 switch just traded
one set of silent failures for a different one.** Comparing
`extract_skill_names()`'s pool-filtered output against each capture's
real warrant-derived skill list turned up 10 mismatches -- and every
single one was `--psm 6` dropping 1-4 real skills that `--psm 3` read
completely correctly (the two most dramatic: a Blade Ambusher capture
where psm 6 kept only 2 of 6, and the ORIGINAL Infamous Warpriest test
capture from earlier in this section, which had looked clean under psm 3
the whole time and turned out to silently lose `Smite` under psm 6).
Direct side-by-side on all 10 real mismatches: **psm 3 was the correct,
complete 6-of-6 result in every single one** -- the exact opposite
pattern from the Corvil case. Neither mode is reliably better; they drop
different real rows on different real captures, apparently depending on
some layout/color interaction with Tesseract's segmentation that hasn't
been further isolated.

**Fix: run both passes, keep whichever matches more of the known-skill
pool** (`extract_skill_names()`, ties go to psm 3). Considered and
rejected: merging the two passes' lines into one combined list. That
looks appealing (union the matched names, more coverage) but breaks real
on-screen order -- checked directly, and the known-skill pool's own
order (Primary+Secondary+Utility, from `skills_by_mercenary.json`) does
NOT reliably match true screen order (e.g. Infamous Warpriest's pool
lists Beacons of Faith before Dominating Blow; the real screen order is
the reverse), so a partial pass's names can't just be re-sorted into pool
order and merged in -- only a single pass's own line order is trustworthy
end to end. Re-ran the full 66-capture sweep after this fix:
**0 mismatches.** Added the two most decisive failing cases as permanent
fixtures (`test_data/skills/blade_ambusher_eli.png`,
`infamous_warpriest_raskor.png`) alongside the original Corvil case, so
`tests.py`'s `run_skill_name_extraction_tests` now guards against a
regression in either direction (psm 3 dropping rows, or psm 6 dropping
rows), not just the one psm 6 was originally built to fix.

**Conclusion: OCR remains the right primary approach for skill-name
identification, and is now validated across 66 real captures (0
mismatches after the dual-pass fix) with zero need for an icon-matching
fallback.** The `assets/pending_skills/` icon assets remain available as
a fallback path if a differently-rendered or harder-to-OCR skill name is
ever encountered, but nothing observed so far justifies building it. Two
Tesseract calls per capture instead of one is the real cost of this fix
-- accepted the same way `is_gem_present_in_rucksack`'s 25-40x slowdown
over the old presence check was: not real-time critical, keypress-
triggered, and correctness mattered more than the extra milliseconds.

**Implemented and wired in**: `process_capture()` calls
`extract_skill_names()` and `extract_level()` and stores both in
`record["skills"]`/`record["mercenary_level"]`.
`build_warrant_extracted_text()` (`capture_pipeline.py`) formats a
record into the same plain-text layout as a real Mercenary Warrant --
Name, Build, Mercenary Level (when extraction succeeds), and one block
per real (pool-filtered) skill name -- and `on_capture()` writes it to
`captures/<timestamp>/warrant_extracted.txt` on every capture,
unconditionally (whether or not the player also manually grabs the real
warrant). One thing a real warrant.txt has that this doesn't:
**Supports** (omitted entirely -- same hard image-matching problem
domain as gems, see "Warrant-text alignment for skill/support ground
truth" above, unsolved). Regression-tested against `test_data/skills/`
(`tests.py`'s `run_skill_name_extraction_tests`) and, for level
extraction specifically, against 8 real CTRL+PRTSC calibration
screenshots -- see "Mercenary Level extraction" below.

### Is dual-pass OCR a valid verification, or does skill identification need an independent check?

A fair question once two different psm modes were shown to drop
different real skill rows: dual-pass OCR (keep whichever pass matches
more of the known pool) is real and validated (0/66 mismatches after the
fix -- see above), and cheap (two Tesseract calls, milliseconds, not
real-time critical). But it isn't a truly INDEPENDENT check -- both
passes are the same OCR engine reading the same pixels through different
page-segmentation heuristics. A failure mode that confuses BOTH
heuristics on the same real row at once hasn't been observed, but can't
be ruled out either, especially since the actual mechanism behind either
mode's row-drops (some interaction between skill-icon color/layout and
Tesseract's layout analysis) was never isolated -- only worked around.

Tested whether icon-based image matching -- genuinely orthogonal, no
reliance on Tesseract's segmentation at all -- could serve as that
independent check, using the exact `assets/pending_skills/` icons
already on hand for the original Infamous Warpriest capture (Holy Relic,
Smite, Dominating Blow, Beacons of Faith, Herald of Purity, Flame Dash).
Cropped each of the real capture's 6 skill-row icons (60x60px bands,
`test_data/skills/infamous_warpriest_raskor.png`, 364x360 total / 6 rows)
and compared each against all 6 pending icons with the same
`_icon_similarity_score_multiscale` already used for gems:

```
row  real skill         top match          multi-scale score  self-rank
0    Holy Relic         Holy Relic              0.8912             1
1    Smite              Smite                   0.8942             1
2    Dominating Blow    Dominating Blow         0.9131             1
3    Beacons of Faith   Beacons of Faith        0.8943             1
4    Herald of Purity   Herald of Purity        0.9037             1
5    Flame Dash         Flame Dash              0.9316             1
```

**6 of 6 correct, including row 1 (Smite) -- the exact skill `--psm 6`
silently dropped from this same capture** (see above). This is a real,
positive result, and unlike gems, skill icons don't appear to carry the
cross-domain rendering-effect problem (no glow/sparkle overlay -- see
"Reference asset sourcing" above, which already documents skills as
rendering without extra in-game effects) that made raw game-extracted
assets unreliable for gem identity matching. Icon matching genuinely
would have caught this specific OCR failure independently.

**Recommendation, not yet built**: keep dual-pass OCR as the primary
method -- it's fast, already validated at 0/66, and doesn't require
maintaining an icon library for the full ~271-skill pool the way
icon-based identification would. But icon matching is a real, validated
candidate for a SECOND-OPINION check specifically when OCR's result for
a row doesn't match the known pool at all (today's ambiguous case) --
not a wholesale replacement. Caveats before building it: this is n=1
capture / 6 icons, all from one mercenary's own skill set, not the
~271-skill pool; per-row icon crop bounds were derived from this one
6-skill layout (60px/row assuming exactly 6 rows) and haven't been
checked against a mercenary with a different skill count, if that's even
possible; and building it out means extracting many more skill icon
assets, comparable in effort to what currencies/scarabs already required.

**On automating that asset acquisition**: the user's own assessment --
not yet attempted, no script exists for it, staying with what's on hand
for now -- is that it's probably not that difficult: parse
`skills_by_mercenary.json`/the raw `mercenaryskills.json` export for each
skill's `_rid`, look up the matching `itemvisualidentity.json` entry the
same way the manual gem-extraction steps in `tools/README.md` already
document, then automate the `.dds` -> `.png` step with a command-line
tool instead of doing it by hand per-icon. Worth attempting before
building icon-based skill verification for real, since manually
extracting icons one at a time the way `pending_skills/`'s current 6
were gathered doesn't scale to the ~271-skill pool.

### Silently missing -> visibly flagged: build_warrant_extracted_text() now flags dropped skill rows

Direct follow-up to a real gap found while explaining the pool-match
safety net: a skill OCR row that doesn't match the mercenary's own known
pool (garbled text, or -- confirmed directly -- a skill that's real but
impossible for THIS mercenary, e.g. `match_skill_name("leapslam",
"Frosthand")` correctly refuses to match it against Frosthand's own
14-skill pool) gets dropped from the skill-block list, which is correct
-- but it used to vanish with NO persisted trace at all. The only signal
was an ephemeral console `NOTE:` at capture time (see
`match_skill_name`'s docstring) -- gone the moment the terminal scrolls,
and never visible to anyone reading `warrant_extracted.txt` after the
fact. Confirmed directly: feeding a record with `['Frost Bomb',
'Frostbite', 'leapslam', 'Ice Nova', 'Flame Dash', 'Discipline']` through
the old code produced a `warrant_extracted.txt` with only 5 skill blocks
-- no marker, no count, nothing distinguishing "this mercenary really
only has 5" from "OCR ate one."

**Fixed**: `build_warrant_extracted_text()` now appends a trailing
comment line, after the real-warrant-mimicking footer (so it can't
corrupt format-matching), listing every dropped row by name:
`# unrecognized skill row(s), dropped: leapslam`. This also means every
already-known instance of the small icon-bleed noise pattern (a stray
single-character/short garbled line, e.g. `'a'` on the Corvil capture --
see the skill-name OCR section above) now shows up in this line too,
which is correct and expected, not a new problem -- it was always being
dropped, just invisibly. Regression-tested with pure synthetic-record
unit tests (`tests.py`'s `run_warrant_extracted_text_tests`, no image
fixtures needed) covering both the flagged case and the clean
no-flag case.

**Follow-up: the flag itself produced a real false alarm on a real
capture, and got a second fix.** `20260914_121940` (Lorakna, the
Profuse) flagged `THE SCYTHE` as an unrecognized/dropped skill row --
but all 6 real skills were actually correct (confirmed against the real
warrant.txt from the same mercenary's earlier encounter, `captures/
20260914_092802`, a rematch pair). Root cause: "Corrupted Blade Vortex
of the Scythe" is long enough to visually wrap to two lines in-game
("CORRUPTED BLADE VORTEX" / "OF THE SCYTHE"), and Tesseract reads each
visual line as its own row. The first line is a real prefix of the full
name and fuzzy-matches correctly; the wrapped continuation doesn't match
anything on its own and was being flagged as if it were a genuinely lost
skill, when nothing was actually missing.

**Fixed**: `build_warrant_extracted_text()` now excludes an unmatched
candidate from the flag list if it's a case-insensitive substring of a
skill THAT ALREADY MATCHED in the same result -- exactly what a wrapped
continuation always is, since word-wrapping only breaks a string at a
boundary, never alters its content. A real wrong/garbled OCR reading
coincidentally being a verbatim substring of some other real matched
skill is possible but unlikely enough not to worry about, compared to
how often multi-word skill names with "of the X"-style endings wrap.
Considered and rejected a line-joining fix instead (detect two
consecutive OCR rows that together fuzzy-match better than either alone,
and merge them before matching) -- more correct in principle, more
complex, and this substring check handles the actual observed failure
mode with high confidence for much less code. Added as a permanent
regression fixture (`test_data/skills/sanguimancer_lorakna_wrapped_skill.png`,
plus a synthetic case in `WARRANT_EXTRACTED_TEXT_CASES` reproducing the
exact candidate list) rather than a one-off diagnostic.

**Also fixed while investigating**: the console `NOTE:` prints (from
`match_skill_name`, `match_mercenary_type`, `parse_mercenary_type`, and
`build_log_row`'s gem-resolution note) were cluttering `tests.py`'s own
output, since several suites deliberately exercise lots of intentional
"didn't confidently match" cases -- that's the fuzzy-match tolerance
being tested, not a bug. Added a module-level `capture_pipeline.NOTES_ENABLED`
flag (default `True`, so live-capture console output is unchanged) and a
shared `_note()` helper; `tests.py` sets it `False` at import time. A
real regression still shows up as an `XX`/failure line regardless -- this
only silences the underlying function's own console chatter during a
test run, it changes nothing about what any function returns.

**Why this matters, per the user's own stated motivation, worth keeping
in mind for future design decisions in this area**: the eventual goal
for this data isn't just a human glancing at a warrant -- it's feeding a
downstream tool (e.g. a trade-API price-check step, the kind of thing
Awakened PoE Trade or the referenced mercenary-builder site do) that
depends on this pipeline to catch every genuinely valuable mercenary. A
silent failure there doesn't just produce a slightly-wrong log line --
it means a real, valuable mercenary can be missed entirely with no way
to know it happened. This is the same reasoning that already justifies
`"Unknown (Blade Ambusher)"` over a guessed gem name, now extended to
skills.

**Related, not built**: a sound-cue idea already exists in
`mercenary_tracker_plan.md`'s Phase 9 ("Immediate feedback on critical
mercenaries/skills/equipment" -- a positive alert when a capture matches
a valuable watchlist entry). The user's extension: a similarly
lightweight but DISTINCT failure tone for a parse/OCR error specifically
(not just silence + a console NOTE), so a failure is as unmissable as a
valuable find, for the same reason above -- a user relying on audio cues
for valuable finds could otherwise miss that a promising mercenary's data
came back incomplete. Both remain unbuilt (Phase 9 was never started),
logged here so the failure-tone half of the idea doesn't get lost
separately from the success-tone half it extends.

### Icon-bleed noise lines were being kept as fake "unresolved skill" candidates, not actually filtered

A real capture (`20260916_062043`, Combatant) printed five separate
`NOTE: skill ... didn't confidently match` lines despite all 6 real
skills reading correctly -- `extract_skill_names`' docstring already
claimed lone garbage lines get "filtered out downstream by
match_skill_name's fuzzy pool match," but that was never actually true:
a genuine no-match was logged and returned as-is, not dropped, so every
lone icon-bleed line (skill icon art bleeding into the OCR crop,
producing its own short extra "line" rather than a prefix glued onto a
real one) survived into `record["skills"]` and, worse, into
`warrant_extracted.txt`'s "unrecognized skill row(s), dropped" footer --
making it look like a real skill was missed when nothing was actually
lost.

**First fix**: only warn once, after picking the better-scoring OCR
pass, and only for candidates at least as long as the shortest real
skill name in the pool ("Arc", 3 characters) -- anything shorter
structurally can't be real. This cleared the reported case ('~', 'ri',
'mi', 'bi', '(R)' -- all 1-2 characters).

**Same day, caught a gap in that fix**: a Kineticist capture
(`20260916_063010`) produced `'ear \xa2'` -- 5 characters, longer than
the length check's floor, so it still slipped through and still showed
up in both the console and the dropped-rows footer. Checking every real
skill name's actual character set across all 65 mercenary type+infamy
combinations (`definitions/skills_by_mercenary.json`) found it's built
from only letters, spaces, apostrophes, and colons (the sole exception,
`"[DNT] Unused"`, is a placeholder already excluded from the usable
pool elsewhere) -- so a candidate containing anything else, at any
length, structurally can't be real either.

**Fixed for real**: `extract_skill_names` now drops a non-pool
candidate (silently, no note, not added to the returned list) if EITHER
check fails -- too short, or contains a character no real skill name
has ever used (`_SKILL_NAME_CHARS_RE`). Either alone is sufficient,
since a real skill name violates neither. A genuinely new/unrecognized
but plausibly-real skill name (right length, ordinary letters) still
gets kept and flagged exactly as before -- this only targets candidates
that structurally cannot be a skill name at all. Both real captures
added as permanent regression fixtures (`test_data/skills/
combatant_razti_icon_bleed.png`, `kineticist_vorlena_icon_bleed.png`),
bringing `SKILL NAME EXTRACTION` to 7/7.

**Also caught along the way, unrelated but adjacent**: `Sanguimancer`'s
one associated gem was displaying as `"Storm Call Of Trarthus"`
(capital "Of") via `display_name_from_slug`, which naively capitalized
every word from the slug. Checked every real "`<X> of <Y>`" gem name in
`definitions/gems_by_mercenary.json` (12 of them) -- all consistently
lowercase "of", never leading. Fixed by excluding "of" from
capitalization rather than guessing grammar generally; this affects
every "of Trarthus" gem's display, not just Storm Call.

### Icon-bleed noise, round 3: stopped guessing at noise's SHAPE, used the mercenary's own known skill quota instead

A third real capture (`20260916_082521`, Fallen Reverend) produced yet
another icon-bleed false alarm -- `'CSaee'`, an extra OCR line
alongside all 6 real skills read correctly. Unlike the two previous
rounds ('~'/'ri'/'mi'/'bi'/'(R)', then 'ear \xa2'), this one has no
shape tell at all: 5 ordinary letters, comfortably past the length
floor, no invalid characters -- it would have passed both checks added
in the previous two rounds. Chasing a fourth shape-based heuristic for
a fourth noise pattern is the same trap already recognized and broken
out of for gem presence (see "Adopted: embeddings for gem PRESENCE"
above) -- tuning what noise LOOKS like doesn't converge, because OCR
garbage has no fixed shape.

**Fixed with a structural check instead of another shape guess.** Added
`_max_skill_count_for_type()`, reading `PrimaryCount+SecondaryCount+
UtilityCount` directly from `skills_by_mercenary.json` (the full
on-screen skill-row count once every slot is unlocked -- 6 for every
real capture seen so far, all level 83). `extract_skill_names` now
checks this FIRST: if every real skill for this type is already
matched, any extra unmatched line can't be one more skill, whatever it
looks like -- no length or character check needed at all, since
there's nothing left for it to be. The length/character-set checks
from the previous two rounds stay as a fallback for when the quota
ISN'T yet full (an unrecognized type, or a real lower-level mercenary
showing fewer than its max skill count -- still unconfirmed either
way, see "Pending (no real capture yet)" on `lvl32/38/46/58` in the
LEVEL EXTRACTION suite).

**Bonus: this also resolves a second, previously-separate false alarm
for free.** A long skill name that visually wraps to two on-screen
lines (e.g. "CORRUPTED BLADE VORTEX" / "OF THE SCYTHE") produces a
real second OCR line that can't match anything alone -- previously
this surfaced as its own spurious `NOTE: skill 'THE SCYTHE' didn't
confidently match...` despite nothing being missing (the
`sanguimancer_lorakna_wrapped_skill.png` fixture), papered over
downstream in `build_warrant_extracted_text()`'s substring-exclusion
check rather than fixed at the source. With the quota already full at
6/6 for Sanguimancer, the quota check drops `'THE SCYTHE'` the same
way it drops `'CSaee'` -- confirmed directly, that NOTE no longer
fires and `extract_skill_names()` now returns exactly the 6 real
skills for that fixture, matching what `SKILL_NAME_EXTRACTION_CASES`
already expected. `build_warrant_extracted_text()`'s substring check
stays in place as a safety net for the quota-not-full case, but the
live real-world instance of this false alarm is now fixed one layer
earlier.

Third real capture added as a permanent regression fixture
(`test_data/skills/fallen_reverend_ung_icon_bleed.png`), bringing
`SKILL NAME EXTRACTION` to 8/8.

### Wave of Conviction of Trarthus: missing reference asset, not a scoring bug

Real capture `20260916_080636` (Flaming Charlatan) failed
`is_gem_present_in_rucksack` entirely (`rucksack_top_right` scored
`0.8687`, just under `GEM_EMBEDDING_PRESENCE_THRESHOLD`'s `0.8737`) --
but unlike every other presence miss investigated this session, this
wasn't a scoring/threshold problem. `assets/gems/` had no
`wave_of_conviction_of_trarthus.png` at all -- Flaming Charlatan's gem
was one of the 5 (`GEM_CASES` in `tests.py`) still `pending` before
this, along with Chain Hook, Heavy Strike, Dark Bargain, and Storm
Call. The embedding model was scoring this crop against 7 unrelated
gems' references, and `0.8687` was just how similar "some real gem, no
specific match" happens to look to that unrelated set -- there was
never a genuine reference to compare against.

**Fixed by adding the real one.** Copied this capture's
`rucksack_top_right.png` into `assets/gems/
wave_of_conviction_of_trarthus.png` (same convention as all 7 existing
gem references -- a real in-game capture, not a game-data extraction,
per `assets/README.md`) and into `test_data/gems/` as the canonical
test crop (same file in both places, matching the existing precedent
for every other gem -- confirmed by hashing `siege_ballista_of_trarthus.png`
and `sunder_of_trarthus.png`'s copies in each directory). Re-ran
`tools/export_gem_embedding_model.py` to regenerate the ONNX model and
reference embeddings (8 reference gems now, was 7). Re-scored: `1.0000`
(self-match) against the crop that used to score `0.8687` -- correctly
flagged present, and `resolve_gem_presence('Flaming Charlatan', 1)`
resolves to `'Wave of Conviction of Trarthus'` (correct casing,
confirming the "of" capitalization fix above generalizes). `GEMS` went
7/12 -> 8/12, `GEM PRESENCE` positive examples 10 -> 11, `RESULT: no
regressions`.

One export hiccup along the way, unrelated to the model itself: the
first re-export attempt failed with `OSError: [Errno 22] Invalid
argument` writing `gem_embedding_resnet18.onnx.data` -- the exporter
doesn't cleanly overwrite an existing external-data file on Windows.
Deleting the old `.onnx`/`.onnx.data` pair before re-running fixed it;
worth remembering for the next gem added (Chain Hook, Heavy Strike,
Dark Bargain, and Storm Call are still pending -- same fix applies
whenever a real capture of one of those turns up).

**Update: Storm Call was already on hand and got missed.** The real
Storm Call of Trarthus capture (`20260916_062717`, Sanguimancer) had
already been investigated earlier in this session -- that's where the
`display_name_from_slug` "of" capitalization bug was caught -- but its
gem crop was never actually turned into a reference asset at the time,
since presence detection happened to already clear the threshold
against the OTHER 7 gems' references (`0.8865`, comfortably above
`0.8737`) and nothing looked broken. The user caught the gap. Same fix
as Wave of Conviction: copied `rucksack_bottom_right.png` into
`assets/gems/storm_call_of_trarthus.png` and `test_data/gems/`,
re-ran the export tool (9 reference gems now). Re-scored: `0.99999988`
(self-match, floating-point short of exactly `1.0` but not a
meaningful difference). `GEMS` 8/12 -> 9/12, `GEM PRESENCE` positive
examples 11 -> 12. Worth remembering going forward: finding a real gem
during a threshold/scoring investigation and confirming presence
already works isn't the same as confirming a reference exists --
check `assets/gems/` for the slug directly, every time, not just
whether the score cleared threshold.

**Checked for the same gap on the remaining 3 pending gems (Chain
Hook, Heavy Strike, Dark Bargain) -- found nothing to backfill, but a
real near-miss worth recording.** Scanned every real capture on hand
for Ripper/Winter Deacon/Cruel Mistress (`grep`'d `warrant.txt`'s
`Build:` line across all of `captures/`): 4 real captures turned up (2
Ripper, 2 Winter Deacon; zero Cruel Mistress so far), but every
rucksack slot in all 4 was a numbered currency/scarab item, confirmed
visually (zoomed crops), not a gem. One score briefly looked like a
repeat of the Wave of Conviction/Storm Call pattern (`0.8495`, a
Winter Deacon capture's slot, close to the presence threshold the way
an unreferenced real gem would score) and got called out as a
candidate before actually looking at it -- it's a scarab (a beetle
icon with a "1" stack badge), not a gem. Score alone isn't a reliable
enough signal to tell "real gem, missing reference" from "an ordinary
currency/scarab that happens to embed a bit closer to the gem
cluster than most" -- confirmed the crop visually before concluding
either way, every time, not just checked the number. The 4 real
crops were still worth keeping: added as new `test_data/currencies/`
negative examples with real confirmed labels (`orb_of_alteration_2.png`,
`orb_of_alchemy_2.png`/`_3.png`, `jewelers_orb_2.png`) -- `GEM
PRESENCE` real negative examples went 62 -> 66.

**User-estimated drop rates for the 3 still-pending gems, for
prioritizing which one to expect finding first:** Winter Deacon 1.3%
mercenary-appearance rate x 5.3% chance of Heavy Strike given that type
(~0.069% combined per encounter); Ripper 3% x 7.7% for Chain Hook
(~0.231% combined); Cruel Mistress 2.4% x 4.5% for Dark Bargain
(~0.108% combined). Chain Hook is the most likely to turn up next by a
real margin (~3.3x Heavy Strike's combined rate), Dark Bargain second,
Heavy Strike least likely -- not validated against real observed
encounter data yet (no Cruel Mistress capture exists at all so far, 2
Ripper and 2 Winter Deacon captures on hand and none had the gem), just
the user's own estimate, logged here so future investigation into
these 3 gaps has a rough expectation of which is worth watching for.

**Update: that "no Cruel Mistress capture exists at all" claim was
wrong -- it only checked `captures/`, not the archives.** Full audit
across all 4 capture locations on disk (`captures/` plus the three
archived `__captures_20260910/`, `__captures_20260914/`,
`__captures_20260915/` rollups -- 282 capture directories total): for
every one with a real `warrant.txt` (138 of 282; the rest have none,
mostly the two oldest archives from before `warrant.txt` capture was
added), parsed the `Build:` line, looked up
`expected_gems_for_type()`, and scored every rucksack slot for a type
that has an associated gem (43 of the 138). Two real Cruel Mistress
captures DO exist -- `__captures_20260914/20260912_063350` and
`__captures_20260915/20260914_092312` -- just neither shows Dark
Bargain in the rucksack, same as every Ripper and Winter Deacon
capture on hand.

Only 5 captures had a gem score above `GEM_EMBEDDING_PRESENCE_THRESHOLD`
across the whole 282-capture history, and all 5 were already fully
accounted for: the 2 Wave of Conviction and 1 Storm Call captures
already added this session, `__captures_20260914/20260913_133259`
(Bastion, Spectral Shield Throw, already in `test_data/gems/variance/`),
and `__captures_20260914/20260912_163151` (Blade Ambusher) -- which
turned out to be the exact source file of the canonical
`spectral_throw_of_trarthus.png` reference itself (confirmed by content
hash, not just filename).

14 more captures scored in a 0.80-0.8737 near-miss band (the same range
Wave of Conviction/Storm Call sat in before their references existed)
across 8 different mercenary types, including the 2 new real Cruel
Mistress captures above. Given the earlier false lead on a Ripper
capture that looked promising by score alone and turned out to be a
scarab (see "Storm Call was already on hand" update above), every one
of these 14 was checked VISUALLY this time, not just by score -- all 14
are scarabs or other numbered currency (a consistent 0.80-0.85 cluster,
cleanly separated from real gems' 0.90-1.0 cluster and ordinary
currency's 0.65-0.79 cluster across this now much larger real sample).
None is a missed gem.

**Bottom line, now actually verified across the full capture history
rather than just the live folder: zero real captures of Chain Hook,
Heavy Strike, or Dark Bargain exist anywhere on disk.** The gap isn't
a detection problem -- the embedding threshold cleanly separates real
gems from every scarab/currency item seen, over 43 type-matched real
captures now -- it's that these 3 gems genuinely haven't dropped yet,
consistent with the user's estimated combined per-encounter rates
above (all three are the rarest of the 12).

### How Awakened PoE Trade uses OCR and vision (technique review only, per this project's existing precedent for external tools)

Reviewed the user's own downloaded source archive of Awakened PoE Trade
(MIT-licensed, `github.com/SnosMe/awakened-poe-trade`) purely to
understand its OCR/vision technique -- same ground rules already
established for the earlier independently-built mercenary-tracking tool
reviewed elsewhere in this file: nothing copied into this repo, only the
approach described. Extracted to a scratch location outside this
project, not committed anywhere.

**OCR is a narrow, optional, opt-in feature -- not how the app's main
price-check flow works at all.** The primary flow reads item data from
the game's own Ctrl+C clipboard item-text copy, no vision/OCR involved.
The ONLY OCR/vision code in the whole app (`main/src/vision/`) exists
for one specific feature: reading Heist "Reward Room" gem-lock
requirement text (`HeistGemFinder.ts`), which has no clipboard-copyable
text source in-game -- the same category of problem as our own
skills/gems region, text/icons that only exist as rendered pixels. Per
`docs/ocr-guide.md`, this isn't even bundled by default: users
separately download a 6MB `cv-ocr.zip` release asset (OpenCV.js +
Tesseract WASM + `eng.traineddata`) and opt in.

**The "learned model" is stock Tesseract -- same engine this project
already uses, no custom training.** `wasm-bindings.ts` loads
`tesseract-core-simd.js`/`.wasm` (Tesseract compiled to WASM) and an
`eng.traineddata` file downloaded from their GitHub releases, then calls
Tesseract's own `TessBaseAPI` (`SetImage`, `Recognize`, `GetUTF8Text`,
`MeanTextConf`) -- functionally the same pytesseract/Tesseract-CLI stack
`capture_pipeline.py` calls, just via WASM bindings for an Electron/Node
app instead of a native binary from Python. There is no custom neural
net, no ONNX/TensorFlow/PyTorch model, no scikit-learn classifier
anywhere in this codebase (checked directly) for either OCR or icon
identification. Tesseract's own bundled LSTM text-recognition net IS a
learned model, but it's the same off-the-shelf one available to anyone,
not something they trained themselves.

**The actual interesting technique is classical CV, not the OCR
call itself**: finding the icon/text locations dynamically instead of
relying on fixed pixel regions the way this project's
`mercenary_regions.json` does.

1. `cv.matchTemplate` (OpenCV, same `TM_CCOEFF_NORMED`-style normalized
   cross-correlation family this project's own `_icon_similarity_score`
   uses, a different specific variant) finds every occurrence of one
   small fixed reference icon (`heist-lock.bmp`) anywhere in a
   downscaled grayscale screenshot -- not a single fixed-position check,
   a full scan for all matches above a threshold.
2. Matches are clustered (`groupWeightedPoints` -- simple greedy
   non-maximum suppression by pixel distance, keeping the
   highest-scoring point in each cluster) since a real icon can produce
   several nearby above-threshold points, not exactly one.
3. Clustered points are paired into "lines" (`findLines` -- sorts by x,
   pairs points within a small y-tolerance) -- inferring a HORIZONTAL
   ROW'S bounding box from two icon detections (per the guide, "both
   icons should be fully visible") rather than a hardcoded pixel region,
   so it naturally handles however many reward-room rows are actually
   on screen, in whatever position they render at.
4. Only THEN does OCR run, on a small crop between the paired icons,
   after converting to HSV and thresholding to isolate a specific
   hue/saturation/value range matching the game's text color
   (`TEXT_HSV_MIN`/`MAX`) -- a color-selective text-isolation mask,
   analogous in purpose to this project's own `_isolate_text_row`
   (which uses grayscale luminance instead of HSV color range) but a
   genuinely different, possibly more selective technique worth knowing
   about.
5. Tesseract runs in `pageseg_mode=7` (single line) -- literally psm 7,
   the same mode this project uses in `extract_text`'s fallback -- and
   the result is quality-gated by Tesseract's OWN confidence score
   (`MeanTextConf() > 30`), not a post-hoc fuzzy-string match against a
   known pool. Different verification philosophy from
   `match_skill_name`'s approach: trust the OCR engine's own confidence
   signal vs. cross-checking the result against domain knowledge of
   what's actually possible. Worth knowing both exist as options, not
   necessarily that one should replace the other -- `match_skill_name`'s
   domain-pool check catches a confidently-wrong reading Tesseract's own
   confidence score wouldn't (e.g. a crisp, confident misread on a real
   character), while `MeanTextConf` catches a genuinely garbled/
   low-quality read `match_skill_name`'s fuzzy match might still slot
   into some cutoff-clearing "closest" pool entry.

**Relevance to this project's own open threads**: the dynamic icon-anchored
row-finding (steps 1-3) is conceptually the same family of idea as the
"dynamic UI-anchor sizing" lead already logged elsewhere in this file
(anchor on a fixed, findable icon instead of hardcoded absolute pixel
regions) -- confirms it's a real, working pattern elsewhere, not
speculative. The HSV color-selective masking (step 4) is a concrete
alternative to `_isolate_text_row`'s luminance-threshold approach, worth
trying if a future capture ever defeats luminance thresholding (e.g. text
over a background of similar brightness but different hue).

### Mercenary Level extraction

The "Lvl NN" text sits in the same header row as `type_subtype`
("Sanguimancer | Lvl 83 | Str / Dex / Int"), immediately right of it, but
had no calibrated region -- unlike Name and Build, nothing captured this
text before now. Real mercenary level varies with the map's own level
(confirmed directly, not assumed -- earlier in this file's history every
observed real warrant happened to read 83, purely because that's what
this player's own maps were producing at the time, not because the game
only shows 83).

**Region calibration**: derived from a real 2560x1440 CTRL+PRTSC
screenshot with the box hand-drawn around "Lvl 83" (a 5px red rectangle).
Rather than eyeball the drawn box, its bounds were measured directly --
an exact-color pixel search (`(237, 28, 36)`, the annotation's specific
red) inside the mercenary-panel window found the rectangle's true pixel
bounding box. Resulting region: `definitions/mercenary_regions.json`'s
new `"level"` entry, absolute `(1255, 142, 1406, 186)`. Checked for
position stability against 7 MORE real screenshots at different levels
(66, 68, 73, 79, 80, 81, 82) -- the same fixed box cleanly framed "Lvl
NN" in every one, with no re-adjustment needed, so this isn't fit to one
mercenary's subtype-text width happening to push the box to a particular
spot.

**OCR**: `extract_level()` (`capture_pipeline.py`) runs `--psm 11` on the
raw crop (no `_isolate_text_row` preprocessing) and regexes out the
digits. psm modes were compared directly on all 8 calibration
screenshots: `--psm 7`/`--psm 6` badly garbled 6 of 8 (misreading the
box's own border/corners as text, e.g. `'| tvtie2 |'` for "Lvl 82"),
while `--psm 11` read all 8 correctly on the first try. Real, decisive
signal to prefer psm 11 here specifically, not a coin flip between
similar options.

**Caveat at the time, since resolved**: all 8 calibration screenshots
were CTRL+PRTSC captures, not `mss` -- per this project's own documented
capture-method color/border mismatch (see "CTRL+PRTSC vs. `mss`
capture-method mismatch" above), they were used ONLY to find the region
and sanity-check psm mode choice, and were deliberately NOT copied into
`test_data/` or `assets/` as fixtures. Whether OCR reads a real
`mss`-captured level crop just as cleanly was the open question -- now
confirmed yes, see the "Update: superseded by real mss test data" note
below, once real captures with the region wired in existed to check
against.

**Update: superseded by real mss test data -- this is now a real,
blocking regression suite, not a diagnostic.** The CTRL+PRTSC calibration
screenshots (`captures/lvlNN.png`) did their job (found the region,
picked psm 11) and have been removed from disk -- replaced by real
mss-captured `level` region crops in `test_data/mercenary_levels/`
(147x40px, produced by the same `build_crop_plan`/`on_capture()` code
path as every other region's saved crop, e.g. `test_data/skills/*.png`),
spanning campaign through maps (32, 38, 46, 58, 66, 68, 73, 79, 80, 81,
82, 83 -- 6 of 12 captured so far: 66/68/73/79/80/81).

`tests.py`'s suite was renamed `run_level_extraction_tests()` and
promoted to blocking, matching every other suite backed by real
production-representative data. **Found and fixed a real bug while
wiring this in**: the suite was still applying `box_tuple()`'s absolute
full-screen region coordinates to these ALREADY-cropped 147x40 images
(copy-pasted from the old calibration-screenshot version, which needed
that crop since those inputs were full 2560x1440 screenshots) -- cropping
a small already-cropped image with full-screen absolute coordinates
produces an out-of-bounds/blank result, so every case was silently
failing OCR regardless of the real fix's correctness. Corrected to open
the pre-cropped file directly, no re-crop. All 6 real mss cases pass;
the other 6 (32, 38, 46, 58, 82, 83) report PENDING until captured.

### Zone level vs. character level: an open hypothesis about mercenary spawn rates

While gathering the level-32/38/46/58 calibration screenshots, a real
observation came up worth recording even though it's unverified: none of
the mercenary encounters captured in a level 66 zone had any rucksack
items at all. Two competing explanations, neither confirmed:

1. **Zone tier, not character level, is the cause.** The PoE Wiki
   describes mercenary skill support-count maximums as lower in "low
   level zones" vs. higher in "high level zones (i.e. maps)" -- possibly
   meaning the campaign (zone levels 1-67) is categorically the "low"
   tier and maps (68+) are the "high" tier, a hard cutoff rather than a
   smooth scale. If gem/currency/scarab rucksack drops are gated the
   same way as support counts, a campaign-zone encounter could be
   structurally incapable of dropping rucksack loot at all, independent
   of anything about the specific capture.
2. **Character level vs. zone level mismatch, not zone tier itself.**
   The Wiki also states mercenary spawn rates are disabled in every zone
   except level 66 and maps once the player's character is 10+ levels
   above the zone's own level -- so a high-character-level player
   farming a level 66 zone specifically (the one zone exempted from that
   spawn-rate cutoff) might still see mercenaries spawn, but possibly
   under some other reduced/altered state that happens to exclude
   rucksack loot, unrelated to the zone-tier explanation above.

Both mechanisms could independently explain the same real observation,
and they're not mutually exclusive. Not chased further here -- logged as
a real, specific lead for whoever next tries to collect real gem/
currency/scarab test data or re-examines the support-count-tier
scaling question above, since it directly bears on whether a "collect
more low-level real captures" plan would actually produce any rucksack
data to work with at all.

### Exception tag (Rematch/No Infamous chance/Infamy-Renown) moved from per-capture to once at script start

Previously `_prompt_for_exception()` ran after EVERY capture, in its own
background thread with a `_tagging_lock` (only one prompt active at a
time -- a second F9 landing mid-prompt logged with no tag rather than
racing the same R/N/I keys) and a 10s timeout so it couldn't stall live
gameplay. This made sense when the tag was assumed to vary per capture,
but in practice all three tags describe session-level facts (an atlas
passive's infamy odds, a scarab's rematch state) that don't change
encounter to encounter within one mapping session -- asking every
single time was needless repetition for an answer that was always
going to be the same.

**Changed**: `_prompt_for_session_exception()` now asks once in `main()`,
before the capture hotkey is even armed, and blocks indefinitely (no
timeout -- nothing is time-sensitive yet at that point, so there's no
reason to risk silently defaulting to "no tag" on a session-wide
answer). The chosen label is passed straight through
`_safe_on_capture` -> `on_capture` -> `build_log_row(exceptions=...)`
for every capture logged the rest of the run. This also let the whole
per-capture threading/locking mechanism go away entirely -- no more
`_tagging_lock`, `_defer_capture`, or a background thread per capture;
`on_capture` now runs fully synchronously (append_log + clipboard copy
+ print), same as the OCR/file-write work it was already doing
synchronously before this. `threading` is still imported, now only for
`Event` in the startup prompt.

## Open action items 
- **If `match_icon()`'s general identity matching is ever revisited with embeddings (not just the Blade Ambusher-specific disambiguation, which already switched), Siege Ballista vs. Blast Rain needs its own real variance data and margin calibration first -- it's a tighter real collision than Helix/Throw was (margins as low as `0.0169` on 2 real Siege Ballista samples vs. Helix/Throw's worst of `0.0264`), not a solved case just because Blade Ambusher's pair worked out. See "Follow-up question worth logging" under "Adopted: embeddings for gem PRESENCE" above for the real numbers.
- **Flagged for later implementation: when support identification is wired in, an unresolvable icon+tier collision should be written to `warrant_extracted.txt` as an explicit either/or string** (e.g. `"Minion Damage III or Minion Life III"`), not guessed and not silently dropped -- same "don't guess, flag it" discipline this project already uses for gems (`"Unknown (Blade Ambusher)"`) and skills (the unrecognized-row flag). Applies only to the small, enumerable set of real per-skill collisions documented in "Support icons: many are shared by design" above (Minion Damage/Life, Throwing Speed/Trigger Radius, the Cooldown/DoT/Shock/Lucky-Lightning cluster, Ignite Chance/Combustion, Freeze Chance/Brittle Chance, Ironwood/Physical as Extra) -- everything else, Gilded included, resolves deterministically via skill+tier lookup and needs no flagging. See `assets/support_icon_coverage.md` for the icon-gathering checklist this depends on.
- **`definitions/*.json` were recently replaced with exported definitions from `tools/extract_mercenary_skills.py`. That script now produces complete, validated output for all 65 mercenaries and 271
  skills directly from real game data. The hand-entered data was replaced; `capture_pipeline.py`'s skill-name lookup (`known_skills_for_type`/`all_known_skills`/`match_skill_name`) has since been updated to consume the new `definitions/skills_by_mercenary.json` dict schema (was pointed at a no-longer-existing `mercenary_skills.json` and silently returning nothing). `known_skills_for_type` now expects the full type+infamy combination (e.g. "Infamous Warpriest"), matching how the new file is keyed, with a fallback to the bare base type if the exact combination isn't found.
  **Update: the skills half is now wired in.** `process_capture()` calls `extract_skill_names()`/`known_skills_for_type()` and `on_capture()` writes `captures/<timestamp>/warrant_extracted.txt` from the result (see "Skills: OCR validated across real data..." above for the real testing and the psm 3 -> psm 6 fix this surfaced). **Update: `definitions/supports_by_skills.json` now has real consuming code too**, just not the image-matching kind this bullet originally meant -- `definitions/supports.json`'s generation and the whole family/icon-collision analysis (see "Support icons: many are shared by design" and `SupportCountTier` sections below) read it extensively. What's still genuinely missing: no `extract_support_names`/`match_support_name` equivalent exists -- nothing turns a real screenshot crop into a support name yet, for any support, matched or ambiguous. Also still not done: skills/supports aren't in the TSV log (`build_log_row`) at all yet, only in `warrant_extracted.txt`.
- **`supports_by_skills.json`'s `SupportCount` field was found and fixed to be a mislabeled tier id, not a literal count.** Caught by cross-referencing a real level-83 warrant (Infamous Bladecaster/Malkan) against the extracted data: all 5 of its skills showed MORE actual equipped supports than their raw `SupportCount` integer (e.g. Flame Dash: raw value 0, but 2 real supports in the warrant). Root cause: every one of the 271 skills' raw `SupportCount` in `tools/extracted_dats/mercenaryskills.json` only ever takes the value 0-3 -- exactly the row range of a separate, already-exported-but-previously-unused table, `mercenarysupportcounts.json` (`{0: Low, 1: Medium, 2: High, 3: None}`). It's a foreign key into that enum, not a literal number. Fixed in `tools/extract_mercenary_skills.py` (`get_support_count_tier`) to resolve it into the tier name; the output field is now `SupportCountTier` (string), not `SupportCount` (int) -- `definitions/supports_by_skills.json` was regenerated and all 5 of the warrant's real counts land inside their resolved tier's documented range (poewiki.net: None=0, Low=1-2, Medium=2-3, High=3-5 in high-level/map zones). That range mapping is recorded as `SUPPORT_COUNT_TIER_RANGES`/`resolve_support_count_range()` in the extraction script, but not yet wired into any output or validation -- the real per-encounter count still isn't derivable from these tables alone (presumably randomized within the tier's range per spawn), so treat it as a plausibility check (does an OCR'd support list's length fall inside the tier's range?), not a precise expected value.
  **Update: checked directly against real level 80/81 warrants (previously only validated at level 83) -- the tier-range mapping holds, but a "lower level rolls toward the low end" hypothesis does NOT.** Real captures at levels 80/81 (`captures/20260914_091911`, `_092050`, `_092312` -- Stormhand, Fallen Reverend, Infamous Cruel Mistress) gave 18 real (skill, observed support count) pairs to check against their resolved `SupportCountTier`. All 18 landed inside their tier's documented range (100%, same as the original level-83 check) -- the mapping itself generalizes across at least this level range. But of the 12 non-`None`-tier skills, 8 landed at the HIGH end of their range and only 5 at the low end (`Stormcall`: High tier 3-5, observed 3 (low); `Absolution`/`Raise Spectre of Transience`/`Soulrend of Reaping`: High tier 3-5, observed 5 (high); several Low-tier skills (`Desecrate`, `Battlemage's Cry`, `Despair`, `Lightning Warp`) observed at 2, the high end of their 1-2 range) -- the opposite lean from what "should roll on the lower end" would predict. Real but small (n=3 mercenaries, 18 skill-tier pairs) -- worth rechecking if more low-level real captures turn up, not yet enough to treat as settled either way, but the initial hunch isn't supported by what's been checked so far.
- a github repo has been created here: https://github.com/imstimpy/poe-mercenary-ocr but only a few markdown files have been committed. The local files need to be sanitized before uploading the rest of the project.
- **Lead for future gem differentiation work: `mercenary_regions.json`'s rucksack quadrants aren't uniformly sized.** The parent `rucksack` box is 137x137px, which doesn't split evenly in half -- `top_left` is 68x68px, while `top_right`/`bottom_left`/`bottom_right` are each 69px in at least one dimension. This was flagged after TWO independent real top_left gem crops (different gems, different sessions) both scored anomalously low under `match_icon()` -- `test_data/gems/top_left.png` (pre-existing failure) and the new `test_data/gems/quadrants/top_left.png` (Siege Ballista, scored `0.7533` against its own correct reference -- lower than a WRONG gem, `spectral_helix_of_trarthus` at `0.7541`). Plausible mechanism: every comparison function resizes crops to a fixed target size regardless of original dimensions, so a systematically smaller original crop gets stretched slightly more than the others, which could produce exactly this kind of small, consistent penalty.
  Attempted to validate directly against a real full-resolution screenshot (`2026_09_13_06_30_45_Greenshot.png`, 2560x1440, matching calibration resolution) via pixel-level edge detection rather than visual inspection (shadows/decorative UI chrome make visual boundary-finding unreliable, confirmed directly -- scan lines crossing rivets/chain-link decoration gave noisy, inconsistent readings). Result was genuinely mixed, not conclusive: the left edge (x=1485) matched the calibrated boundary cleanly (a sharp, real brightness spike right at the calibrated pixel); the top edge showed a possible +4px offset (real border peak around y=557, not y=553); right and bottom edges were too noisy to read confidently. **Not settled either way** -- worth revisiting with a cleaner reference point (e.g. a screenshot with the rucksack panel isolated against a plainer background) rather than treated as confirmed miscalibration or ruled out.
  The known historical Siege Ballista failure can't be directly re-tested with a wider crop -- `on_capture()` only ever saves the already-cropped quadrant PNGs, never the original full screenshot, so there's no way to re-derive a differently-sized crop for a past capture after the fact.

  **Follow-up: content-centroid recentering tried and made it slightly
  worse (0.7417 vs 0.7533)** -- finding the item's own content centroid
  (bright pixels vs. background, excluding the stack-count digit
  region) and recropping centered on it doesn't help, because the
  candidate isn't the only side with framing to worry about: the
  reference image has its own independent centering, and correcting
  the candidate alone can move it further from whatever alignment the
  reference happens to have. **What DID work, substantially: running
  the already-validated `_icon_similarity_score_multiscale` (built for
  presence detection, not yet used for identity) against this same
  Siege Ballista case.** Score rose from `0.7533` to `0.8299` (best fit
  at scale=1.15, a real position offset found, not the geometric
  center) -- and critically, it fixed the RANKING, not just the score:
  Siege Ballista now correctly comes out on top with a `0.0456` margin
  over the runner-up (bigger than `match_icon()`'s own required
  `0.025`), where the fixed-position method had a wrong gem
  (`spectral_helix_of_trarthus`) outscoring the correct one entirely.
  Doesn't clear `match_icon()`'s absolute `0.94` floor yet, but this is
  a real, working mechanism already in the codebase -- the natural next
  step for "better gem differentiation" is applying
  `_icon_similarity_score_multiscale` to identity matching generally,
  not inventing a new centering mechanism. Still n=1 on this specific
  test; worth confirming on other known-imperfect real gem crops before
  treating it as a general fix.

  **Update: confirmed on a second, independent real Siege Ballista
  instance -- `captures/20260915_105105`, this time in `bottom_right`,
  not `top_left`.** Notable on its own before even trying multiscale:
  the fixed-size `_icon_similarity_score` scored it `0.7325`, WORSE than
  the original `top_left` failure (`0.7533`) and now ranked 6th of 7
  real gem references (four unrelated gems outscored it) -- real
  evidence the quadrant-sizing theory above isn't the whole story, since
  `bottom_right` is one of the normally-sized quadrants, not the
  undersized `top_left`. Running the same `_icon_similarity_score_multiscale`
  fix (windowed via `_glue_rucksack_region` + `RUCKSACK_WINDOW_MARGIN`,
  exactly as `is_gem_present_in_rucksack` already does) reproduced the
  same result as the first case: score rose to `0.8253` at the SAME
  best-fit scale (`1.15`) as the original instance, and the ranking
  corrected itself -- Siege Ballista back on top by a `0.0301` margin
  over the runner-up (`bladefall_of_trarthus`). Two independent real
  instances, two different quadrants, same failure mode, same fix, same
  optimal scale -- this is real signal, not a fluke of one crop. Neither
  instance clears `match_icon()`'s `0.94` floor yet, but the case for
  wiring `_icon_similarity_score_multiscale` into identity matching
  generally (not just presence detection) is now a lot stronger than
  "n=1, promising."

  **Important counter-example, checked the same day: multiscale is NOT
  a strict upgrade -- it can make a good match worse.** A third real
  instance, `captures/20260915_105453`'s `rucksack_bottom_left` (Blast
  Rain of Trarthus, confirmed by eye against the reference), was
  already a clean, confident match under the plain fixed-size
  `_icon_similarity_score`: `0.9923`, correctly ranked first, clearing
  `match_icon()`'s `0.94` floor outright with the next-best candidate
  (`spectral_helix_of_trarthus`) at only `0.9182` -- nothing wrong with
  this one to begin with. Running the exact same windowed multiscale
  search used above on this ALREADY-GOOD crop dropped its score to
  `0.8399` and collapsed the margin over the runner-up to `0.0069` --
  from a wide, confident gap down to a near-coin-flip. Same mechanism
  likely responsible either way: multiscale's wider search window and
  scale freedom give a WRONG reference more chances to find some
  locally-good-enough alignment too, which mostly helps when the
  fixed-size method was failing for exactly that reason (Siege
  Ballista, above) but can only hurt when the fixed-size method was
  already aligned correctly. **Practical implication for the "wire
  multiscale into identity matching" idea two paragraphs up: it isn't a
  blanket replacement for `_icon_similarity_score` -- if adopted, it
  needs to be a fallback tried when the fixed-size score is
  low/ambiguous, not something that runs unconditionally on every
  crop.**

## Open decisions
- **Inactive mercenary types** (currently just "Bladereach") sit in
  `definitions/mercenary_types.json` with no test data and never will,
  since they can't be encountered in-game. They also currently sit in
  `match_mercenary_type()`'s fuzzy-matching pool, meaning an OCR misread
  of an active type could in principle get pulled toward an
  unencounterable one. Proposed but not implemented: an `"active":
  false` flag (or a separate list) so inactive types can be excluded
  from both the matching pool and the pending-test count without losing
  them from the reference list entirely.
- **Infamous/Non-famous** `definitions/mercenary_types.json` is a raw output of all mercenaries, listing each Infamous and Non-Infamous mercenary as unique. The raw mercenary skills output does the same. A) the game appears to treat them as different even though the names are quite similar B) does the code and definitions achieve a simpler, more maintainable form by condensing infamous/non-infamous into single records C) recognize that Infamous Warpriest of the Ruckus is an exception that doesn't have a non-Infamous variant and some mercenaries don't have an Infamous variant.
- **Leading noise before the real name in `extract_text()`'s output -- decided to leave as-is, not auto-strip.** Real examples from live captures: `"i W Vorla Tarthis"`, `"ha ee Sem, the Pity"`, `"a Slythia, the Noxious"`, `"igure. Tavielle, the Pure"` (`captures/20260914_120938`) -- one or more short garbage tokens glued onto the front of an otherwise-correct name. Confirmed this isn't tied to a specific name/crop: the stored `test_data/mercenary_names/vorla_tarthis.png` reference crop extracts perfectly clean (`'Vorla Tarthis'`, no noise) -- the noisy `"i W Vorla Tarthis"` reading came from a *different* real capture of the same mercenary, so it's frame-to-frame background-art variance contaminating the crop (the same underlying phenomenon as the `--psm 11` total-failure case fixed for Eli, the Contemptible -- see `extract_text`'s docstring), not something specific to certain names or a fixable-once bug in one image. All three examples still contain the real name as a clean substring, which is what the containment-based test suite (and `_isolate_text_row`'s documented recall-over-precision bias) is already designed to tolerate.
  Decided against adding an auto-strip heuristic (e.g. "drop leading short/lowercase tokens before the first capitalized word") on only 3 examples: this project already has a documented cautionary tale for exactly this move -- an earlier, more elaborate precision-oriented name-isolation approach (anchor on tallest letter, exclude by dark-backing window, cluster by baseline/height) was iterated on repeatedly and ultimately reverted because each refinement fixed the specific capture in front of it while clipping a real character in a previously-working one. Revisit only if enough real noisy-prefix examples accumulate to validate a heuristic against without repeating that failure mode -- same "wait for real data before generalizing" discipline as the gem-matching investigation earlier in this file.

## Update: `warrant_extracted.txt` renamed to `warrant_generated.txt`
Pure rename, no behavior change -- "extracted" read as implying it was
pulled from something (the real warrant item), when it's actually the
pipeline's own from-scratch reconstruction of that layout from OCR'd
data; "generated" says that plainly. Renamed the write site in
`on_capture()`, the read site in `harvest_support_icons.py` (the one
place besides `capture_pipeline.py` itself that opens this file by
name, for its no-`warrant.txt` inference fallback), and every doc/
comment mentioning the literal filename. All 113 existing files on disk
across `captures/` and the `__captures_*` archives were renamed to
match (these directories are gitignored, so no history to preserve) --
re-ran `harvest_support_icons.py` afterward to confirm the inference
path still finds them (24 captures used it, same as before the rename)
and that harvested-catalog/coverage-report numbers came back identical
(105/159 covered, 54 missing). `tests.py` has no test that opens this
file by name (it exercises `build_warrant_extracted_text()` directly
against in-memory records), so nothing there needed changing beyond one
comment.

## Tier I support detection was silently broken -- three real, separate bugs found via one new capture archive

Prompted by adding `test_data/mercenary_levels/lvl32.png` (see the
extract_level() fix below) and `__captures_campaign/` (20 new low-level
sessions, none with a real `warrant.txt` -- OCR-inferred only): user
observation that none of the Tier I supports in these captures'
`warrant_generated.txt` were reporting as Tier I. Confirmed directly:
across the whole archive's 129 real occupied support cells, the
tier-badge reader itself correctly said "i" 117 times, but
`match_support_icon()`'s actual output only came out tier "i" twice.
Three independent root causes, not one:

**Bug 1 -- `extract_level()`'s `--psm 11` misreads "Lvl 32" as "Lvl
5250".** `image_to_data` shows this as one single low-confidence word
token, not multiple merged fragments -- a genuine glyph-level misread
on this specific crop, same category as the earlier `--psm 11`
total-failure case for a mercenary's name. OCR confidence doesn't
separate this from already-correct reads (checked directly: this
case's conf=38 sits between several correct reads' own confidences,
e.g. Lvl 83 at conf=0). What does separate it: plausibility of the
VALUE. No real mercenary is level 5250. Fixed by retrying with `--psm
3` (which reads this crop correctly) whenever the `--psm 11` result is
missing or exceeds a `_LEVEL_PLAUSIBLE_MAX = 100` sanity ceiling --
confirmed against all 10 real level crops on hand this never overrides
an already-correct `--psm 11` reading, since `--psm 11` is only wrong
on this one. `LEVEL EXTRACTION` now 10/10 (was 9/10, `lvl32` failing).

**Bug 2 -- `match_support_icon()`'s tier-badge correction was gated on
having a harvested REFERENCE for the corrected tier, not on whether
that (icon, tier) combination is real.** `if badge_key in refs:` --
`refs` is only what's been harvested so far, and 47 of 54 missing
coverage keys were Tier I (see `support_icon_coverage.md`), so the
correction silently failed for most real Tier I crops and fell through
to `return best` (the wrong, higher tier the raw embedding matched
instead, since it has nothing to compare a genuine Tier I crop against
when no Tier I reference exists). Fixed by checking against
`_support_visual_key_to_names()` (every real combination in
`definitions/supports.json`) instead -- the badge reader doesn't need
a reference IMAGE to be trusted, only for the combination it names to
actually exist; `resolve_support_name()` only needs the visual_key
string to look up an identity, not an embedding. Result on the real
campaign data: tier "i" output went from 2/129 to 110/129 (the
remaining 7 unresolved don't clear `SUPPORT_MATCH_MIN_SCORE` at all,
unrelated to tier). `tools/match_support_icon.py`'s own `classify()`
had the identical bug (kept in sync, same fix) -- though its own
validation loop structurally can't detect this bug class regardless
(it only evaluates crops whose ground truth already has a harvested
reference, i.e. it's blind by construction to exactly the coverage-gap
failure mode this whole investigation is about), so its printed 100%
accuracy doesn't change and never actually vouched for this path.

**Bug 3 -- `harvest_support_icons.py`'s `parse_warrant_skills()`
treated every equipped support as if it were an additional skill.**
Written before supports were wired into `build_warrant_extracted_text()`,
this function appended every non-separator line between skill blocks --
correct when a block was just the skill name, wrong now that a block is
`SkillName` followed by zero or more `SupportName (Tier: N)` lines. A
real 6-skill capture with several supports produced an 11+ entry list,
so every row past the first (accidentally-correct) one got paired with
the WRONG skill for candidate narrowing -- confirmed directly against
`__captures_campaign`'s real `warrant_generated.txt` files, where a
support crop's own recorded `contributing_skills` metadata literally
contained support names like `"Impale Chance (Tier: 2)"` and
`"Unknown"` instead of real skill names. This one bug fully explains
this run's abnormal `32 anomalies (empty candidate set)` -- candidate
narrowing against the wrong skill's `PossibleSupports` pool naturally
often yields nothing. Fixed by only taking the first line after each
`--------` separator as the skill name and skipping every line after
it until the next separator. Re-ran the harvest: anomalies dropped
32 -> 0.

**Follow-on: used the now-fixed, already-validated badge reader to
do "Part 2"** (`harvest_support_icons.py`'s own module docstring calls
reading a tier off a crop by eye "something a human can do trivially
that image-matching code can't yet" -- true when written, less true
now). Ran `_resolve_support_tier_from_badge()` against all 86 crops
still sitting in the `ambiguous, inferred candidate list` bucket after
fixing bug 3: 85 of 86 got a confident reading, 79 of those Tier I --
added as new `tier_notes.json` entries (zero collisions with the ~140
existing hand-entries, which itself makes sense in hindsight: Tier I
was always going to be rare in the corpus until a real low-level
archive existed to harvest from). Re-ran the harvest: 7 more crops
confidently auto-labeled, ambiguous-inferred 86 -> 79. Coverage
105/159 -> 107/159. The remaining 79 are genuine ambiguity (multiple
real candidates share the exact icon+tier for that specific skill's
own pool, once narrowed) -- not something a tier hint can resolve
further, same structural ceiling as the already-documented icon+tier
collisions.

**New regression coverage**: `test_data/supports/tiers/` (new) holds 4
real crops -- `tier_i_1.png`/`tier_i_2.png` from `__captures_campaign`
(the exact archive that surfaced bug 2), `tier_ii_1.png` (from the
already-verified `sanguimancer_rakella.png` fixture), `tier_iii_1.png`
(also from the campaign archive) -- each visually confirmed to show the
claimed bar count before being locked in, not just trusted from
`match_support_icon()`'s own output. New `run_support_tier_tests()` in
`tests.py` checks both `_resolve_support_tier_from_badge()` and
`match_support_icon()`'s resolved key per crop (`SUPPORT TIER
DETECTION: 4/4 passing`), wired into the blocking total -- a regression
in either the badge reader or how its result gets used will show up
here directly, specifically anchored on Tier I since that's what had no
real coverage before this session's data existed.

## Update: coverage gap made visible on every test run, bundled by skill

Added `run_support_coverage_tests()` to `tests.py`, right after the new
tier-detection suite. Re-derives the same gap
`tools/generate_support_coverage_report.py` tracks
(`assets/support_icon_coverage.md`), but organized by skill instead of
by (icon, tier), and printed as part of the normal test run instead of
only in a separately-regenerated report -- for every real support's
every tier (`definitions/supports.json`), bundled under each skill that
can roll it (`definitions/supports_by_skills.json`), prints `ISSUE` on
the specific (support, tier) pair if the reference catalog doesn't
cover it yet. A skill offering only covered tiers prints nothing, same
"only surface real gaps" discipline as the coverage report itself.

Deliberately non-blocking, same treatment as the match_icon suites:
missing harvested data isn't a code regression, so `run()` never adds
`support_coverage_issues` to the blocking total, just surfaces the count
at the end. Current numbers: 2844/4061 (support, tier) entries covered
across all skills' pools (1217 issues across 224 skills) -- a much
bigger raw count than the coverage report's 107/159 unique (icon, tier)
keys, since one missing key (e.g. `mercsilverintsupportgem_i`) gets
listed once per skill that can roll it, not once overall. Also prints
the small set of real "zero-breadth" supports (offered by no skill at
all in the extracted data, e.g. Excommunicate/Gilded Malediction) in
their own un-counted section, same set already noted in this file's
`generate_support_coverage_report.py` writeup.

## Update: coverage-by-skill suite deduped to the real gap count

Real, immediate feedback on the suite above: counting the same missing
(icon, tier) once per skill that offers it inflated "1217 issues" for
what's actually 52 distinct gaps (`assets/support_icon_coverage.md`'s
own number) -- `mercsilverintsupportgem_i` alone is reachable from
dozens of skills. Fixed by flagging each visual_key only the first time
it's seen (skills visited in sorted order, so which skill gets credited
is deterministic); every later skill sharing the same gap still gets a
line in its own bundle (so nothing disappears from the per-skill view),
but it reads as a cross-reference back to the flagged skill (`..  ...
-- same gap already flagged under 'Absolution'`) instead of a new
`ISSUE`, and doesn't add to the count again. Result: 49 distinct
skill-reachable gaps. The remaining 3 (all `Excommunicate` tiers) can't
be flagged this way at all -- no skill offers `Excommunicate` in the
extracted data (same zero-breadth set this suite already lists
separately) -- so `49 + 3 = 52` reconciles exactly against the coverage
report, and the suite's own output now says so explicitly rather than
leaving that gap unexplained.

## Update: cross-reference lines were still too much noise -- report each gap exactly once, full stop

The dedup fix above still printed a `..  ... -- same gap already
flagged under X` line for every skill sharing an already-flagged gap --
better than a duplicate ISSUE, but still 1217 lines of "nothing new
here" for 49 real gaps. Changed to the simplest possible rule: a gap is
printed once, under the first skill (sorted order) that offers it, and
every later skill that would otherwise only repeat it is skipped
entirely -- no line, no header. A skill still prints if it has at least
one gap not yet flagged elsewhere, even alongside an already-flagged
one; only the new gap shows for that skill, not the repeat. Output
dropped from 224 skill blocks / 1217 lines to 25 skill blocks / 49
lines -- one line per real gap. Added an explicit NOTE at the top of
this suite's own output explaining the one-report-per-gap policy, so a
reader doesn't wonder why some skill (Blink Arrow, say) that genuinely
also lacks Minion Damage I isn't shown -- it's covered under Absolution
instead, deterministically, not dropped.

## Update: why __captures_campaign has zero warrant.txt -- it's a hard game constraint, not a gap

User-confirmed real game mechanic: a mercenary warrant cannot be
obtained at all below level 68. This isn't a "haven't gotten around to
capturing it yet" gap the way a merely-rare high-level skill is --
every sub-68 encounter is PERMANENTLY inference-only, no later capture
of the same or a similar encounter will ever produce a real
`warrant.txt` to promote it to ground truth. `__captures_campaign`
(all sub-35, the archive behind this session's whole Tier I
investigation) is the extreme case: 0 of 20 sessions have one, and
structurally never will.

Practical implication for the 79 crops still sitting in
`ambiguous, inferred candidate list` (see the Tier I writeup above):
resolving those further has exactly two real paths, not three --
`tier_notes.json` (already exhausted, see above) narrows by tier alone
and can't pick between same-tier candidates; a real `warrant.txt`
literally cannot arrive for these later. The only path left is
`manual_labels.json` -- a human recognizing the icon by eye. Worth
knowing before spending more effort trying to narrow these
algorithmically: there's no cleverer inference that closes this gap,
since the missing information (which specific support this skill
actually rolled) was never observable from what got captured, and
never will be from more captures of similarly low-level encounters.
This also means Tier I coverage generally (45 of 52 missing keys, per
`support_icon_coverage.md`) will keep leaning on manual identification
rather than ground truth far more than Tier II/III ever needed to,
since real captures already skew toward high-tier rolls at the levels
where a warrant is even possible.

## Real bug: `mercgoldsupportgem_i.png` was actually "Increased Area of Effect", not a Gilded support

User-caught, not something I noticed: the primary reference file for
`mercgoldsupportgem_i` visually showed "Increased Area of Effect", the
wrong icon entirely for that visual_key. Root cause, confirmed
directly: `definitions/supports.json`'s `IncreaseAreaOfEffect` family
has 4 members spanning TWO different icons --
`Gilded Area per Projectile` (icon `mercgoldsupportgem`, tier III
only) and the plain `Lesser/[mid]/Greater Increased Area of Effect`
trio (icon `increasedaoe`, tiers I/II/III). `build_identity_members()`
sorts members alphabetically, so `members[0]` for this family is
`"Gilded Area per Projectile"` -- and `canonical_display_name()`
already special-cases "Gilded " out for the human-facing NAME (per its
own docstring, fixed once before for exactly this family), but the
icon lookup two lines below it (`icon = supports.get(members[0],
{}).get("icon")`) never got the same fix. A tier-I inference
resolution correctly produced the label "Increased Area of Effect"
(Gilded-excluded, as intended) but then filed it under
`mercgoldsupportgem_i` (`members[0]`'s icon) instead of `increasedaoe_i`
-- a tier that member doesn't even have (Gilded Area per Projectile is
tier III only).

**7 identity families span more than one icon** (checked directly):
`MultipleProjectiles`, `Duration`, `IncreaseAreaOfEffect`, `PhysGainAs`,
`CritChance`, `ProjectileSpeed`, `Pierce` -- any of them could have hit
the same bug, not just this one.

**Fixed** by finding the specific member that actually exists at
`resolved_tier_roman` and using ITS icon, instead of blindly trusting
`members[0]`. If more than one member of a family shares the exact
same tier (a real family-level collision -- structurally possible,
same category as the already-documented icon+tier collisions), that
now stays unresolved rather than guessing, consistent with this
project's "don't guess" discipline everywhere else.

Re-ran the full refresh pipeline after the fix: `mercgoldsupportgem_i`
no longer exists as a visual_key at all (correctly -- there's no real
Tier I Gilded Area per Projectile), all 5 crops that were wrongly filed
under it now correctly merge under `increasedaoe_i`. Coverage
107/159 -> 111/159 (this fix alone recovered `increasedaoe_i`, plus a
few more from new captures folded in during the same re-run). Full
`match_support_icon.py` validation still 100% (2094/2094) -- this bug
lived entirely in the harvester's own labeling logic, never in
`capture_pipeline.py`'s production matching. `tests.py` still reports
no regressions; `SUPPORT COVERAGE BY SKILL` gap count dropped
49 -> 45 (48 total missing per the coverage report, minus the 3
zero-breadth `Excommunicate` entries no skill can reach).

## Real incident: manual identification work destroyed by re-running the harvester, and the durable fix

User had manually renamed several `unlabeled_<hash>.png` files directly
on disk to their identified `<visual_key>.png` names -- a reasonable-
looking shortcut, but not the mechanism `harvest_support_icons.py`
actually respects. Re-running it (for the icon-selection bug fix above)
hard-deleted every top-level PNG and rebuilt the folder fresh from
`captures/` + `tier_notes.json` + `manual_labels.json`, per its own
long-documented "Clear PNGs... before writing this one's" behavior --
silently wiping the renames, since they were never recorded anywhere
the script reads back in. Made worse by `assets/harvested_supports/`
not having been committed to git since before this session's work
started (confirmed: everything was either untracked `??` or a stale
diff against a much older commit) -- no git-based recovery existed
either. Real lesson, on me: I re-ran a script whose own docstring says
it deletes and rebuilds the output folder, right after manual work had
plausibly happened there, without checking `git status` on that folder
or asking first. Recovery (if any) only possible via OneDrive's own
file version history, outside this project's tooling entirely.

User's own pushback, correctly: committing the folder isn't the fix
either while it's full of `unlabeled_*` placeholders -- that's the
actual problem being solved, not a reason to freeze it in git as-is.

**Durable fix**: `tools/generate_manual_labeling_page.py` (new) --
generates a local, offline HTML page (never published as a Claude
Artifact; these are real GGG game assets, see `THIRD_PARTY_NOTICES.md`)
showing every unresolved crop next to clickable buttons for its
already-narrowed candidate list (from `manifest.json`'s `candidates`
field -- most are down to 2-4 real options already, not blind
identification). Produces JSON to paste into `manual_labels.json`
directly -- the one mechanism `harvest_support_icons.py` actually
reads back in and never overwrites itself, so an identification
recorded there survives every future re-harvest instead of being
silently wiped by the next run. `.claude/skills/support-coverage-refresh/
SKILL.md` updated with an explicit warning to check `git status` on
`assets/harvested_supports/` before running the harvest step if manual
work might be in progress, plus this as an optional step 4.

## User's excellent question: "shouldn't the system recognize the icon and determine the tier by region?" -- yes, and it wasn't wired in

Prompted by the manual-review page still showing a genuinely confusing
case (`Chain` vs `Multiple Projectiles`, two completely different real
icons, both structurally possible for the same two skills with no way
to tell which from skill names alone). User's question cut right to
the real gap: `capture_pipeline.match_support_icon()` -- the SAME
embedding classifier already validated at 100% against 2094 real
ground-truth crops -- had never been used by `harvest_support_icons.py`
itself. The harvester's own candidate-narrowing is purely textual
(`PossibleSupports` intersection), and the two pipelines had simply
never been connected.

**Tested directly before trusting it**: ran `match_support_icon()`
against all 108 crops stuck in the ambiguous-inferred bucket. It
confidently resolved all 108 -- but a naive comparison against
manifest.json's `candidates` field showed only 14 agreeing, which
looked alarming until the bug turned out to be in the DIAGNOSTIC, not
the classifier: `candidates` stores bare family names
(`canonical_display_name()`-style, e.g. "Minion Damage"), while the
embedding's resolved member is the exact tiered literal name (e.g.
"Lesser Minion Damage") -- comparing by ICON instead of by raw name
string gave the real number: **99 of 108 agreed**, only 9 genuinely
disagreed.

**Wired in as a fourth resolution source**, `resolve_via_embedding()` in
`harvest_support_icons.py`, sitting between `manual_labels.json` and
`tier_notes.json`-narrowed inference: runs `match_support_icon()` on
the crop, then only trusts the result if it agrees with what text-based
narrowing already knows is structurally possible (never trusted alone
-- an icon the classifier likes but that isn't even a structural
candidate would be a real misidentification, not a resolution). New
manifest `source` value `"embedding_confirmed"`, kept visibly distinct
from `"manual"` (human eyes) and `"inferred"` (text alone) -- always
disclosed, never silently merged into either.

`harvest_support_icons.py` also now `os.chdir(PROJECT_ROOT)`s at import
time and imports `capture_pipeline` directly, since the classifier
needs cwd-relative asset paths to resolve correctly and this script's
own paths were already PROJECT_ROOT-based regardless of invocation
directory -- runs correctly now whether invoked from `tools/` (as
documented) or the project root.

**Real result on this run**: 71 of 108 previously-stuck crops resolved
with zero human input. `ambiguous, inferred candidate list` bucket:
108 -> 9. Coverage: 111/159 -> 140/159 (biggest single jump this
project has seen). `match_support_icon.py`'s own validation held at
100% (2094/2094) throughout -- this only ever touches the harvester's
offline candidate resolution, never production matching itself.
`SUPPORT COVERAGE BY SKILL` gap count: 45 -> 16. The remaining 9 are
genuinely unresolvable by any current signal (embedding disagreed with
structural candidates, or didn't clear the confidence threshold) --
manual_review.html now correctly shows only those 9, not 108.

## Two more real fixes from the same review session

**Fix 1 -- harvester's single-candidate resolution had the same "tier
without icon" gap already fixed once for Gilded/IncreaseAreaOfEffect,
just not generalized.** User noticed 3 crops on the review page each
showing exactly ONE candidate button, yet still sitting in the
ambiguous-inferred bucket instead of being auto-resolved. Root cause:
`tier_matches` (harvest_support_icons.py) filtered members by tier
alone, not icon -- a family like `Duration` spans two unrelated icons
(`increasedduration` "More Duration" / `reduceduration` "Less
Duration"), and when one member of EACH icon happens to land on the
same tier, this correctly refused to guess between them even when only
ONE of those icons was actually possible for the contributing skill(s)
(the exact structural fact `resolve_via_embedding()` and the manual
review page already check). Fixed by adding the same `icon in
common_icons` filter here. Result: ambiguous-inferred bucket 9 -> 7.

**Fix 2 -- a skill's own row can fully resolve an icon+tier collision
without any new information, just game-rule logic.** User's insight,
prompted by a real capture (`__captures_campaign/20260917_213531`,
Reanimator Zixa): Raise Zombie of Gigantism rolled BOTH Minion Damage
and Minion Life at once (2 slots, both printing the same "Minion Damage
(Tier: 1) or Minion Life (Tier: 1)" ambiguous string). Validated the
underlying assumption first, not just trusted it: checked every real
ground-truth-backed skill row on hand (663 of them) for a literal
duplicate support name -- zero found. So when N slots in one row share
the identical unresolved collision string and that collision has
exactly N members, each candidate is forced to appear exactly once --
a real constraint, not a heuristic. Implemented as
`_resolve_same_skill_collisions()` in `capture_pipeline.py`, applied to
every row in `extract_support_names()`: resolves the row to "Minion
Damage (Tier: 1)" once and "Minion Life (Tier: 1)" once, instead of the
same ambiguous string printed twice. Doesn't determine which physical
slot is which (the image never says that), but fully resolves what the
skill actually has, which is what a reader cares about. Confirmed
against the real triggering capture directly, then locked in as a new
`SUPPORT_EXTRACTION_CASES` fixture (`reanimator_zixa.png`) since no
real `warrant.txt` can ever exist for this level-32 capture to verify
against otherwise (structurally impossible below level 68).

Neither fix touches the harvested reference catalog itself (both are
about resolving what's already known to be true from structural logic,
not learning anything new about an icon) -- Fix 1 lives in the
harvester's inference resolution, Fix 2 in live production output.
Full test suite: no regressions, `SUPPORT EXTRACTION` now 4/4.

## User's framework cut through the remaining noise: two real bugs, zero crops left to review

User proposed a clean model for what the manual review page should ever
show: (1) genuinely unseen supports needing real identification, or (2)
a gap in the identification tool that a manual answer helps fix in
code. Then asked, pointedly, why a single-candidate crop still needed a
click, and why a clearly-MinionDamage-icon crop was being offered
"Brutality"/"Mitigation Ignore" as options. Neither case fit the
framework -- which meant neither was actually case 1 or 2, and
investigating why turned up two more real bugs in
`resolve_via_embedding()`'s wiring, not edge cases to route around.

**Bug 1**: gated on `len(candidates) > 1`. A crop with exactly ONE
candidate identity ("PhysGainAs") but no `tier_notes.json` entry, whose
identity spans multiple tiers, can't be resolved by the existing
single-candidate block either (it needs a tier and has no way to get
one) -- but the embedding check that COULD supply that tier via its own
badge-reading never even ran, since one candidate isn't `> 1`. Checked
directly: `match_support_icon()` already correctly resolves this exact
crop to `mercsilverstrintsupportgem_i`, and of that key's two real
members (`Lesser Ironwood` family `TotemDefences`, `Lesser Physical as
Extra` family `PhysGainAs`), only "Lesser Physical as Extra" is
structurally possible for either contributing skill (Meteor, Sanctified
Strike don't offer Ironwood at all) -- a clean, unambiguous resolution
that was simply never attempted. Fixed by broadening the gate to
`>= 1`.

**Bug 2**: `resolve_via_embedding()` only ever returned a single
resolution or nothing, with no way to express "the classifier
recognized the icon exactly, and it renders a REAL, proven collision"
-- so a crop the classifier confidently, correctly identified as
`miniondamage_i` (verified directly: `match_support_icon()` -> exactly
`{Lesser Minion Damage, Lesser Minion Life}`, nothing else) still fell
through to the generic ambiguous-inferred bucket, which built its
candidate list from ALL identities reachable via ANY icon the
contributing skill's pool touches (six different icons for a
single-skill cluster, with no other skill to intersect against) --
hence "Brutality"/"Mitigation Ignore" bleeding in from a totally
unrelated icon the same skill also happens to offer. Neither case 1
(genuinely unseen -- it isn't, this exact collision is already proven
elsewhere) nor case 2 (a fixable gap -- it can't be resolved further,
by any means) applied; showing it as if it needed a decision was itself
the bug. Fixed by having `resolve_via_embedding()` return the full
tied-match list, not just a single-or-nothing result: when every tied
member IS structurally possible (not a disagreement, a confirmed
collision), the loop now sets `visual_key` directly and narrows
`candidates` to just those tied identities -- which lets the EXISTING
by_key merge pass (built for exactly this, see the ground-truth version
above) fold it into `visual_key_ambiguous` on its own, no new
merge logic needed.

User also asked directly whether the exact real capture behind
`_resolve_same_skill_collisions()` (Reanimator Zixa,
`20260917_213531`) could let us arbitrarily assign one of these two
specific hashes to Minion Damage and the other to Minion Life, since we
know for certain it's one of each. Checked: yes, those two hashes
(`228e02e13f`, `54e049e58d`) are exactly that capture's two slots.
Declined anyway, on purpose: `miniondamage_i` is already a covered
reference key, so resolving these two specific hash clusters adds zero
coverage -- the only effect of an arbitrary per-hash assignment would
be recording a guess as a confirmed fact in the permanent catalog,
directly against this project's "don't guess, flag it" discipline
everywhere else. Correctly filing them as the known collision (which
Bug 2's fix does) is the honest outcome; picking one arbitrarily isn't.

**Result**: ambiguous-inferred bucket 7 -> 0. `manual_review.html` now
correctly shows nothing (with an explicit "nothing to review" message
rather than a blank page). Embedding-confirmed count 65 -> 100.
Coverage held at 140/159 (neither fix adds new coverage -- both were
about correctly classifying crops for icons already in the catalog).
Classifier validation still 100% (2094/2094), full test suite: no
regressions.

## Independent shadow-ground-truth pipeline for campaign captures

Direct follow-through on the previous validation-gap discussion (84% of
Tier I references are `embedding_confirmed`, self-checked only against
structural plausibility, never against an independent source). User's
plan: capture more sub-68 mercenaries, but this time isolate them
entirely from the normal harvest and record what each support really is
by reading the game's own tooltip -- a genuinely independent check,
done live during each encounter for speed. Three pieces:

**1. `[C]` capture mode (`capture_pipeline.py`)**: `_prompt_for_capture_mode()`,
asked once at startup alongside the existing exception-tag prompt.
`[C]` saves the whole session's captures to `captures_campaign/`
instead of `captures/`; `capture_dir` threaded through
`on_capture()`/`_safe_on_capture()`. Nothing else about a campaign
capture differs -- same crops, same `warrant_generated.txt`, same log
row.

**2. `tools/harvest_campaign_review.py`** (new) -- deliberately a
separate script, not a mode flag on `harvest_support_icons.py`, so
isolation is structural rather than a runtime option someone could
forget to pass. Scans `captures_campaign/` only; confirmed by
construction (not just intent) that the main harvester's own
`find_capture_dirs()` glob patterns (`captures/*`, `__captures_*/*`)
cannot match it either way. Reuses `harvest_support_icons.py`'s
`load_grid()`/`OCCUPIED_STD_THRESHOLD`/`parse_warrant_skills()` directly
(imported, not copied -- same direction as `resolve_via_embedding()`
calling `capture_pipeline` directly rather than duplicating its logic,
now extended to this tool too) but never calls `match_support_icon()`
or reads the reference catalog anywhere -- candidates are built purely
from each contributing skill's exact `PossibleSupports` strings, tier
already baked in, so the option list is skill-anchored and never
touches icon recognition at all. Writes to `assets/campaign_review/`,
a location `assets/harvested_supports/`'s own tooling never reads.

Real bug caught by testing with actual data before trusting it: candidate
buttons show the `PossibleSupports` form ("Lesser Chain I"), but
`definitions/supports.json`'s own keys never carry the trailing tier
("Lesser Chain") -- confirmed the same gap `generate_support_coverage_report.py`
already documented, but freshly rediscovered here since this script
validates against `supports.json` directly. Every legitimate button
click would have failed validation and been silently dropped. Fixed by
stripping the tier suffix before validating/storing, then verified with
a real clipboard round-trip (button-form and bare free-text form both
merge correctly now) before calling it done.

**Fast loop**: press capture hotkey (campaign mode) -> run
`python tools/harvest_campaign_review.py` -> open
`assets/campaign_review/review.html`, hover each support in-game, click
the matching button, copy the result -> run the script again (merges
the clipboard, regenerates the page for whatever's left or newly
captured). `.claude/skills/campaign-review/SKILL.md` documents this
loop for quick invocation.

This data is intentionally NEVER fed into `assets/harvested_supports/`
-- the whole point is auditing that pipeline's Tier I confidence against
something it didn't produce itself, not adding another self-referential
input to it.

**Update: the two separate startup prompts got consolidated into one.**
User's call: campaign mode and the exception tags (Rematch/No Infamous
chance/Infamy) are mutually exclusive in practice -- a session dedicated
to the campaign shadow-ground-truth workflow is never also tagged as
one of those -- so asking them as two sequential blocking prompts was
one more keypress than needed. `_prompt_for_capture_mode()` is gone;
`_prompt_for_session_exception()` is now `_prompt_for_session_options()`,
handling `[R]`/`[N]`/`[I]`/`[C]`/`[ENTER]` as one set of mutually
exclusive choices and returning `(session_exception, capture_dir)`
together. Behavior is otherwise identical -- same keys, same directory
routing, same "blocks indefinitely" reasoning -- just one prompt instead
of two.

## First real payoff of the campaign audit: a genuine badge-reader false negative

Ran `tools/audit_campaign_truth.py` against the first 61 human-verified
campaign answers: 59/61 agreed, 2 disagreed, both the same real support
("Lesser Lightning Penetration") at two different hashes. Traced both
precisely rather than guessing: `_resolve_support_tier_from_badge()`
correctly found an 11px gold bar (well above the length threshold) on
both, but rejected it at the color-uniformity check --
`core_std` 12.0/13.3, above the `_SUPPORT_BADGE_CORE_STD_MAX = 10.0`
ceiling from the earlier poison-icon fix. That threshold was validated
against real bars measuring 0.0-1.7 and the one known false positive
(poison's fang art) measuring 31.7-56.6 -- a sample that happened not to
include a real bar with a subtle highlight/shine gradient, which this
icon's own art has. With the badge reader returning None, both crops
fell back to raw embedding similarity, which favored Tier II/III simply
because `lightningpenetration_i` had no reference in the catalog at all
to compete against.

Raised `_SUPPORT_BADGE_CORE_STD_MAX` to 20.0 (both copies -- it's
duplicated between `capture_pipeline.py` and
`tools/match_support_icon.py`'s own validation script, not shared code)
-- comfortably covers both known real-bar ranges (0.0-1.7 and
12.0-13.3) while staying well clear of the fake-art floor (31.7).
Validated before calling it done, same way every threshold change here
has been:
- `tools/match_support_icon.py`'s 2094-crop validation: still 100%,
  fired-count unchanged (that validation set structurally can't
  exercise this case either -- it only scores crops whose ground truth
  already has a harvested reference, same limitation noted when this
  suite was first built).
- `audit_campaign_truth.py`: **65/65 agree now** (0 disagreements, 0
  no-matches) -- both real cases fixed.
- Full harvest re-run: coverage 140/159 -> **142/159**
  (`lightningpenetration_i` and one more key now resolvable).
- `tests.py`: no regressions; added
  `test_data/supports/tiers/tier_i_4_lightningpenetration.png` as a
  permanent fixture (`SUPPORT TIER DETECTION` now 5/5) since this is
  exactly the kind of real, reproduced bug this project always turns
  into a regression test.

This is the campaign audit pipeline doing exactly what it was built
for: catching a real classifier gap that the existing validation
methodology was structurally blind to, using data the classifier had no
part in producing.

## A fifth manifest source: promoting a specific campaign identification into the catalog

After the badge-threshold fix above, the user asked the natural next
question: several of the still-missing (icon, tier) keys in
`assets/support_icon_coverage.md` are Tier I ("Lesser") rolls that skew
toward campaign play precisely because they're rarest to catch on
camera at the level-68+ a real warrant requires. The campaign
shadow-ground-truth pipeline (`harvest_campaign_review.py`,
`manual_truth.json`) already has positive, human-confirmed identities
for some of these -- read straight off the real in-game tooltip. Since
that pipeline was deliberately built to stay independent of
`assets/harvested_supports/` (see its own docstring, and the earlier
entry above on why), the question was whether a *specific* identified
crop could be deliberately promoted into the production catalog without
undermining that independence.

The answer: yes, and it's actually cleaner provenance than a typical
`manual_labels.json` entry. `generate_manual_labeling_page.py`'s
candidate list is narrowed using `match_support_icon()` itself, so a
human's click there is partly informed by the classifier being
validated. A campaign-review identification is anchored purely to the
contributing skill's real `PossibleSupports` pool -- zero icon-matching
involvement at any point.

The one real hazard: a crop can't do both jobs (audit fodder and
production reference) at once. If a promoted crop becomes a reference
image feeding the embedding matcher, re-running `audit_campaign_truth.py`
against that same crop afterward is circular -- checking whether the
classifier matches an image against itself, not an independent check
anymore.

Built `tools/promote_campaign_truth.py <hash-or-prefix>...`:
- Copies the crop from (gitignored, regenerated-fresh-each-run)
  `assets/campaign_review/crops/` into a new, persistent, committed
  `assets/harvested_supports/campaign_promoted/<hash>.png`.
- Records `{full_hash: name}` in a new, committed
  `assets/harvested_supports/campaign_promotions.json` -- hand-maintained
  by this tool only, same read-only convention `harvest_support_icons.py`
  already applies to `tier_notes.json`/`manual_labels.json`.
- Removes the promoted hash from `manual_truth.json` -- not a loss, its
  new home is `campaign_promotions.json` -- specifically so it drops out
  of `audit_campaign_truth.py`'s pool once it's no longer an independent
  check.

`harvest_support_icons.py` now loads `campaign_promotions.json` after
its normal real-capture scan and seeds one synthetic cluster per
promoted hash (recomputing and verifying the hash from the saved PNG
before trusting it -- same "never silently trust an override file"
discipline as every other hand-maintained input here), resolved via a
new branch checked before ground truth: `source = "campaign_confirmed"`.
If the same exact hash also happens to appear in a real capture, that
occurrence's own resolution wins and the promotion is skipped (logged,
not silently dropped) -- the promotion only ever fills a genuine gap,
never overrides a real capture's own finding. These synthetic clusters
flow through the exact same by-visual-key merge/dedup pass as everything
else, so a promoted crop that disagrees with what real captures already
established for that icon+tier gets caught as `visual_key_ambiguous`
rather than silently overwriting it.

Promoted the actual crop that prompted this
(`744f10cdcea66b60ba66ba1de0c12a7a`, "Lesser Melee Physical Damage",
resolves to visual_key `increasedphysicaldamage_i`) and validated the
whole chain:
- `harvest_support_icons.py`: `promoted from the campaign shadow-ground-
  truth: 1`, new reference file written
  (`increasedphysicaldamage_i.png`).
- `generate_support_coverage_report.py`: **142/159 -> 143/159** covered.
- `audit_campaign_truth.py`: pool correctly shrank **65 -> 64** (the
  promoted hash dropped out), still **64/64 agree, 0 disagree, 0
  no-match** -- no circularity introduced.
- `export_support_reference_embeddings.py`: 143 reference keys (was
  142).
- `tools/match_support_icon.py`'s 2094-crop validation: still 100%,
  unchanged.
- `tests.py`: no regressions.

## Investigating a support with real breadth but zero real hits: Mitigation Ignore

After the campaign-promotion work above, the user asked about the
Mitigation Ignore family specifically -- still missing all 3 tiers from
`assets/support_icon_coverage.md` -- and floated a natural hypothesis:
maybe it's actually excluded from the live drop pool, and just
happens to only be reachable through an uncommon mercenary archetype
we haven't captured much of lately.

Checked directly rather than guessing either way:
- **Breadth**: 20 skills structurally offer it per
  `definitions/supports_by_skills.json` (identical breadth for all 3
  tiers) -- Barrage of Volley Fire, Bear Trap, Cleave, Corrupted Blade
  Vortex of the Scythe, Earthquake of Amplification, Ensnaring Arrow,
  Greater Split Arrow, Leap Slam of Groundbreaking, Rain of Arrows of
  Saturation, Raise Zombie of Gigantism, Reap, Shrapnel Ballista, Siege
  Ballista of Trarthus, Split Arrow, Summon Skeletons, Sunder of
  Trarthus, Tectonic Cascade, Tornado Shot, Vaal Ground Slam, Vaal Reap.
- Those 20 skills trace to only 6 mercenary archetypes (Blade Ambusher,
  Earthshaker, Reanimator, Ripper, Sanguimancer, Sniper, each with an
  Infamous variant) -- **the "uncommon merc" theory doesn't hold**:
  matched real+campaign capture dirs against each archetype's full
  skill pool and got 79 (Reanimator), 70 (Sniper), 72 (Blade Ambusher),
  67 (Sanguimancer), 43 (Earthshaker), 39 (Ripper) -- all commonly seen,
  none of them a rare/overdue archetype.
- The real, striking number: **38 real encounters had a genuine
  `warrant.txt`** (100%-certain ground truth, zero inference) for one
  of these 20 skills, totaling **153 actually-equipped support slots**.
  Mitigation Ignore (any tier) appears in **zero** of them -- a counted
  sample, not a coverage gap dressed up as one.
- The user independently cross-checked Earthquake of Amplification
  against two separate community PoE-trade-adjacent tools: **zero
  listed warrants show Mitigation Ignore on that skill either.**

Neither signal alone proves the affix is dead in the current game
version -- 153 real slots isn't an astronomical sample if the affix
has an unusually low roll weight relative to everything else in these
skills' pools, and trade listings only reflect what happens to be for
sale right now, not the full universe of what's obtainable. But the
two independent signals (real captures AND live trade) point the same
direction, which is a meaningfully stronger case than either alone.
Not escalated to the same certainty as the confirmed zero-breadth
Excommunicate family (that one has NO listed breadth anywhere in the
extracted data at all -- this one has real, structural breadth, just a
suspicious real-world absence) -- flagged here as worth continued
monitoring, not concluded as fact, per this project's own "never
silently guess" rule.

**Update:** The user then checked all 20 skills (not just Earthquake of
Amplification) directly on the official `pathofexile.com/trade` site --
**zero warrants show any Mitigation Ignore tier on any of the 20
skills.** This is materially stronger than the single-skill check
above: official first-party trade data, full coverage of every skill
that structurally lists it as possible, unanimous zero. Doesn't reach
the same category as a confirmed zero-breadth support (nothing here
proves it's impossible, only that it hasn't been listed for sale by
anyone across every skill that could produce it), but this is about as
much practical verification as is available without developer-side
confirmation. Treating this as a strong signal to stop spending capture
effort specifically hunting Mitigation Ignore going forward -- not
formally reclassified as zero-breadth in `assets/support_icon_coverage.md`
(that section is reserved for what the extracted game data itself
proves, not external trade observation), but functionally in the same
"probably not worth chasing" bucket.

**Update:** Formalized this into `assets/support_icon_coverage.md` itself
rather than leaving it as a chat-only conclusion. New hand-maintained
`assets/support_icon_coverage_notes.json` (read-only from
`generate_support_coverage_report.py`'s side, same convention as
`tier_notes.json`/`manual_labels.json`/`campaign_promotions.json`) holds
a list of `{reason, names}` groups; the report now excludes any missing
key belonging to such a group from the main hunting-targets table and
lists it instead under a new "## Supports unlikely to ever get a
captured reference" section with two clearly separate subsections:
"Confirmed zero breadth" (still computed fresh from the extracted data
every run, unchanged logic, just relabeled/regrouped -- this is where
the Excommunicate family now lives) and "Suspected non-live" (the new
hand-flagged group -- currently just Mitigation Ignore, with the exact
trade-site + real-capture evidence as its `reason` text). Deliberately
NOT unified into one data source: the zero-breadth fact is provably
correct from data on every run, while the suspected-non-live flag is a
human judgment call that could later be reversed by new evidence --
conflating them would blur a confidence distinction this project has
been careful to keep everywhere else (ground_truth vs. manual vs.
embedding_confirmed vs. inferred). Validated: coverage report
regenerates cleanly with both groups showing correctly, `tests.py` has
no regressions.

## A real bug: promoting a hash made its own old capture look unanswered again

Found via the user's sharp observation, not a self-review: after
promoting `744f10cdce` (Lesser Melee Physical Damage) earlier this
session, the very next `harvest_campaign_review.py` run showed a "new"
crop needing review with contributing skill "Triggerblades" -- flagged
as suspicious ("that encounter doesn't have Triggerblades as a skill").

Checked the actual capture (`captures_campaign/20260918_085903`,
timestamped 08:59 that morning): its own `skills.png` and OCR'd
`warrant_generated.txt` both genuinely show Triggerblades as skill #3,
with `Lesser Melee Physical Damage (Tier: 1)` directly under it -- no
parsing or OCR bug at all. The user then asked the better question:
this capture is hours old, so why is it only being "pulled up" now?

Root cause: `promote_campaign_truth.py` deliberately REMOVES a promoted
hash from `manual_truth.json` (so it stops double-counting as an
independent audit check -- see the promotion writeup above). But
`harvest_campaign_review.py`'s `generate_review_html()` only ever
checked `manual_truth.json` to decide what still "needs review" -- it
had no idea `campaign_promotions.json` existed. The instant
`744f10cdce` got promoted, this OLD, already-fully-resolved capture
(same real support, same pixel hash, rolled again on a different
skill) lost its "already answered" marker and resurfaced as if nobody
had ever identified it -- not new data, a real regression introduced by
the promotion tool built earlier this same session.

Fixed in `tools/harvest_campaign_review.py`:
- `generate_review_html()` now takes `known` (manual_truth.json UNION
  campaign_promotions.json), not just `truth` -- a promoted hash is
  exactly as resolved as an unpromoted one, just living in a different
  file.
- `merge_clipboard_into_truth()` now also refuses to re-merge an
  already-promoted hash back into `manual_truth.json`, closing the same
  gap on the write side (a user could otherwise re-answer a stale
  resurfaced card and re-add a hash that promotion deliberately
  removed).
- `main()`'s summary line now reports how many of "answered so far" are
  already-promoted, so this stays visible instead of silently folded
  into one number.

Validated: re-ran `harvest_campaign_review.py` -- "5 need review"
dropped back to the correct "4 need review" (the promoted hash's card
is gone), `audit_campaign_truth.py` unchanged at 64/64 agree, full
`tests.py` no regressions. A caution for next time: any hand-maintained
override/promotion file introduced into one part of this project's
several interlocking review pipelines needs an explicit audit of every
OTHER script that reads from the file(s) it's implicitly superseding --
this one was missed because `harvest_campaign_review.py` and
`promote_campaign_truth.py` were built and tested independently, and
the interaction between them only showed up on a second real use.

## generate_campaign_audit_coverage.py undercounted corroboration by relying on exact hash

The user submitted a campaign identification for "Lesser Added Chaos"
and, two captures later, asked why the coverage report still listed it
as "not yet audited." Checked directly: the production catalog's
`addedchaosdamage_i` reference has hash `a1cbb2fd0e...`, the user's new
campaign crop hashed to `caf563ef25...` -- completely different bytes.
Confirmed pixel-level: 37.4% of pixels differ between the two crops
(mean abs diff ~10.9) -- the same real-capture-jitter phenomenon
documented earlier this session (likely animated shimmer/glow VFX),
not a different icon. Both are genuinely "Lesser Added Chaos."

Root cause: `generate_campaign_audit_coverage.py` only ever checked
whether a Tier I key's OWN specific catalog hash also appeared in
`manual_truth.json`/`campaign_promotions.json` -- true pixel-identical
corroboration only. Whenever capture jitter gives a fresh campaign roll
of an already-covered identity a different hash than the one sitting in
the catalog (apparently common, not rare), the real corroboration was
silently invisible to this report.

Fixed by adding a second, explicitly weaker evidence tier: a reverse
index from name -> every hash independently confirmed as that name,
checked whenever the exact-hash check finds nothing. The two are never
conflated in the output -- the "Confirmations" column now says "N
pixel-identical" vs. "N different capture, same name" so the strength
of evidence stays visible, same discipline as every other source label
in this project. Effect on the actual numbers: audited-and-agreeing
Tier I keys jumped from 20/40 to **30/40** once name-based
corroboration was counted -- the exact-hash-only version had been
significantly undercounting real, already-existing evidence the whole
time, not because of missing data but because of how the report itself
compared it.

## Support tier rolls shift hard toward II/III as mercenary level climbs

User's own observation while capturing: "a dramatic shift towards II
and III in lvl 46 zones." Checked directly across all 24
`captures_campaign/` sessions so far (every occupied support slot with
a known identity, via `manual_truth.json` + `campaign_promotions.json`),
bucketed by the mercenary's own `Mercenary Level:` line:

```
level 32-38 (135 slots): 93% Tier I,  0% Tier II,  7% Tier III
level 39-45 (9 slots):   67% Tier I, 33% Tier II,  0% Tier III
level 46+   (20 slots):  45% Tier I, 35% Tier II, 20% Tier III
```

Real and substantial, not sampling noise -- Tier I share drops from 93%
to 45% across this level range. Consistent with (and a more precise,
level-resolved version of) the general "real captures skew toward
high-tier rolls" note already in `assets/support_icon_coverage.md`.
Practical implication for future campaign sessions: low-level zones
(30s) are the best source for closing remaining Tier I gaps
specifically; 46+ zones behave increasingly like the main `captures/`
pipeline (already II/III-heavy) rather than adding much unique Tier I
value.

## Correction: "not found on trade" is weak evidence for a Tier I support

User's own insight, prompted by checking Lesser Generosity and Lesser
Leech: both are absent from `pathofexile.com/trade` search results, but
their Tier II/III siblings ARE found there. The reason isn't rarity in
the structural sense -- it's that Tier I ("Lesser") rolls are heavily a
low-level campaign phenomenon (quantified earlier this session: 93%
Tier I at mercenary level 32-38, dropping to 45% at 46+), and low-level
campaign gear generally isn't worth listing for trade at all. A trade
search will systematically undercount ANY Tier I support regardless of
whether it's actually obtainable -- the absence reflects what players
bother to sell, not what the game can produce.

This means the trade-search leg of the earlier Mitigation Ignore
writeup ("zero warrants across all 20 possible skills checked directly
on pathofexile.com/trade") needs to be downgraded -- it was never good
evidence for a Tier I-specific question, and shouldn't have been
weighted as equal to the other leg of that finding. The OTHER leg still
stands on its own, though: 153 real, ground-truth-certain equipped
support slots across this project's own real captures (not filtered by
what anyone chose to trade -- an unbiased sample of actual gameplay)
showed zero Mitigation Ignore hits. That evidence doesn't have the
trade-selection-bias problem and remains the actually meaningful part
of that finding.

General lesson for future investigations: a trade-market check can
corroborate or investigate a NORMAL-tier or high-tier support's
obtainability, but is close to useless for a Tier I-specific question
-- use real capture data (this project's own, ground-truth-backed where
possible) instead. `support_icon_coverage_notes.json`'s
"suspected_nonlive" group correctly still only lists Mitigation Ignore,
justified by the real-capture leg alone -- Lesser Generosity/Lesser
Leech were correctly NOT added there; their trade absence is fully
explained by this effect, not suspicious.

## Rage on Hit: the "zero on trade" was a broken query, not a signal at all

Follow-up to the trade-verification caution above -- a different failure
mode, worth keeping distinct. User initially reported zero trade
results for Rage on Hit across any skill/tier. This one was checkable
against harder evidence than a trade search: `ragesupport_ii` ("Rage on
Hit") and `ragesupport_iii` ("Greater Rage on Hit") are BOTH already in
this project's own catalog via real `ground_truth` -- actual warrant.txt
item text from real captures, Greater confirmed 3 separate times. That
directly contradicts a genuine zero-trade-listings result, which is
what made this worth pushing on rather than accepting at face value.

Resolution: the user re-checked and found the trade query itself was
broken/malformed -- searching by skill (Cleave) directly does surface
warrants with Rage on Hit. Not a drop-pool question, not a low-level
trade-visibility question (the previous entry's caveat) -- just a bad
search the first time.

Lesson: a "zero results" trade check is only as good as the query that
produced it, and should be cross-validated against this project's own
ground_truth data before being trusted as a real signal -- exactly what
caught this one. Contrast with Mitigation Ignore (real signal, backed
by 153 real ground-truth-certain slots with zero hits, no contradicting
evidence anywhere) and Generosity/Leech (explained away by low-level
trade-visibility bias, not a query bug) -- three genuinely different
outcomes from the same style of check, each resolved on its own
evidence rather than pattern-matched to the others.

## A taxonomy for interpreting a "zero results" trade check

Distilled from three real investigations this session (Mitigation
Ignore, Lesser Generosity/Lesser Leech, Rage on Hit) -- a trade search
returning nothing means one of three genuinely different things, and
conflating them was the recurring mistake worth guarding against going
forward:

1. **Rarity/value, not obtainability** (Lesser Leech, Lesser
   Generosity). The support is real and obtainable, but a Tier I
   ("Lesser") roll skews toward low-level campaign content that players
   rarely bother listing for trade (quantified earlier: 93% Tier I at
   mercenary level 32-38, dropping to 45% at 46+). Diagnostic: the
   SAME family's II/III tiers DO show up on trade, and/or the support's
   real breadth (skills that can roll it) is narrow, meaning the sample
   of trade-visible items was always going to be thin regardless of
   truth. A trade zero here carries almost no evidential weight.

2. **The trade query itself is broken** (Rage on Hit). Diagnostic:
   contradicted by harder evidence this project already has -- a real
   `ground_truth` entry in `assets/harvested_supports/manifest.json`
   (an actual warrant.txt's item text, zero ambiguity) proves the
   support exists and gets equipped in real gameplay. If a trade search
   says otherwise, the search is wrong, not the game -- re-verify the
   query (skill-based search worked where a raw affix-name search
   didn't, in this case) before drawing any other conclusion.

3. **Genuinely not currently obtainable** (Mitigation Ignore). No
   contradicting `ground_truth`/`campaign_confirmed` evidence anywhere
   in this project's own data, REAL breadth is broad (20 skills, not a
   narrow 1-5 skill case), and -- the actually load-bearing evidence --
   a large, unbiased real-capture sample (153 ground-truth-certain
   equipped slots across this project's own captures, not filtered by
   what anyone chose to trade) also shows zero hits. Trade absence adds
   corroboration here but was never the primary evidence; the real
   capture sample was.

Practical checklist before trusting a trade "zero results" for
anything: (a) is this a Tier I / narrow-breadth case? -- if so, expect
under-representation regardless of truth, treat as uninformative; (b)
does it contradict a `ground_truth` entry already in this project's own
manifest? -- if so, the query is broken, not the data; (c) only once
both of those are ruled out does a broad, corroborated trade zero add
real signal, and even then it should be backed by this project's own
real-capture data (see `support_icon_coverage_notes.json`'s
"suspected_nonlive" group), never trade alone.

**Update:** A 4th scenario, distinct from #2 (the site/query being
broken): **user error operating the trade interface**. Found via
Generosity -- Greater Generosity shows up on trade, but plain
"Generosity" (Tier II) and Lesser Generosity do not, even though this
project's OWN `ground_truth` data already proves Tier II "Generosity"
is real (an actual warrant.txt capture, zero ambiguity -- see
`generositysupport_ii` in `assets/harvested_supports/manifest.json`).
Same contradiction-with-ground_truth diagnostic as scenario #2, but the
cause this time was on the human side of the search (a filter/tier
selection easy to get wrong on the trade site's own UI), not a broken
query or malformed search term. Practically the same fix either way --
re-verify the search methodology before trusting a zero -- but worth
keeping as its own category since "the tool is broken" and "I used the
tool wrong" call for different follow-up (report vs. re-learn the UI).

**Update (refining scenario 1):** A single real warrant the user found
on trade had BOTH `Pierce` (II, not Lesser) on Kinetic Bolt AND `Lesser
Spell Cascade` on Flame Wall. The Lesser Spell Cascade sighting is a
useful positive control: it proves a Tier I roll CAN and does surface
in trade search results sometimes -- scenario 1 isn't "Lesser items are
invisible to trade," it's "Lesser items are rarer/sparser on trade,"
a real distinction. Separately, `Lesser Pierce` not showing for a
Kinetic-Bolt-specific search carries no weight at all here, independent
of the trade-visibility question: this project already has it
confirmed and promoted (`807691371a`, from Puncture, not Kinetic Bolt --
19 skills total can roll it, so one specific skill not showing it in a
trade sample is unremarkable, not a gap).

## Lesser Leech and Lesser Rage on Hit added to suspected-nonlive; Lesser Generosity deliberately left out

Full-scour investigation (every manifest entry touching each family's
possible skills, including ambiguous/unresolved ones) confirmed both
belong in `support_icon_coverage_notes.json` alongside Mitigation
Ignore -- but ONLY their Lesser tier, since both families' II/III are
already solidly `ground_truth`-confirmed (real warrant.txt, multiple
times each):

- **Lesser Leech**: 58 distinct crops / 192 total occurrences across
  its 5 possible skills, zero hits, no ambiguous crop lists it as a
  candidate either. User independently confirmed Leech/Greater Leech
  show on trade but Lesser doesn't.
- **Lesser Rage on Hit**: 21 distinct crops / 70 total occurrences
  across its 5 possible skills (all `ground_truth`), zero hits. Trade
  evidence for this family was the "broken query" case from earlier --
  this flag rests entirely on the real-capture evidence, not trade.

**Lesser Generosity was deliberately NOT added**, despite showing the
same trade-absence pattern (Generosity/Greater Generosity found on
trade, Lesser not) -- checked its real sample directly: only 7 distinct
crops / 21 total occurrences from Smite, the ONE skill that can roll it
at all. An order of magnitude smaller than the other three flags, and
those 21 slots span many unrelated identities, not just this family --
zero hits here is exactly what ordinary rarity on a narrow single-skill
support predicts. Adding it would have diluted the group's own
evidentiary bar. Stays an ordinary, uncaught coverage gap instead.

Also cleaned up `generate_support_coverage_report.py`'s output: the
"Suspected non-live" heading now prints once with each group's reason
underneath, instead of repeating the identical H3 heading once per
group (harmless at 1 group, looked broken at 3).

## Spell Cascade (III) and Greater Sacred Wisps added to suspected-nonlive; coverage report's tier ambiguity fixed

Two more real findings, both stronger evidence than Mitigation Ignore's
original benchmark:

- **Spell Cascade (III)**: this family has only two tiers -- Lesser (I)
  and, oddly, an unprefixed "Spell Cascade" at III with no II at all.
  Lesser Spell Cascade is proven real many times over (`ground_truth`)
  and shows on trade; Spell Cascade (III) shows on neither trade nor
  any of the 79 distinct crops (294 total occurrences) captured from
  its 9 possible skills.
- **Greater Sacred Wisps (III)**: zero on trade, zero across 45 distinct
  crops (150 total occurrences, 100% `ground_truth`) from its 3
  possible skills.

Both added to `support_icon_coverage_notes.json`. Notably, these are
Tier III cases, not Tier I -- meaning the low-level trade-visibility
caveat from the taxonomy above doesn't apply and can't explain the
trade absence away. That makes the trade signal here more trustworthy
than it was for the Tier I flags, not less -- Tier III items normally
show up on trade without issue (confirmed: Greater Leech, Greater Rage
on Hit, Greater Pierce all do), so a genuine Tier III absence is real
information.

Also fixed a real, user-caught ambiguity in
`generate_support_coverage_report.py`: `support_icon_coverage.md`
displayed a bare name like "Spell Cascade" with no tier annotation,
which reads as if it might be the family's Tier II member (the usual
convention for an unprefixed name) when it's actually Tier III -- true
for several families with non-standard naming (Spell Cascade, Second
Wind, Knockback, Multiple Projectiles, Combustion...). Every row now
explicitly shows "(Tier X)" regardless of whether the name's own
Lesser/Greater prefix already implies it. Required carrying the raw
`names` list alongside the display label through the row tuples, since
the suspected-nonlive matching logic used to parse tier-free names back
out of the display string -- would have silently broken once tier
suffixes were added to the label. Validated: report regenerates
correctly with all entries tier-labeled, suspected-nonlive matching
still correctly excludes the right entries from the hunting table, full
test suite has no regressions.

## Lesser Rage on Hit: the "suspected non-live" flag directly overturned by new evidence

The exact scenario the campaign audit pipeline exists for: a
classification made in good faith on real evidence, reversed cleanly
once better evidence arrived, with no drama and no need to distrust the
process that made the original call.

Capture `captures_campaign/20260919_053448` -- "Hoko, the Pit-fighter,"
Build: Ripper, level 49 -- the FIRST Ripper ever seen in any of this
project's 82 campaign sessions (Ripper had been 0/81 up to this point,
despite being common in the real high-level corpus; see the earlier
"notable omissions" investigation). It rolled `Chain Hook of Trarthus`
with three supports: Lesser Brutality, **Lesser Rage on Hit**, and
Greater Faster Attacks. User tooltip-confirmed all three directly
through the campaign review UI, independent of any icon-matching.

`Lesser Rage on Hit` was flagged suspected-nonlive a few turns earlier
on real, honestly-gathered evidence (70 real occurrences across its 5
possible skills, zero hits) -- a fair call at the time, just overtaken
by the very next relevant encounter. Removed from
`support_icon_coverage_notes.json` and promoted into the production
catalog (`e06008f740...`). Validated the full chain:

- Coverage: 147/159 -> **148/159**
- Campaign audit pool: 305 -> 304 (promoted hash dropped out), still
  **302/302 scorable agree**
- **Tier I audit coverage reached 45/45 -- every single Tier I key in
  the production catalog is now independently confirmed via the
  campaign UI**, not just self-resolved by the classifier. That number
  started at 17/40 a number of turns ago.
- Reference embeddings: 148 keys
- Classifier validation: still 100% (2094/2094)
- Full test suite: no regressions

Practical lesson for how "suspected non-live" should be read going
forward: it is explicitly a snapshot of current evidence, not a
permanent verdict -- exactly as the flag's own reasoning text always
said ("not proven impossible, just strongly suspicious"). This is the
first time one of these flags got overturned instead of reinforced; the
mechanism worked exactly as designed.

## Full real-warrant.txt-vs-regenerated audit: 5 real skill-name bugs found and fixed

User's request: regenerate `warrant_generated.txt` (skills + supports) for
every real capture that has a genuine `warrant.txt`, and validate the
freshly-regenerated text against that ground truth directly -- something
that had never been done systematically before. This mattered because
most real-warrant captures never had a `warrant_generated.txt` saved at
capture time at all (the live pipeline apparently skips OCR generation
once a real warrant is pasted), so this was the FIRST time
`process_capture()`'s current skill/support extraction code was ever
checked against the full 146-capture real-ground-truth corpus at once.

**Method**: reconstructed each capture's `crops` dict from its saved
region PNGs (name/type_subtype/level/skills/supports -- rucksack
quadrants not needed for warrant text), ran `process_capture()` +
`build_warrant_extracted_text()` fresh, parsed both the real and fresh
text with `harvest_support_icons.parse_warrant_ground_truth()`, and
diffed the structured (skill, [(support, tier)]) blocks directly rather
than raw text (avoids false positives from cosmetic differences like a
missing Mercenary Level line on older captures with no `level.png`).

**Triage came first, before any fix**, per the user's explicit
instruction to add failures to the test suite before fixing anything --
which also meant not treating everything that diffed as a bug. Of 46
initial mismatches, 40 were the pipeline correctly reporting a genuine,
already-proven same-skill collision as an honest "X or Y" string (see
`assets/support_icon_collisions.md`'s own "Collisions within a single
skill" table) -- exactly the intended, honest behavior of
`resolve_support_name()`'s "don't guess" policy, not something to
"fix." A programmatic classifier (does every real support in the row
appear as either an exact match or as a member of an unresolved
ambiguity that the row's own skill can genuinely roll?) confirmed this
cleanly, leaving 6 genuinely unexplained mismatches, which resolved
into 3 real, distinct bug categories plus one data artifact:

1. **Exact-prefix vs ratio() length bias** (`match_skill_name`). A skill
   name that visually wraps to two on-screen lines produces a first OCR
   line that's a literal, complete prefix of the true full name (e.g.
   "Leap Slam of" for "Leap Slam of Groundbreaking"). `difflib.ratio()`
   rewards raw character overlap regardless of length, so when the SAME
   mercenary also has the shorter "Leap Slam" as an independent real
   skill, "leap slam of" scored higher against "leap slam" (0.857) than
   against the correct "leap slam of groundbreaking" (0.615) -- pure
   ratio() got it decisively wrong, not just narrowly. Separately,
   heavy icon-bleed garbling (`"GLACIAL HAMMER"` OCR'd as `"dl GLACIAL
   Eee"`) landed ratio() in a genuine near-tie between the correct name
   and an unrelated longer one ("Glacial Hammer" 0.643 vs "Vaal Glacial
   Hammer" 0.667).
2. **Adjacent wrap-line duplication.** Once (1) correctly resolves the
   wrapped first line to the full name, the bare continuation line
   ("Groundbreaking") ALSO independently fuzzy-matches that same full
   name on its own -- producing two rows for one real skill and
   shifting every later row's alignment by one.
3. **Tesseract block-order scrambling** (`_extract_skill_names_pass`).
   Every individual line OCR'd and fuzzy-matched correctly on two real
   captures, but `image_to_string`'s raw text stream put one skill's
   line dead last instead of its true 4th-of-6 on-screen position --
   Tesseract's own internal block-traversal order isn't always true
   visual order, and nothing previously checked for that (pass-picking
   only ever scored "how many names matched," never whether the order
   was right).
4. **Data artifact, not a bug**: one capture's `warrant.txt` is a
   genuine 0-byte empty file (a failed clipboard paste at capture time)
   -- nothing to compare against, excluded from the audit rather than
   forced into either bucket.

**Fixes**, in `capture_pipeline.py`:
- `match_skill_name`: exact-match check first (a real, valid, standalone
  skill can itself be a strict prefix of a different real skill's name
  -- "Leap Slam" vs "Leap Slam of Groundbreaking" -- so this must win
  before any prefix reasoning even runs); then exact-prefix preference,
  gated to candidates at least as long as the shortest real name in the
  pool (a 2-character icon-bleed token like "tr" is trivially a
  "prefix" of "Triggerblades" by pure chance -- a real regression this
  length floor was added specifically to catch, found by testing
  against the FULL corpus, not just the fixtures being fixed); then,
  for a near-tie between two candidates where one is exactly the other
  plus one or more extra LEADING words (the common "Vaal X" pattern),
  prefer whichever is consistent with whether that extra word actually
  appears anywhere in the raw candidate text.
- `extract_skill_names`: collapse an immediately-adjacent duplicate
  skill name (a mercenary's skill list never contains the same name
  twice, unlike supports, so this is always safe).
- `_extract_skill_names_pass`: switched from `image_to_string` to
  `image_to_data`, grouping words into lines by Tesseract's own
  (block, paragraph, line) triple and explicitly sorting the resulting
  lines by pixel `top` position -- recovers genuine visual order
  regardless of which order Tesseract's internal segmentation happened
  to traverse blocks in.

**A real near-miss worth recording**: the first version of the
near-tie fix used a cruder "strip 0 or 1 leading candidate words, check
which candidate's first word matches" heuristic instead of the
extra-word-presence check above. Tested against ONLY the two known
bugs it looked fine, but a full corpus re-run surfaced 5 BRAND NEW
mismatches it hadn't existed before -- "tr" (icon-bleed noise) matching
"Triggerblades" via the ungated prefix check, and "Burning Arrow"
refusing to resolve against "Vaal Burning Arrow" because the cruder
heuristic couldn't handle two leading noise words at once. Caught only
because the fix was re-validated against the FULL 146-capture corpus,
not just the fixtures it was written to fix -- exactly the discipline
this project has followed everywhere else, and exactly why it mattered
here too.

**Final validation**: all 13 `SKILL_NAME_EXTRACTION_CASES` fixtures
passing (5 new ones added first, confirmed failing, then fixed -- see
their entries and comments in `tests.py`), full `tests.py` suite no
regressions, and the full 146-capture real-warrant re-validation:
46 -> 42 mismatches, zero new ones introduced, and every one of the
remaining 42 is either the same pre-existing, already-documented
same-skill-collision pattern (41) or the one genuinely uncomparable
empty-`warrant.txt` capture (1).

## Name extraction: apostrophe silently split "Ven'zi Kaldri" into two fragments, kept the wrong one

Real capture `captures/20260921_111909` had its name OCR'd as just
"zi Kaldri" -- the real name is "Ven'zi Kaldri". Root cause was in
`extract_text()`'s `_WORD_RUN_RE` (`[A-Za-z][A-Za-z,.\- ]*[A-Za-z]`):
apostrophe was never in the allowed mid-run character set, so any name
containing one split into two separate regex matches at that point.
`extract_text()` then does `max(matches, key=len)` to throw away
leading/trailing OCR noise -- fine when the noise actually is shorter
than the real text, but here the real text ITSELF got split, and the
second half ("zi Kaldri", 9 chars) was longer than the first ("Ven",
3 chars), so the longest-run heuristic threw away the correct half.

Confirmed the exact raw OCR on the real crop:
- `--psm 11`: `* Ven\ufffdzi Kaldri` -- Tesseract misread the apostrophe
  glyph as the Unicode replacement character, not a real apostrophe.
- `--psm 7`: `ecge * Ven'zi Kaldri 2,29` -- read the apostrophe correctly
  as `'` this time, but `_WORD_RUN_RE` still didn't allow it mid-run, so
  this pass would have failed identically had psm 11 not already
  short-circuited the loop (matches non-empty -> break before psm 7 even
  runs). Not a psm-11-vs-7 issue at all; the regex was the whole problem.

Fixed by extending `_WORD_RUN_RE` to allow apostrophe, both curly-quote
variants (`\u2018`/`\u2019`), and `\ufffd` inside a run (still anchored to
start/end on `[A-Za-z]`, so a leading/trailing stray apostrophe-like
glyph is still stripped -- only an INTERNAL one now survives), then
normalizing all three to a plain `'` in the final string. Skill names
already had this exact normalization for curly quotes via
`_CURLY_QUOTES_RE` (`match_skill_name`'s pool match) -- names never had
the equivalent until now, and apostrophes are a real, established
character in this game's proper nouns and skill names (`Battlemage's
Cry`, `Poacher's Mark`, etc. per `definitions/skills_by_mercenary.json`),
not a corruption to filter out.

Added `test_data/mercenary_names/ven_zi_kaldri.png` (the real crop) and
a `("Ven'zi Kaldri", False)` case to `MERCENARY_NAME_CASES` in
`tests.py`. Confirmed failing before the fix (returned "zi Kaldri"),
passing after (21/21 mercenary-name tests), full suite re-run afterward
with no regressions.

## Name extraction: second leading-noise bug, distinct from the apostrophe one -- "ae Ruktara, the Unrelenting"

Real capture `captures/20260922_125511` (a non-Infamous Flamequiver)
extracted its name as "ae Ruktara, the Unrelenting" -- real name (per
warrant.txt) is "Ruktara, the Unrelenting". Raw OCR:
- `--psm 11`: `ae Ruktara, the Unrelenting` -- background-art texture
  at the crop's left edge (visible directly in `_isolate_text_row`'s
  output as speckled noise, confirmed by eye) got read as its own short
  lowercase word and landed space-joined onto the real name.
- `--psm 7`: `=~" Ruktara, the Unrelenting ©.` -- correct on this crop,
  but never reached since psm 11 already produced a non-empty match and
  the loop breaks on first success.

This is NOT the same bug as the "Ven'zi Kaldri" apostrophe fix earlier
today: there, the real name text itself got split into two fragments by
a mid-name character the regex didn't allow, and the longest-run
heuristic kept the wrong half. Here, the real name was never split --
noise and name were glued into ONE continuous, space-joined
`_WORD_RUN_RE` match from the start, so "keep the longest run" can't
separate them; the noise IS part of the only (and thus longest) run.

Also confirmed this doesn't reuse `_STRAY_ICON_PREFIX_RE` (the existing
"Infamous" icon-bleed fix) -- that regex only strips a stray leading
letter immediately before the literal word "Infamous", and this
mercenary isn't Infamous at all, so it never matched here (that call
was a no-op on this crop both before and after this fix).

Fixed with a new, separate `_STRAY_NAME_PREFIX_RE = r"^[a-z]{1,3}\s+(?=[A-Z])"`,
applied only in `extract_text()`. Justified generalizing beyond "exactly
this one observed noise word" because a real mercenary name can never
legitimately start with a lowercase word (always a capitalized proper
name) -- so stripping any short all-lowercase leading word here has zero
risk of clipping real content, unlike the apostrophe fix which had to
stay narrowly scoped to specific characters.

Added `test_data/mercenary_names/ruktara_the_unrelenting.png` (the real
crop) and a `("Ruktara, the Unrelenting", False)` case to
`MERCENARY_NAME_CASES`. Confirmed failing before the fix (returned "ae
Ruktara, the Unrelenting"), passing after (22/22 mercenary-name tests),
full suite re-run afterward with no regressions.

## Name extraction: trailing mirror of the "ae Ruktara" bug -- "oe Pradin Prowl-linger af"

Real capture `captures/20260922_132138` extracted its name as "oe Pradin
Prowl-linger af" (before today's prefix fix was applied to this
specific capture on disk) -- true name (warrant.txt) is "Pradin
Prowl-linger". Same root cause as yesterday's `_STRAY_NAME_PREFIX_RE`
fix, just on the opposite edge: `_isolate_text_row`'s output shows the
same speckled background-art noise on BOTH the left and right edges of
this crop (visually confirmed), and --psm 11 read the right-edge noise
as its own short trailing lowercase word ("af"), space-joined onto the
end of the real name inside the same `_WORD_RUN_RE` match.
`_STRAY_NAME_PREFIX_RE` already fixed the leading "oe " (this capture's
extract_text() result had already improved to "Pradin Prowl-linger af"
before this fix, from "oe Pradin Prowl-linger af"), but nothing existed
yet for a trailing stray word.

Fixed with the natural mirror, `_STRAY_NAME_SUFFIX_RE =
r"(?<=[A-Za-z])\s+[a-z]{1,3}$"`, same justification as the prefix
version: no real mercenary name has ever been observed ending in a
lowercase word (every "Name, the Title" and hyphenated-surname case
ends capitalized), so stripping one here has no risk of clipping real
content. Verified against all 22 then-existing MERCENARY_NAME_CASES
fixtures before adding -- none changed.

Added `test_data/mercenary_names/pradin_prowl_linger.png` (the real
crop) and a `("Pradin Prowl-linger", False)` case. Confirmed failing
before the fix (returned "Pradin Prowl-linger af"), passing after
(23/23 mercenary-name tests), full suite re-run afterward with no
regressions.

Also filled in 2 of the 3 remaining gem test fixtures this session
identified as pending: Dark Bargain of Trarthus (from
captures/20260921_103832/rucksack_top_left.png, Cruel Mistress) and
Chain Hook of Trarthus (from
captures/20260922_130659/rucksack_top_right.png, Ripper) -- both copied
into assets/gems/<slug>.png AND test_data/gems/<slug>.png (confirmed
this double-copy, byte-identical, is the established pattern for every
existing gem fixture, not just assumed). GEMS: 9/12 -> 11/12 passing.
Only Heavy Strike of Trarthus (Winter Deacon) remains unseen.

## Gem coverage complete: 12/12, and a self-caught process miss along the way

When asked "what gem assets are missing?" earlier this session, I
answered by cross-referencing GEM_CASES against assets/gems/ + the two
captures already fresh in this conversation's context (today's Dark
Bargain and Chain Hook sightings). That answer was incomplete: the user
had already flagged `captures/20260920_214634` as containing a gem two
days earlier in this same session, and I never went back to check it
against the pending list -- I only reasoned from what was immediately
in front of me instead of actually checking every un-harvested real
capture. The user caught this by asking directly why that capture
hadn't been used.

Checked: `captures/20260920_214634` is mercenary_type Winter Deacon
(Fredrik Torn, level 74), gem present in rucksack_top_right -- exactly
Heavy Strike of Trarthus per gems_by_mercenary.json, the one gem
GEM_CASES still listed as pending after the Dark Bargain/Chain Hook
harvest. Copied into assets/gems/heavy_strike_of_trarthus.png and
test_data/gems/heavy_strike_of_trarthus.png (same double-copy pattern
as every other gem fixture). GEMS: 11/12 -> 12/12 passing -- full gem
coverage across all 12 known types, full suite re-run with no
regressions.

Lesson: when auditing "what's missing" against a real corpus, check the
FULL corpus (or at minimum everything mentioned anywhere in the current
session), not just whatever's most recently top-of-mind -- the same
category of mistake as the near-miss documented earlier where a fix
was validated only against its own fixtures instead of the full
146-capture set.

## Re-ran the full real-warrant audit against the grown corpus: 168 captures, 50 mismatches, all explained

Corpus grew from 146 to 168 real-warrant captures since the last full
audit (22 new captures gained a warrant.txt; separately, the old empty-
warrant.txt artifact at `__captures_20260915/20260914_092535` no longer
exists on disk at all -- its warrant.txt was removed since then, so
it's no longer counted either way). Re-ran `/tmp/validate_warrants.py`
fresh (same script as the original audit) rather than assuming the
archives were still clean, since several skill/name-extraction fixes
landed since the last run.

Result: 50 mismatches now (up from 42). Triaging these turned into a
self-caught false alarm: my first triage script assumed
`resolve_support_name()`'s "X (Tier: N) or Y (Tier: N)" format always
has exactly 2 members with a tier on each, and its regex broke on the
real 3-way collisions (e.g. "Arcane Traps (Tier: 3) or Greater Throwing
Speed (Tier: 3) or Greater Trigger Radius (Tier: 3)") and, worse, on
`harvest_support_icons.parse_warrant_ground_truth()`'s own quirk of
absorbing an embedded "(Tier: N)" into the "name" portion when a line
has more than one tier annotation -- neither of which is a
capture_pipeline.py bug, both are just my ad hoc triage script being
too strict. Rewrote it to parse warrant text directly by skill block
(splitting on "--------", matching support lines directly against
`definitions/supports_by_skills.json`'s `PossibleSupports` at the
correct roman-numeral tier) instead of trusting the already-lossy
parsed blocks. Result with the corrected triage: **50/50 mismatches
fully explained** as the same known same-skill icon+tier collision
category -- 0 new/unexplained bugs found in this pass, including
across all the new captures gathered this session (Ven'zi Kaldri,
Ruktara the Unrelenting, Pradin Prowl-linger, the Blast Rain/Chain
Hook/Dark Bargain gem sightings, etc.).

Lesson for next time: when a validation script itself changes (not
just the code under test), re-verify the SCRIPT against a known-correct
case before trusting a big jump in its reported failure count --
42->50 looked alarming at first glance but was entirely a comparison-
tooling artifact, not a regression.

## Name extraction: third edge-noise flavor -- a real UI badge, not background art -- "Alak Prowl-linger PORE"

Real capture `captures/20260922_171248` extracted its name as "Alak
Prowl-linger PORE" -- true name (warrant.txt) is "Alak Prowl-linger".
Visually inspected the crop this time before assuming it was the same
speckled-background-art category as the last two fixes: it wasn't --
there's an actual on-screen orange UI badge reading "ORB" sitting
inside the wide/generous name crop region, distinct in both color and
shape from background art noise. --psm 11 misread it as "PORE" and
space-joined it onto the name the same structural way as before (one
continuous _WORD_RUN_RE match, noise included). --psm 7 actually
separated it correctly this time ("Alak Prowl-linger" as its own
match), but never got a chance to run since psm 11 already produced a
match.

Existing `_STRAY_NAME_SUFFIX_RE` (lowercase-only, 1-3 chars) didn't
catch this: "PORE" is 4 letters and uppercase. Extended the regex with
a separate ALL-UPPERCASE alternative, `[A-Z]{1,6}`, rather than making
the lowercase class case-insensitive -- a real title can legitimately
end in a short Title-Case word (e.g. "the Azadin Howler"), so the
class must stay case-EXACT, not case-insensitive, for the safety
argument ("real name segments are never rendered fully uppercase") to
hold regardless of noise-token length. Verified against all 24
then-existing MERCENARY_NAME_CASES fixtures before adding -- none
affected.

No leading-edge (prefix) instance of an uppercase badge has been seen
yet -- left `_STRAY_NAME_PREFIX_RE` lowercase-only rather than
speculatively mirroring this into it too.

Added `test_data/mercenary_names/alak_prowl_linger.png` and an
`("Alak Prowl-linger", False)` case. Confirmed failing before the fix
(returned "Alak Prowl-linger PORE"), passing after (24/24
mercenary-name tests), full suite re-run afterward with no
regressions.

Also confirmed another already-covered gem sighting this session:
Bladefall of Trarthus (Bladecaster, captures/20260922_171248,
rucksack_bottom_left) -- no new fixture needed, gem coverage was
already 12/12.

## Name extraction: a fourth bug, but this one isn't safely fixable -- "Voltai re, the Ari stocrat"

Real capture `captures/20260922_173604` extracted "Voltai re, the Ari
stocrat" -- true name "Voltaire, the Aristocrat" (confirmed visually
against name.png). This is a genuinely different failure mode from the
three name bugs fixed earlier today: those were all extra noise GLUED
onto an otherwise-intact real name at one edge (background art, or a
real UI badge). This one is --psm 11 inserting spurious spaces INSIDE
two real words themselves ("Voltaire" -> "Voltai re", "Aristocrat" ->
"Ari stocrat") -- confirmed via raw OCR: `--psm 11` gives '... Voltai
re, the Ari stocrat ...' directly, not a downstream artifact of
_WORD_RUN_RE or the edge-noise regexes.

Deliberately did NOT attempt a fix here, for a concrete reason rather
than just running out of ideas: every edge-noise fix so far worked
because "a real name never starts/ends in a short all-lowercase or
all-uppercase word" is a safe, checkable invariant. There's no
equivalent safe invariant for a MID-word split -- there's no dictionary
of valid mercenary given names to fuzzy-match against (unlike
match_skill_name's known-pool approach), and a naive "merge short
internal fragments back together" rule would wrongly corrupt real,
already-fixture-confirmed multi-word names: "Alo" (in "Dalshon Alo") is
3 letters, the same ballpark as the bogus "Ari" fragment here, so
fragment length alone can't distinguish a real two-word name from a
corrupted single word.

Also checked whether just preferring --psm 7 would fix it: it does, for
this ONE crop ('aan Voltaire, the Aristocrat' -- correct once the
existing "aan " prefix strip runs), since Tesseract's psm 7 word
segmentation didn't split these words. But extract_text()'s existing
psm-11-first order is itself backed by real, opposite evidence (the
"Eli, the Contemptible" fixture: two byte-identical name crops where
psm 11 deterministically returns NOTHING on one, and psm 7 recovers it
-- swapping the default order would need to preserve that case too, not
just fix this new one). One counterexample isn't enough to justify
reordering a decision that was itself made from real evidence; would
need more real mid-word-split captures before that trade is
provably a net win.

Tracked as a documented `known_issue=True` case instead --
`test_data/mercenary_names/voltaire_the_aristocrat.png` +
`("Voltaire, the Aristocrat", True)` in MERCENARY_NAME_CASES, using the
test harness's own existing known-issue bucket (already built for
exactly this purpose) rather than leaving it silently unrecorded. Full
suite confirms it's tracked as "known issue, still failing" -- not
counted as a regression, and will surface loudly if a future change
happens to fix it (the "known_issue_now_passing" bucket).

## Filled in the last pending quadrant: top_right, with an honest match_icon failure

User asked directly whether we'd seen a top_right gem this session,
since test_data/gems/quadrants/ was still missing top_right.png. Yes --
checked, and it came up repeatedly: Blast Rain of Trarthus (x2, Ven'zi
Kaldri's/Ruktara Kryssik's Flamequivers), Chain Hook of Trarthus
(Ripper), Heavy Strike of Trarthus (Winter Deacon), Storm Call of
Trarthus (Sanguimancer) -- all landed in rucksack_top_right at some
point this session, it just hadn't been backfilled into QUADRANT_CASES.

Deliberately did NOT reuse captures/20260922_130659 (Chain Hook) or
captures/20260920_214634 (Heavy Strike) for this, even though both are
confirmed real top_right sightings -- both are ALSO already
assets/gems/<slug>.png's own canonical reference image (copied
verbatim earlier this session), so testing match_icon against either
would just be matching an image against itself, not a real check.
Used captures/20260921_111400 (Blast Rain) instead, an independent
capture never used as any reference.

Result is an honest failure: match_icon returns None on it, not
'blast_rain_of_trarthus'. Checked this isn't specific to this one crop
-- also tried captures/20260922_125511 (the other independent Blast
Rain top_right) and captures/20260922_140752 (independent Storm Call
top_right): both ALSO fail to match_icon, while the two crops that
happen to already BE their own reference trivially "pass" (self-match).
This is the same already-documented match_icon unreliability from
earlier work (assets/README.md: "unverified... same problem domain as
gems"), not a new bug -- and quadrant/gem match_icon failures are
already bucketed as non-blocking since production identity resolution
uses the type->gem lookup, never match_icon. QUADRANTS: 2/4 -> now 4/4
with real data (2 pass, 2 honest fail, 0 pending); aggregate "match_icon
identity-matching suites" failure count correctly moved 1 -> 2. Full
suite still reports no regressions.

## Backfilled stale gem variance folders with today's independent sightings

User spotted that test_data/gems/variance/bladefall_of_trarthus/ only
had one sample from 20260910, despite a fresh independent Bladefall
sighting captured just today (captures/20260922_171248, Bladecaster,
rucksack_bottom_left). Checked whether this was an isolated miss or a
broader pattern: audited all 8 variance/<gem>/ folders against every
gem sighting logged this session. Found two more stale ones --
blast_rain_of_trarthus (last sample 20260915, missing both of today's
independent Flamequiver sightings, captures/20260921_111400 and
captures/20260922_125511) and sunder_of_trarthus (last sample
20260915, missing captures/20260922_172451's Earthshaker sighting).
siege_ballista_of_trarthus, spectral_helix/spectral_throw/
spectral_shield_throw_of_trarthus, and wave_of_conviction_of_trarthus
had no new sightings this session (no Sniper/Blade Ambusher/Bastion/
Flaming Charlatan captures), so left those alone rather than touching
folders with nothing new to add.

Copied all 4 new samples in, timestamp-named per the folder's own
convention. Re-ran the one test actually wired to any of these
(BLADEFALL vs SPECTRAL THROW COLLISION WATCH, which reads
variance/bladefall_of_trarthus/): now measures against 3 Bladefall
samples instead of 2. The worst-pair margin didn't change (still the
same Throw-vs-Bladefall pair, margin=0.0557) -- the new sample didn't
surface a worse collision, just broadened the real-sample base backing
that measurement. blast_rain_of_trarthus and sunder_of_trarthus aren't
wired into any test (per test_data/README.md's own caveat), so those
two additions are pure archival groundwork with zero effect on test
output, consistent with the README's explicit guidance: "Add more here
as real captures turn up rather than letting them depend on an archive
folder sticking around." Full suite re-run with no regressions.
