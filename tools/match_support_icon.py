"""
Part 2 of wiring in support identification (see harvest_support_icons.py's
own module docstring, and AI_RAMBLINGS.md's "Warrant-text alignment for
skill/support ground truth"): an experimental image-based classifier for
support icons, built against the reference catalog harvest_support_icons.py
produces. NOT wired into capture_pipeline.py -- this is a standalone
report/validation tool only, same "review before promoting" caution
assets/README.md already applies to every extracted-vs-captured asset
category.

Uses the same frozen ResNet18 embedding backbone already validated for
gem presence (capture_pipeline._embed_crop / _load_gem_embedding_session)
-- it's a generic ImageNet feature extractor, not gem-specific, so
reusing it here needs no new model file or dependency.

Classifies at the (icon, tier) level ("visual_key"), not the individual
support NAME -- that's the actual decidable target for an image method.
Several real supports render pixel-identical at the same tier by design
(see assets/support_icon_coverage.md's ambiguous-key notes); no image
method, this one included, can ever tell those apart, so getting the
visual_key right is the correct and complete goal -- a "miss" against
one specific name in an ambiguous group is checked and reported
separately from a genuine miss.

Two-stage classification: the whole-crop embedding picks the icon
family (validated at 100% across every real crop on hand), then
`resolve_tier_from_badge()` tries to read the tier directly off the
roman-numeral badge in a fixed corner of the crop (bar-counting on the
badge's own gold color, not embeddings) and overrides the embedding's
tier guess when it gets a confident reading. This exists because the
whole-crop embedding alone is only 88.3% accurate on exact (icon,
tier) despite 100% icon accuracy -- global average pooling over a
frozen backbone isn't very sensitive to a small, spatially localized
badge, which is the ONE thing tier resolution depends on.

The badge check now resolves every real case on hand: 100% exact
(icon+tier) accuracy across all 1,971 real validation crops (230
disagreements with the embedding, all 230 correct, 0 wrong). Getting
there took two real bug fixes in `resolve_tier_from_badge()` -- see its
own docstring -- found by tracing actual failures down to their pixel
masks rather than guessing at a better threshold. It's still applied
as a second opinion (only overrides when confident) rather than a full
replacement of the embedding's own tier guess, since "100% on every
real crop seen so far" is not the same claim as "provably always
correct" -- a genuinely new icon's art could in principle trip the
same false-positive class poison's fang trim did before it was fixed.

Validates against real captures' `warrant.txt` ground truth (never the
files used to BUILD the reference catalog in the abstract -- each
validation crop is freshly re-cropped from its own capture's
supports.png, independent of which capture happened to become the
reference), same row-occupancy guard as the harvester itself.

Usage:
    python match_support_icon.py

Must run from the project root (imports capture_pipeline, which resolves
its own asset paths relative to cwd).

Reads: assets/harvested_supports/ (reference catalog + manifest.json for
    occurrence counts), definitions/supports.json, captures/, __captures_*/
    (validation crops via real warrant.txt)
Writes: nothing -- report to stdout only
"""
import json
import os
import sys

import numpy as np
from PIL import Image

PROJECT_ROOT = os.getcwd()
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, TOOLS_DIR)

import capture_pipeline as cp
import harvest_support_icons as harvester

HARVESTED_DIR = os.path.join(PROJECT_ROOT, "assets", "harvested_supports")
SUPPORTS_PATH = os.path.join(PROJECT_ROOT, "definitions", "supports.json")


def load_reference_embeddings(session):
    """One embedding per top-level assets/harvested_supports/<visual_key>.png
    file -- the harvester's primary, deduplicated reference per (icon,
    tier). Deliberately NOT the variants/ subfolders, which hold near-
    duplicate noise of the SAME identity, not additional identities."""
    refs = {}
    for fname in sorted(os.listdir(HARVESTED_DIR)):
        if not fname.lower().endswith(".png"):
            continue
        visual_key = os.path.splitext(fname)[0]
        if visual_key.startswith("unlabeled_"):
            continue  # a genuinely unresolved cluster (no ground truth, no tier) -- not a real
            # (icon, tier) identity, so it's not a valid target; including it just adds a
            # confusable non-answer a real crop could wrongly land on
        crop = Image.open(os.path.join(HARVESTED_DIR, fname))
        refs[visual_key] = cp._embed_crop(crop, session)
    return refs


