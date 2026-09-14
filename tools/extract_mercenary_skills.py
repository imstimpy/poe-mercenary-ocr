import json
import os
from typing import Dict, List, Any

# PoE support gem tiers are a small closed set (1-3) -- mapped to the
# roman-numeral suffix convention already used in this project's
# hand-collected skill_supports.json (e.g. "Lesser Throwing Speed I"),
# confirmed to match exactly: mercenarysupports.json's Name field
# already includes the Lesser/Greater qualifier, Tier is the separate
# 1/2/3 integer, and combining them reproduces every entry in our
# existing manual data for Lightning Trap byte-for-byte.
TIER_TO_ROMAN = {1: "I", 2: "II", 3: "III"}


def get_skill_names_from_id(skill_ids: List[int], skills_table: List[Dict[str, Any]]) -> List[str]:
    """
    Transforms skill ids into names with a lookup.
    """
    skill_names = []

    for skill_id in skill_ids:
        target_entry = next((item for item in skills_table if item.get("_rid") == skill_id), None)

        if target_entry:
            print(f"Found skill '{target_entry['Name']}'")
            skill_names.append(target_entry.get("Name", "Unknown"))
        else:
            print(f"No entry found with _rid: {skill_id}")

    return skill_names


def get_support_count_tier(support_count_id: int, support_counts_table: List[Dict[str, Any]]) -> str:
    """
    Resolves a skill's raw "SupportCount" field into its tier name
    (Low/Medium/High/None) via mercenarysupportcounts.json.

    This field is NOT a literal support count, despite the name -- it's a
    foreign-key index into mercenarysupportcounts.datc64 (a 4-row
    Low/Medium/High/None enum).
    """
    if support_count_id is None:
        return None
    target_entry = next((item for item in support_counts_table if item.get("_rid") == support_count_id), None)
    return target_entry.get("Id", "Unknown") if target_entry else "Unknown"


# Per-tier min/max support counts a skill can actually spawn with, in
# high-level zones (maps) specifically -- source: poewiki.net's
# Mercenary page. Lower-level areas cap the maximum lower than this, but
# that scaling isn't documented with concrete numbers there, and this
# project only targets max-level (level 83, map-tier) mercenary
# encounters, so that lower-level case isn't handled here.
SUPPORT_COUNT_TIER_RANGES = {
    "None": (0, 0),
    "Low": (1, 2),
    "Medium": (2, 3),
    "High": (3, 5),
}


def resolve_support_count_range(tier: str):
    """
    Stub: maps a skill's support-count TIER (see get_support_count_tier)
    to the (min, max) number of supports it can actually spawn with, per
    SUPPORT_COUNT_TIER_RANGES. Not yet wired into extract_mercenary_data's
    output -- supports_by_skills.json currently records the tier name
    only, not a resolved range, since exposing a range invites treating
    it as precise when the real number for a given encounter still can't
    be derived from these tables alone (it's presumably randomized within
    the tier's range per-spawn, not a fixed value). Kept here as the
    known, wiki-sourced mapping so a future feature (e.g. flagging an
    OCR'd support list whose count falls outside its skill's tier range)
    doesn't have to rediscover it.
    """
    return SUPPORT_COUNT_TIER_RANGES.get(tier)


def get_support_names_from_id(support_ids: List[int], supports_table: List[Dict[str, Any]]) -> List[str]:
    """
    Transforms support ids into display names (Name + roman-numeral
    Tier, e.g. "Lesser Throwing Speed I") with a lookup, matching the
    convention already established by hand in this project's
    skill_supports.json.
    """
    support_names = []

    for support_id in support_ids:
        target_entry = next((item for item in supports_table if item.get("_rid") == support_id), None)

        if target_entry:
            name = target_entry.get("Name", "Unknown")
            tier = target_entry.get("Tier")
            tier_label = TIER_TO_ROMAN.get(tier, str(tier) if tier is not None else "")
            display_name = f"{name} {tier_label}".strip()
            print(f"Found support '{display_name}'")
            support_names.append(display_name)
        else:
            print(f"No entry found with _rid: {support_id}")

    return support_names


