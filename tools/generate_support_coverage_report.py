"""
Regenerates assets/support_icon_coverage.md from the current
assets/harvested_supports/manifest.json -- the "rerun the query behind
this file" step that doc's own footer asks for (previously done by
hand, not saved as a script). Run this after harvest_support_icons.py
any time new captures have been folded in.

Needed (icon, tier) keys come straight from definitions/supports.json
(one key per real support's icon+tier_roman, deduplicated -- multiple
supports sharing a rendered icon at the same tier collapse into one
key, since one reference crop covers all of them). Breadth ("skills
that can roll it") counts distinct skills in
definitions/supports_by_skills.json whose PossibleSupports include that
support, after stripping the trailing " I"/" II"/" III" that file bakes
into each name (definitions/supports.json's own keys never carry that
suffix -- confirmed directly, e.g. "Lesser Increased Area of Effect" vs.
PossibleSupports' "Lesser Increased Area of Effect I").

A missing key can fall into one of two special groups instead of the
main hunting-targets table -- both "don't bother capturing this," but
with very different confidence. "Confirmed zero breadth" is computed
fresh from the extracted data itself every run (a support this proves
appears in NO skill's PossibleSupports at all). "Suspected non-live" is
the opposite kind of evidence -- a support WITH real structural
breadth, hand-flagged in support_icon_coverage_notes.json after direct
investigation found zero real-world occurrences despite that breadth
(see AI_RAMBLINGS.md) -- never proven impossible, just strongly
suspicious, and disclosed as exactly that, not silently merged with the
proven category.

Usage:
    python generate_support_coverage_report.py

Reads: definitions/supports.json, definitions/skills_by_mercenary.json,
    definitions/supports_by_skills.json, assets/harvested_supports/manifest.json,
    assets/support_icon_coverage_notes.json (hand-maintained, optional --
    see this module's docstring above)
Writes: assets/support_icon_coverage.md
"""
import json
import os
import re

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TOOLS_DIR)
SUPPORTS_PATH = os.path.join(PROJECT_ROOT, "definitions", "supports.json")
MERC_SKILLS_PATH = os.path.join(PROJECT_ROOT, "definitions", "skills_by_mercenary.json")
SUPPORTS_BY_SKILLS_PATH = os.path.join(PROJECT_ROOT, "definitions", "supports_by_skills.json")
MANIFEST_PATH = os.path.join(PROJECT_ROOT, "assets", "harvested_supports", "manifest.json")
NOTES_PATH = os.path.join(PROJECT_ROOT, "assets", "support_icon_coverage_notes.json")
OUTPUT_PATH = os.path.join(PROJECT_ROOT, "assets", "support_icon_coverage.md")

_TIER_SUFFIX_RE = re.compile(r"\s+(I|II|III)$")


def _strip_tier_suffix(name: str) -> str:
    return _TIER_SUFFIX_RE.sub("", name)


def _load_suspected_nonlive(supports: dict) -> list:
    """Hand-maintained (never touched by this script) list of {"reason":
    str, "names": [exact literal supports.json names]} groups -- see this
    module's docstring for why this is kept separate from the
    data-proven zero-breadth check. A typo'd name is warned about and
    dropped rather than silently accepted. Missing file is not an
    error."""
    if not os.path.isfile(NOTES_PATH):
        return []
    with open(NOTES_PATH, encoding="utf-8") as f:
        notes = json.load(f)
    groups = []
    for group in notes.get("suspected_nonlive", []):
        names = []
        for n in group.get("names", []):
            if n not in supports:
                print(f"  WARNING: {NOTES_PATH} names {n!r}, but that's not a real "
                      f"definitions/supports.json key -- check for a typo. Ignoring.")
                continue
            names.append(n)
        if names:
            groups.append({"reason": group.get("reason", ""), "names": names})
    return groups


def _skill_pool(entry: dict) -> list:
    return entry.get("Primary", []) + entry.get("Secondary", []) + entry.get("Utility", [])


def _breadth_and_example(support_name: str, skill_data: dict, merc_data: dict):
    count = 0
    example = None
    for skill, info in skill_data.items():
        stripped = {_strip_tier_suffix(s) for s in info.get("PossibleSupports", [])}
        if support_name not in stripped:
            continue
        count += 1
        if example is None:
            for merc, entry in merc_data.items():
                if skill in _skill_pool(entry):
                    example = (skill, merc)
                    break
    return count, example