# The roman-numeral tier badge sits in a fixed corner of every support
# crop regardless of icon content -- confirmed directly by averaging
# many same-tier crops of the same icon to cancel out per-capture noise,
# then diffing those averages across tiers of 4 different icon families:
# the only region that differs by more than the natural same-tier noise
# floor is consistently y=[0.55, 1.0], x=[0.35, 1.0] of the crop (bottom-
# right). This is genuinely fixed-position, not icon-dependent.
_BADGE_Y_FRAC = (0.55, 1.0)
_BADGE_X_FRAC = (0.35, 1.0)
_TIER_FOR_BAR_COUNT = {1: "i", 2: "ii", 3: "iii"}


# How tall a column's longest unbroken run of badge-gold pixels must be,
# as a fraction of the badge ROI's height, to count as part of a real
# bar. 0.45 (a first guess) turned out too strict -- validated directly
# against a real failure (addedcolddamage_iii's middle bar has a run of
# 9 rows against a 22-row ROI, 40.9%, just under 0.45 -- a false
# negative, not noise). Swept 0.25-0.45 against the full 915-crop real
# corpus; 0.32-0.40 is a flat, stable plateau (99.5% correct either
# way), so 0.35 sits with real margin on both sides rather than right at
# either edge.
_BADGE_BAR_HEIGHT_FRAC = 0.35


# A real bar is a flat-filled UI overlay (near-constant color top to
# bottom); the one confirmed false positive (see below) is gradient-
# shaded icon art rendered in 3D, which drifts in color along its
# length even where it happens to be gold and bar-shaped. Trimming this
# many pixels off each end of a qualifying run before checking color
# uniformity excludes the anti-aliased edge transition (which blends
# toward the background and would otherwise look like drift on a real
# flat bar too), leaving only the bar's solid interior to judge.
_BADGE_CORE_TRIM = 2
# Combined per-channel std-dev (R+G+B) within a run's trimmed core, above
# which it's judged gradient-shaded art rather than a flat badge bar.
# Validated directly: real bar cores measured 0.0-1.7, the one confirmed
# false positive (poison's fang/vial trim) measured 31.7-56.6 -- a wide,
# clean gap, not a close call.
_BADGE_CORE_STD_MAX = 10.0


def _longest_true_run(column) -> tuple:
    """Returns (start_index, length) of the longest unbroken run of True
    values, so the caller can inspect the run's own pixels, not just its
    length."""
    best_start = best_len = current_start = current_len = 0
    for i, value in enumerate(column):
        if value:
            if current_len == 0:
                current_start = i
            current_len += 1
            if current_len > best_len:
                best_start, best_len = current_start, current_len
        else:
            current_len = 0
    return best_start, best_len


def resolve_tier_from_badge(crop: Image.Image):
    """Attempts to read the tier directly off the roman-numeral badge by
    counting its vertical gold bars (I/II/III), instead of asking the
    whole-crop embedding to notice a small corner detail it isn't very
    sensitive to (see this module's own validated finding: 100% icon
    accuracy, 88.3% exact tier accuracy, ALL misses same-icon-wrong-tier).

    Thresholds on the badge's actual rendered color (sampled directly
    from real crops: ~RGB(240, 205, 135), gold/tan, clearly separated
    from both the dark crop border and every icon's own art by having R
    much greater than B) within the fixed badge region, then finds each
    COLUMN's longest unbroken run of that color and counts how many
    separate groups of adjacent qualifying columns there are.

    Two real bugs found and fixed by tracing actual failures down to
    their pixel masks, not by guessing:

    1. An earlier connected-component version (2D bar-shaped blobs, not
       per-column) only reached 75.2% standalone accuracy -- a real bar
       would sometimes touch an unrelated gold pixel from the icon's
       OWN art (diagonally adjacent, or bridged by a dilation step
       meant to patch anti-aliasing gaps within one bar), merging into
       one oversized blob that then failed a width check and silently
       vanished. Per-column run-length has no such cross-column
       contamination -- a stray gold pixel in some other column can't
       extend a real bar's column's own run.
    2. That alone still left one icon (`poison`) misreading its tier
       100% of the time: its own art has a tall, narrow, gold-TRIMMED
       fang/vial shape that clears the same run-length threshold as a
       real bar. The fix isn't a positional exclusion (checked --
       other icons' real leftmost bar sits at almost the same
       horizontal position the fang occupies, so narrowing the region
       would trade this failure for a new one elsewhere). The real
       differentiator, found by sampling actual pixels: a badge bar is
       a FLAT UI overlay (near-constant color along its whole length),
       while the fang trim is gradient-shaded 3D icon art (drifts
       noticeably in color along its length) -- so a qualifying run's
       trimmed core (see _BADGE_CORE_TRIM/_BADGE_CORE_STD_MAX) also has
       to be near-uniform in color, not just tall and gold, to count.

    Standalone accuracy across the full 915-crop real corpus (every
    harvested primary reference plus every variant sample) with both
    fixes: 100% (915/915) -- confirmed against the exact real captures
    that broke earlier versions, not just re-measured in aggregate.
    """
    arr = np.array(crop.convert("RGB")).astype(int)
    h, w, _ = arr.shape
    y0, y1 = int(h * _BADGE_Y_FRAC[0]), int(h * _BADGE_Y_FRAC[1])
    x0, x1 = int(w * _BADGE_X_FRAC[0]), int(w * _BADGE_X_FRAC[1])
    roi = arr[y0:y1, x0:x1]
    r, g, b = roi[..., 0], roi[..., 1], roi[..., 2]
    mask = (r > 170) & (r > b + 40) & (g > b + 15)
    roi_h = y1 - y0
    threshold = roi_h * _BADGE_BAR_HEIGHT_FRAC

    qualifying_columns = []
    for x in range(mask.shape[1]):
        start, length = _longest_true_run(mask[:, x])
        if length < threshold:
            qualifying_columns.append(False)
            continue
        if length <= _BADGE_CORE_TRIM * 2:
            qualifying_columns.append(True)  # too short to trim a core -- trust run-length alone
            continue
        core = roi[start + _BADGE_CORE_TRIM:start + length - _BADGE_CORE_TRIM, x]
        qualifying_columns.append(core.std(axis=0).sum() <= _BADGE_CORE_STD_MAX)

    bar_count = 0
    previous_qualified = False
    for qualified in qualifying_columns:
        if qualified and not previous_qualified:
            bar_count += 1
        previous_qualified = qualified
    return _TIER_FOR_BAR_COUNT.get(bar_count)


