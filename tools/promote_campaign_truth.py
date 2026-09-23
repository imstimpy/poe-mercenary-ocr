"""
Promotes a specific campaign shadow-ground-truth identification (see
tools/harvest_campaign_review.py) into the production support-icon
catalog under assets/harvested_supports/.

Why this is safe despite harvest_campaign_review.py being built
specifically to stay independent of that catalog (see its own
docstring): a campaign-review identification is a human reading the
exact name/tier straight off the real in-game tooltip, with candidates
anchored only to the contributing skill's real PossibleSupports pool --
zero icon-matching involvement anywhere. That's actually CLEANER
provenance than a manual_labels.json entry, whose candidate list is
narrowed using capture_pipeline.match_support_icon() first. Several
real (icon, tier) keys in assets/support_icon_coverage.md's missing
list may never show up in a warrant-backed capture at all -- that list
skews heavily toward Tier I ("Lesser") rolls precisely because those
are rarest to catch on camera at the level-68+ a warrant requires (see
AI_RAMBLINGS.md) -- so campaign play may be the only practical source
for some of them.

The one real risk: once a crop becomes a reference image, re-running
tools/audit_campaign_truth.py against that SAME crop would be circular
(checking whether the classifier matches an image against itself). This
script removes a promoted hash from manual_truth.json when it promotes
it -- not a loss, the identification's new home is
campaign_promotions.json -- so the audit pool only ever contains crops
that are still genuine out-of-sample checks.

Writes source="campaign_confirmed" in harvest_support_icons.py's
manifest -- distinct from "ground_truth" (a real warrant.txt) and
"manual" (narrowed via icon-matching first), always disclosed, never
silently merged into either.

Usage:
    python promote_campaign_truth.py <hash-or-prefix> [<hash-or-prefix> ...]

    A hash-or-prefix is anything that uniquely starts-with-matches a key
    in assets/campaign_review/manual_truth.json -- the full 32-char hash,
    or the 10-char prefix used in review.html's filenames / quoted back
    in chat (e.g. "744f10cdce").

Reads: assets/campaign_review/manual_truth.json,
    assets/campaign_review/crops/*.png (regenerate first with
    harvest_campaign_review.py if a crop file is missing),
    definitions/supports.json
Writes: assets/harvested_supports/campaign_promotions.json (hash -> exact
    literal supports.json name -- persistent and committed, unlike
    assets/campaign_review/crops/, which is gitignored and wiped every
    harvest_campaign_review.py run),
    assets/harvested_supports/campaign_promoted/<hash>.png (persistent
    copy of the crop),
    assets/campaign_review/manual_truth.json (removes each promoted hash
    -- see module docstring above for why)

Then re-run harvest_support_icons.py to fold the promotion into
manifest.json and the reference catalog (and export_support_reference_
embeddings.py / generate_support_coverage_report.py after that, same as
any other new capture).
"""
import json
import os
import shutil
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TOOLS_DIR)

CAMPAIGN_REVIEW_DIR = os.path.join(PROJECT_ROOT, "assets", "campaign_review")
TRUTH_PATH = os.path.join(CAMPAIGN_REVIEW_DIR, "manual_truth.json")
CROPS_DIR = os.path.join(CAMPAIGN_REVIEW_DIR, "crops")

HARVESTED_DIR = os.path.join(PROJECT_ROOT, "assets", "harvested_supports")
PROMOTIONS_PATH = os.path.join(HARVESTED_DIR, "campaign_promotions.json")
PROMOTED_CROPS_DIR = os.path.join(HARVESTED_DIR, "campaign_promoted")
SUPPORTS_PATH = os.path.join(PROJECT_ROOT, "definitions", "supports.json")


def _load_json(path, default):
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: python promote_campaign_truth.py <hash-or-prefix> [<hash-or-prefix> ...]")
        print(f"  (hashes/prefixes come from {TRUTH_PATH})")
        sys.exit(1)

    with open(SUPPORTS_PATH, encoding="utf-8") as f:
        supports = json.load(f)["supports"]
    truth = _load_json(TRUTH_PATH, {})
    promotions = _load_json(PROMOTIONS_PATH, {})

    os.makedirs(PROMOTED_CROPS_DIR, exist_ok=True)

    promoted = []
    for arg in args:
        matches = [h for h in truth if h.startswith(arg)]
        if not matches:
            print(f"  SKIP {arg!r}: no entry in {TRUTH_PATH} starts with this hash/prefix "
                  f"(already promoted? check {PROMOTIONS_PATH}).")
            continue
        if len(matches) > 1:
            print(f"  SKIP {arg!r}: ambiguous, matches {len(matches)} hashes {matches} -- use a longer prefix.")
            continue
        full_hash = matches[0]
        name = truth[full_hash]
        if name not in supports:
            print(f"  SKIP {full_hash}: manual_truth.json names {name!r}, but that's not a real "
                  f"definitions/supports.json key -- check for a stale/hand-edited entry.")
            continue
        crop_path = os.path.join(CROPS_DIR, f"{full_hash[:10]}.png")
        if not os.path.isfile(crop_path):
            print(f"  SKIP {full_hash}: no crop file at {crop_path} -- re-run "
                  f"harvest_campaign_review.py first to regenerate it.")
            continue
        if full_hash in promotions and promotions[full_hash] != name:
            print(f"  WARNING: {full_hash} was already promoted as {promotions[full_hash]!r} -- "
                  f"overwriting with {name!r}.")

        shutil.copyfile(crop_path, os.path.join(PROMOTED_CROPS_DIR, f"{full_hash}.png"))
        promotions[full_hash] = name
        del truth[full_hash]
        promoted.append((full_hash, name))

    if not promoted:
        print("Nothing promoted.")
        return

    with open(PROMOTIONS_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(promotions, f, indent=2, sort_keys=True)
    with open(TRUTH_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(truth, f, indent=2, sort_keys=True)

    print(f"Promoted {len(promoted)} crop(s) into {PROMOTIONS_PATH}:")
    for h, name in promoted:
        print(f"  {h[:10]}  ->  {name!r}")
    print(f"Removed from {TRUTH_PATH} -- no longer an independent audit check now that it's also "
          f"a reference image (see this script's module docstring).")
    print("Re-run harvest_support_icons.py to fold this into manifest.json and the reference catalog.")


if __name__ == "__main__":
    main()
