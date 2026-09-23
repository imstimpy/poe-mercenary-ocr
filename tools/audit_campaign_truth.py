"""
Audits capture_pipeline.match_support_icon() against the independent
"shadow ground truth" built by tools/harvest_campaign_review.py -- the
actual point of the whole campaign-review pipeline: checking the
classifier's Tier I confidence against something it didn't produce
itself (see AI_RAMBLINGS.md's writeup on the validation gap this
closes -- match_support_icon.py's own 100% figure is measured almost
entirely on Tier II/III data, since a real warrant.txt is structurally
impossible below level 68).

Read-only: never writes to assets/harvested_supports/, manual_labels.json,
tier_notes.json, or manual_truth.json. Just reports agreement.

Usage:
    python audit_campaign_truth.py

Reads: assets/campaign_review/manual_truth.json,
    assets/campaign_review/crops/*.png (regenerate first with
    harvest_campaign_review.py if a crop file is missing)
"""
import json
import os
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TOOLS_DIR)
sys.path.insert(0, PROJECT_ROOT)
import capture_pipeline as cp
from PIL import Image

TRUTH_PATH = os.path.join(PROJECT_ROOT, "assets", "campaign_review", "manual_truth.json")
CROPS_DIR = os.path.join(PROJECT_ROOT, "assets", "campaign_review", "crops")


def main():
    cp.NOTES_ENABLED = False
    with open(TRUTH_PATH, encoding="utf-8") as f:
        truth = json.load(f)

    agree = disagree = no_match = missing_crop = 0
    disagreements = []
    no_matches = []

    for full_hash, human_name in truth.items():
        path = os.path.join(CROPS_DIR, full_hash[:10] + ".png")
        if not os.path.isfile(path):
            missing_crop += 1
            continue
        crop = Image.open(path)
        visual_key = cp.match_support_icon(crop)
        if visual_key is None:
            no_match += 1
            no_matches.append((full_hash[:10], human_name))
            continue
        members = cp._support_visual_key_to_names().get(visual_key, [])
        member_names = {m[0] for m in members}
        if human_name in member_names:
            agree += 1
        else:
            disagree += 1
            disagreements.append((full_hash[:10], human_name, visual_key, sorted(member_names)))

    total = len(truth)
    print(f"Audited {total} human-verified crops against capture_pipeline.match_support_icon()")
    if missing_crop:
        print(f"  ({missing_crop} skipped -- no crop file on disk; re-run harvest_campaign_review.py first)")
    print()
    print(f"Agree:                                 {agree}/{total}")
    print(f"Disagree (classifier wrong or a gap):  {disagree}/{total}")
    print(f"No match (classifier returned None):   {no_match}/{total}")

    if disagreements:
        print("\nDisagreements:")
        for h, human, vk, members in disagreements:
            print(f"  {h}: human says {human!r}, classifier says {vk!r} (members: {members})")

    if no_matches:
        print("\nClassifier found nothing confident for:")
        for h, human in no_matches:
            print(f"  {h}: human says {human!r}")


if __name__ == "__main__":
    main()