def classify(crop, session, refs):
    vec = cp._embed_crop(crop, session)
    scores = {vk: float(np.dot(vec, ref_vec)) for vk, ref_vec in refs.items()}
    best = max(scores, key=scores.get)

    badge_tier = resolve_tier_from_badge(crop)
    if badge_tier is not None:
        icon = best.rsplit("_", 1)[0]
        badge_key = f"{icon}_{badge_tier}"
        if badge_key in refs and badge_key != best:
            return badge_key, scores[best], best  # badge overrode the whole-crop guess
    return best, scores[best], None


def visual_key_for(support_name, supports):
    entry = supports.get(support_name)
    if entry is None:
        return None
    return f"{entry['icon']}_{entry['tier_roman'].lower()}"


def load_occurrence_counts():
    manifest_path = os.path.join(HARVESTED_DIR, "manifest.json")
    counts = {}
    if not os.path.isfile(manifest_path):
        return counts
    with open(manifest_path, encoding="utf-8") as f:
        for c in json.load(f)["clusters"]:
            vk = c.get("visual_key")
            if vk:
                counts[vk] = counts.get(vk, 0) + c.get("occurrence_count", 1)
    return counts


def load_ambiguous_groups(supports):
    """visual_key -> set of support display names that share it, for keys
    with more than one -- these are the ones no image method can ever
    tell apart, so a 'wrong' guess landing on another member of the same
    group isn't a real classifier failure."""
    groups = {}
    for name, entry in supports.items():
        key = f"{entry['icon']}_{entry['tier_roman'].lower()}"
        groups.setdefault(key, set()).add(name)
    return {k: v for k, v in groups.items() if len(v) > 1}