def generate():
    with open(SUPPORTS_PATH, encoding="utf-8") as f:
        supports = json.load(f)["supports"]
    with open(MERC_SKILLS_PATH, encoding="utf-8") as f:
        merc_data = json.load(f)["mercenaries"]
    with open(SUPPORTS_BY_SKILLS_PATH, encoding="utf-8") as f:
        skill_data = json.load(f)["skills"]
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        clusters = json.load(f)["clusters"]

    needed = {}
    for name, entry in supports.items():
        key = f"{entry['icon']}_{entry['tier_roman'].lower()}"
        needed.setdefault(key, []).append(name)

    covered_keys = {}
    for c in clusters:
        vk = c.get("visual_key")
        if vk:
            covered_keys.setdefault(vk, []).append(c)

    covered = set(needed) & set(covered_keys)
    missing = sorted(set(needed) - set(covered_keys))

    ground_truth = manual_only = ambiguous = 0
    for k in covered:
        srcs = {c["source"] for c in covered_keys[k]}
        if "ground_truth" in srcs:
            ground_truth += 1
        elif "visual_key_ambiguous" in srcs:
            ambiguous += 1
        elif "manual" in srcs:
            manual_only += 1

    suspected_nonlive_groups = _load_suspected_nonlive(supports)
    suspected_nonlive_names = {n for g in suspected_nonlive_groups for n in g["names"]}

    rows = []
    hunting_rows = []
    tier1_keys = 0
    for key in missing:
        names = needed[key]
        tier_roman = supports[names[0]]["tier_roman"]
        if tier_roman == "I":
            tier1_keys += 1
        best_breadth, best_example = 0, None
        for name in names:
            breadth, example = _breadth_and_example(name, skill_data, merc_data)
            if breadth > best_breadth:
                best_breadth, best_example = breadth, example
        # Always show the tier explicitly, even though most names carry it
        # in a "Lesser "/"Greater " prefix -- a handful of real families
        # (Spell Cascade, Second Wind, Knockback, Multiple Projectiles...)
        # have a Tier III (or I) member with NO prefix at all, which reads
        # as ambiguous/looks-like-Tier-II without this (confirmed a real
        # point of confusion: "Spell Cascade" alone was mistaken for the
        # in-between tier when it's actually this family's Tier III).
        label = f"{' / '.join(names)} (Tier {tier_roman})"
        row = (best_breadth, label, best_example, names)
        rows.append(row)
        # Zero-breadth (proven from the data itself) and suspected-nonlive
        # (hand-flagged after direct investigation, see
        # support_icon_coverage_notes.json) both get their own dedicated
        # group below instead of cluttering the main hunting-targets table
        # with entries nobody should actually spend capture time chasing.
        if best_breadth == 0 or (suspected_nonlive_names & set(names)):
            continue
        hunting_rows.append(row)
    rows.sort(key=lambda r: -r[0])
    hunting_rows.sort(key=lambda r: -r[0])

    lines = []
    lines.append("# Support icon coverage checklist")
    lines.append("")
    lines.append("Tracks progress toward a labeled reference image for every real support.")
    lines.append("Regenerated by `tools/generate_support_coverage_report.py` from")
    lines.append("`assets/harvested_supports/manifest.json` -- see `tools/harvest_support_icons.py`")
    lines.append("and `AI_RAMBLINGS.md`'s \"Warrant-text alignment for skill/support ground")
    lines.append("truth\" for how coverage is actually built (ground truth from real")
    lines.append("`warrant.txt` text first, manual visual ID second, inference a distant")
    lines.append("last resort). Don't hand-edit the data rows below; re-run this script instead.")
    lines.append("")
    lines.append("## Why (icon, tier), not just icon")
    lines.append("")
    lines.append("A shared icon needs one reference PER TIER, not one overall: reference crops")
    lines.append("bake the rendered tier badge into the pixels (GGG's own icon asset doesn't")
    lines.append(f"have one -- it's a render-time overlay). The real count is **{len(needed)} (icon, tier)")
    lines.append(f"combinations** across the {len(supports)} real supports.")
    lines.append("")
    lines.append("## Current status")
    lines.append("")
    lines.append("```")
    lines.append(f"{len(needed)} total (icon, tier) keys needed")
    lines.append(f"{len(covered)} covered   ({ground_truth} confidently labeled from ground truth alone,")
    lines.append(f"               {manual_only} only resolved with a manual_labels.json visual ID,")
    lines.append(f"               {ambiguous} genuinely ambiguous -- two+ different real supports")
    lines.append("               proven to share pixels, not an unresolved gap; see")
    lines.append("               AI_RAMBLINGS.md's Minion Damage/Minion Life writeup)")
    lines.append(f"{len(missing):>3} missing entirely -- no harvested crop at all yet")
    lines.append("```")
    lines.append("")
    other_count = len(missing) - tier1_keys
    lines.append(f"**{tier1_keys} of {len(missing)} missing keys are tier \"I\" (Lesser)**, "
                  f"{other_count} are other tiers. Real captures skew toward high-tier rolls "
                  "(see AI_RAMBLINGS.md's `SupportCountTier` section), so the low end of a "
                  "support's own trio is inherently the rarest roll to catch on camera. For "
                  "most of these you already have coverage of the SAME support's other tiers "
                  "from the SAME kind of skill/mercenary -- nothing new to hunt for, just more "
                  "captures of what you're already getting.")
    lines.append("")
    lines.append("## Missing crops -- hunting targets")
    lines.append("")
    lines.append("One concrete (skill, mercenary) example per gap, plus the REAL breadth (how")
    lines.append("many distinct skills can roll it) as the actual difficulty signal -- the")
    lines.append("single example is just a place to start, not the only source. Sorted easiest")
    lines.append("(most skill options) to hardest (fewest).")
    lines.append("")
    lines.append("| Support | Example skill | Example mercenary | Skills that can roll it |")
    lines.append("|---|---|---|---|")
    for breadth, label, example, _names in hunting_rows:
        ex_skill, ex_merc = example if example else ("(none)", "(none)")
        lines.append(f"| {label} | {ex_skill} | {ex_merc} | {breadth} |")
    lines.append("")

    zero_breadth = [label for breadth, label, _example, _names in rows if breadth == 0]
    suspected_rows_by_group = [
        (g["reason"], [label for _breadth, label, _example, names in rows if set(names) & set(g["names"])])
        for g in suspected_nonlive_groups
    ]
    if zero_breadth or any(labels for _, labels in suspected_rows_by_group):
        lines.append("## Supports unlikely to ever get a captured reference")
        lines.append("")
        lines.append("Two groups, excluded from the hunting-targets table above -- not because")
        lines.append("they can't technically go there, but because continuing to spend capture")
        lines.append("time on them isn't worthwhile. Kept separate because they rest on very")
        lines.append("different evidence: one proven directly from the extracted data, the other")
        lines.append("a hand-flagged suspicion from external investigation. See this script's own")
        lines.append("module docstring.")
        lines.append("")

        if zero_breadth:
            lines.append("### Confirmed zero breadth (proven from extracted data)")
            lines.append("")
            lines.append("The following don't appear in ANY skill's `PossibleSupports` in the")
            lines.append("current extracted data -- not rare, structurally unobtainable as far as")
            lines.append("this data shows. Worth a second look at whether these are actually live")
            lines.append("in the current game version, or leftover/disabled entries in the source")
            lines.append("data.")
            lines.append("")
            for label in zero_breadth:
                lines.append(f"- {label}")
            lines.append("")

        live_groups = [(reason, labels) for reason, labels in suspected_rows_by_group if labels]
        if live_groups:
            lines.append("### Suspected non-live (real breadth, no confirmed real-world hits)")
            lines.append("")
            for reason, labels in live_groups:
                lines.append(reason)
                lines.append("")
                for label in labels:
                    lines.append(f"- {label}")
                lines.append("")

    narrow = [(breadth, label) for breadth, label, _example, _names in hunting_rows if 0 < breadth <= 2]
    if narrow:
        lines.append("## Notes")
        lines.append("")
        for breadth, label in narrow:
            plural = "" if breadth == 1 else "s"
            lines.append(f"- **{label}** is a genuinely narrow case ({breadth} skill option{plural} "
                          "total) -- everything else missing has more independent skill options.")
        lines.append("- This list needs a re-run once you've captured more; re-run "
                      "`harvest_support_icons.py` then this script rather than editing this "
                      "table by hand as gaps close.")
        lines.append("")

    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print(f"Wrote {OUTPUT_PATH}")
    print(f"{len(covered)}/{len(needed)} covered, {len(missing)} missing "
          f"({tier1_keys} tier-I, {other_count} other tiers)")


if __name__ == "__main__":
    generate()