def get_support_details(supports_table: List[Dict[str, Any]], support_families_table: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Builds a complete {support Name: {"icon": ..., "family": ...}}
    lookup directly from mercenarysupports.json.

    Keyed by the exact `Name` field (tier prefix included where the game
    itself includes one, e.g. "Lesser Cooldown Recovery" / "Cooldown
    Recovery" / "Greater Cooldown Recovery" are three separate keys) --
    NOT collapsed to one canonical name per family. Strip
    a trailing " I"/" II"/" III" from a PossibleSupports string to get
    the key to look up here.

    One real Name collision exists in the source data (two different
    _rid rows both named "Gilded Extra Targets", one per skill that
    grants it) -- confirmed both rows agree on icon and family, so
    collapsing them into one dict key here loses nothing.
    """
    family_id_to_name = {f["_rid"]: f.get("Id", "Unknown") for f in support_families_table}

    support_details: Dict[str, Any] = {}
    for support in supports_table:
        name = support.get("Name", "Unknown")
        icon_path = support.get("GemIcon", "")
        icon = os.path.splitext(os.path.basename(icon_path))[0].lower()
        family_id = support.get("SupportFamily")
        family_name = family_id_to_name.get(family_id) if family_id is not None else None
        support_details[name] = {
            "icon": icon,
            "family": family_name,
        }
    return support_details


def extract_mercenary_data(dat_dir: str, output_dir: str) -> None:
    """
    Parses PoE Mercenary JSON files (exported from datc64) into human-readable JSON files.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Load PoE JSON resources
    try:
        with open(os.path.join(dat_dir, "mercenarybuilds.json"), "r", encoding="utf-8") as f:
            mercenaries_table = json.load(f)
        with open(os.path.join(dat_dir, "mercenaryskills.json"), "r", encoding="utf-8") as f:
            mercenary_skills_table = json.load(f)
        with open(os.path.join(dat_dir, "mercenarysupports.json"), "r", encoding="utf-8") as f:
            mercenary_supports_table = json.load(f)
        with open(os.path.join(dat_dir, "mercenarysupportcounts.json"), "r", encoding="utf-8") as f:
            mercenary_support_counts_table = json.load(f)
        with open(os.path.join(dat_dir, "mercenarysupportfamilies.json"), "r", encoding="utf-8") as f:
            mercenary_support_families_table = json.load(f)
    except FileNotFoundError as e:
        print(f"Error: Missing required extracted source data table. Details: {e}")
        return

    # Use a list of mercenary type names
    mercenary_types: Dict[str, List[str]] = {}
    mercenary_types["note"] = "AUTOGENERATED FILE -- DO NOT MODIFY"
    mercenary_types.setdefault("types", [])

    # Use a dictionary where the KEY is the mercenary type name
    mercenary_skills: Dict[str, Any] = {}
    mercenary_skills["note"] = "AUTOGENERATED FILE -- DO NOT MODIFY"
    mercenary_skills.setdefault("mercenaries", {})

    for merc in mercenaries_table:
        merc_id = merc["_rid"]
        merc_name = merc.get("Name", "Unknown")
        print(f"processing: '{merc_name}' ({merc_id})")
        merc_skills1_ids = merc.get("Skills1", [])
        skills2count = merc.get("Skills2Count")
        merc_skills2_ids = merc.get("Skills2", [])
        skills3count = merc.get("Skills3Count")
        merc_skills3_ids = merc.get("Skills3", [])

        merc_skills1 = get_skill_names_from_id(merc_skills1_ids, mercenary_skills_table)
        merc_skills2 = get_skill_names_from_id(merc_skills2_ids, mercenary_skills_table)
        merc_skills3 = get_skill_names_from_id(merc_skills3_ids, mercenary_skills_table)

        if merc_name not in mercenary_types["types"]:
            #print(f"adding: '{merc_name}'")
            mercenary_types["types"].append(merc_name)

        if merc_name not in mercenary_skills["mercenaries"]:
            #print(f"adding: '{merc_name}'")
            mercenary_skills["mercenaries"][merc_name] = {
                "PrimaryCount": len(merc_skills1),
                "Primary": merc_skills1,
                "SecondaryCount": skills2count,
                "Secondary": merc_skills2,
                "UtilityCount": skills3count,
                "Utility": merc_skills3
            }

    # Use a dictionary where the KEY is the skill name -- iterates over
    # EVERY skill in mercenaryskills.json directly (not just ones
    # referenced by a mercenary above), since this is meant as a
    # standalone, complete skill->supports lookup table, not limited to
    # whichever skills happen to already be in use.
    supports_by_skill: Dict[str, Any] = {}
    supports_by_skill["note"] = "AUTOGENERATED FILE -- DO NOT MODIFY"
    supports_by_skill.setdefault("skills", {})

    for skill in mercenary_skills_table:
        skill_name = skill.get("Name", "Unknown")
        print(f"processing skill: '{skill_name}'")
        possible_support_ids = skill.get("PossibleSupports", [])
        support_count_tier = get_support_count_tier(skill.get("SupportCount"), mercenary_support_counts_table)

        support_names = get_support_names_from_id(possible_support_ids, mercenary_supports_table)

        if skill_name not in supports_by_skill["skills"]:
            supports_by_skill["skills"][skill_name] = {
                "SupportCountTier": support_count_tier,
                "PossibleSupports": support_names
            }

    # Complete, standalone {support Name: {icon, family}} lookup --
    # covers all 266 real support entries directly from
    # mercenarysupports.json, not just ones some skill's PossibleSupports
    # happens to reference.
    support_details: Dict[str, Any] = {}
    support_details["note"] = "AUTOGENERATED FILE -- DO NOT MODIFY"
    support_details["supports"] = get_support_details(mercenary_supports_table, mercenary_support_families_table)

    mercenary_types_output_path = os.path.join(output_dir, "mercenary_types.json")
    mercenary_skill_output_path = os.path.join(output_dir, "skills_by_mercenary.json")
    supports_by_skills_output_path = os.path.join(output_dir, "supports_by_skills.json")
    support_details_output_path = os.path.join(output_dir, "supports.json")

    # newline="\n" on every output write below -- Python's text-mode
    # open() otherwise translates each '\n' json.dump writes into the
    # platform default line ending (CRLF on Windows), which is what was
    # triggering git's CRLF/LF warning on these files. Forcing LF here
    # keeps the generated files' line endings consistent regardless of
    # what OS the extraction is run on, rather than relying on git
    # config/.gitattributes to normalize it after the fact.
    try:
        with open(mercenary_types_output_path, "w", encoding="utf-8", newline="\n") as out_f:
            json.dump(mercenary_types, out_f, indent=2, ensure_ascii=False, sort_keys=False)
        with open(mercenary_skill_output_path, "w", encoding="utf-8", newline="\n") as out_f:
            json.dump(mercenary_skills, out_f, indent=2, ensure_ascii=False, sort_keys=False)
        with open(supports_by_skills_output_path, "w", encoding="utf-8", newline="\n") as out_f:
            json.dump(supports_by_skill, out_f, indent=2, ensure_ascii=False, sort_keys=False)
        with open(support_details_output_path, "w", encoding="utf-8", newline="\n") as out_f:
            json.dump(support_details, out_f, indent=2, ensure_ascii=False, sort_keys=False)
    except OSError as e:
        print(f"Error: Unable to write output files in '{output_dir}'. Details: {e}")
        return

    print(f"Successfully extracted! Check the output directory: '{output_dir}'")


if __name__ == '__main__':
    # Adjust paths based on your data-mining folder scheme
    extract_mercenary_data(dat_dir="./extracted_dats", output_dir="./mercenary_jsons")