def main():
    session = cp._load_gem_embedding_session()
    if session is None:
        print(f"No embedding model found at {cp.GEM_EMBEDDING_MODEL_PATH} -- "
              f"see tools/export_gem_embedding_model.py.")
        return
    refs = load_reference_embeddings(session)
    print(f"Loaded {len(refs)} reference (icon, tier) embeddings from {HARVESTED_DIR}")

    with open(SUPPORTS_PATH, encoding="utf-8") as f:
        supports = json.load(f)["supports"]
    ambiguous_groups = load_ambiguous_groups(supports)
    occurrence_counts = load_occurrence_counts()

    columns, rows = harvester.load_grid()
    capture_dirs = harvester.find_capture_dirs()

    total = correct = icon_correct = 0
    multi_total = multi_correct = 0
    ambiguous_family_hits = 0
    badge_overrides = badge_overrides_helped = badge_overrides_hurt = 0
    wrong = []

    for capture_dir in capture_dirs:
        supports_png = os.path.join(capture_dir, "supports.png")
        warrant_path = os.path.join(capture_dir, "warrant.txt")
        if not os.path.isfile(supports_png) or not os.path.isfile(warrant_path):
            continue
        with open(warrant_path, encoding="utf-8") as f:
            blocks = harvester.parse_warrant_ground_truth(f.read())
        if not any(support_list for _, support_list in blocks):
            continue

        im = Image.open(supports_png).convert("RGB")
        arr = np.array(im).astype(float)
        brightness = arr.mean(axis=2)

        for row_idx, (skill_name, support_list) in enumerate(blocks):
            if row_idx >= len(rows):
                break
            ry0, ry1 = rows[row_idx]
            occupied_idx = [
                c for c, (cx0, cx1) in enumerate(columns)
                if brightness[ry0:ry1, cx0:cx1].std() >= harvester.OCCUPIED_STD_THRESHOLD
            ]
            if occupied_idx != list(range(len(support_list))):
                continue  # same row-mismatch guard as the harvester -- skip rather than mislabel

            for col_idx, (support_name, _tier) in enumerate(support_list):
                true_key = visual_key_for(support_name, supports)
                if true_key is None or true_key not in refs:
                    continue
                cx0, cx1 = columns[col_idx]
                crop = im.crop((cx0, ry0, cx1, ry1))
                guess, score, overridden_from = classify(crop, session, refs)

                if overridden_from is not None:
                    badge_overrides += 1
                    if guess == true_key and overridden_from != true_key:
                        badge_overrides_helped += 1
                    elif overridden_from == true_key and guess != true_key:
                        badge_overrides_hurt += 1

                total += 1
                is_correct = guess == true_key
                is_multi = occurrence_counts.get(true_key, 0) > 1
                if is_multi:
                    multi_total += 1
                if guess.rsplit("_", 1)[0] == true_key.rsplit("_", 1)[0]:
                    icon_correct += 1
                if is_correct:
                    correct += 1
                    if is_multi:
                        multi_correct += 1
                else:
                    same_family = guess in ambiguous_groups and support_name in ambiguous_groups.get(guess, set())
                    if same_family:
                        ambiguous_family_hits += 1
                    wrong.append((capture_dir, skill_name, support_name, true_key, guess, score, same_family))

    print()
    if total:
        print(f"Overall (icon + tier): {correct}/{total} correct ({100*correct/total:.1f}%)")
        print(f"Icon family only (tier ignored): {icon_correct}/{total} correct ({100*icon_correct/total:.1f}%)")
    else:
        print("No validation samples found.")
    if badge_overrides:
        net = badge_overrides_helped - badge_overrides_hurt
        print(f"Tier-badge secondary check fired {badge_overrides} times (disagreed with the "
              f"whole-crop guess): helped {badge_overrides_helped}, hurt {badge_overrides_hurt}, "
              f"net {'+' if net >= 0 else ''}{net}.")
    else:
        print("Tier-badge secondary check never disagreed with the whole-crop guess.")
    if multi_total:
        print(f"Keys seen in more than one real capture (the only genuinely independent "
              f"signal -- occurrence_count=1 keys trivially match their own sole instance): "
              f"{multi_correct}/{multi_total} correct ({100*multi_correct/multi_total:.1f}%)")
    else:
        print("No multi-occurrence keys to check independently yet.")
    if wrong:
        genuine = [w for w in wrong if not w[6]]
        print(f"\nMisclassifications: {len(wrong)} total, {ambiguous_family_hits} landed on a "
              f"KNOWN pixel-identical sibling (not a real failure -- see assets/"
              f"support_icon_coverage.md), {len(genuine)} genuine.")
        if genuine:
            same_icon_wrong_tier = sum(
                1 for *_, true_key, guess, _score, _sf in genuine
                if true_key.rsplit("_", 1)[0] == guess.rsplit("_", 1)[0]
            )
            print(f"Of those {len(genuine)} genuine misses, {same_icon_wrong_tier} guessed the "
                  f"RIGHT icon but WRONG tier (the roman-numeral badge is a small, spatially "
                  f"localized detail this embedding model -- global average pooling over a "
                  f"frozen backbone -- isn't very sensitive to); {len(genuine) - same_icon_wrong_tier} "
                  f"guessed a genuinely different icon.")
            print("\nGenuine misclassifications:")
            for capture_dir, skill_name, support_name, true_key, guess, score, _ in genuine:
                print(f"  {capture_dir}  {skill_name!r} -> {support_name!r} (true={true_key}) "
                      f"classified as {guess!r} (score={score:.4f})")


if __name__ == "__main__":
    main()
