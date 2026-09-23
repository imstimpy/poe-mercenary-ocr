"""
Part 1 of wiring in support identification (see AI_RAMBLINGS.md's "Open
action items" flag): walks every real capture, clips each occupied cell
out of the fixed grid calibrated in `definitions/mercenary_regions.json`
(`regions.supports.grid`), and groups the results into unique real
(icon, tier) crops -- a post-process/batch job over already-saved
captures, same category as `compose_gem_reference.py`, not part of the
live `capture_pipeline.py` capture path.

**Ground truth first, inference only as a fallback.** A real
`warrant.txt` (the raw copied item, not the OCR'd `warrant_generated.txt`)
lists each skill's actual equipped supports as plain text with an exact
tier number -- `DoT Multiplier (Tier: 2)` -- and that text matches a
`definitions/supports.json` key byte-for-byte, confirmed directly
against real data. That's zero-ambiguity ground truth, no image
matching involved at all -- an earlier version of this script didn't
check for it and instead tried to INFER identity by intersecting a
skill's `PossibleSupports` pool across every capture a crop appeared
in, which only ever narrows to a candidate list, never a certainty,
and only for crops seen more than once. Wherever a capture has usable
`warrant.txt` support/tier text, this uses it directly: column order
on screen matches the listed order under each skill exactly (verified
against a real example), so pairing position N of the grid to entry N
of that skill's listed supports gives an exact label with no
narrowing needed.

The inference path (candidate-narrowing via `PossibleSupports`
intersection, optionally sharpened by a hand-supplied tier in
`tier_notes.json`) is kept as a fallback ONLY for captures that have no
usable `warrant.txt` -- historical captures, or ones where only the
OCR'd `warrant_generated.txt` was saved. **Not just a data-collection
gap below level 68**: a mercenary warrant genuinely cannot be obtained
at all before then (real game constraint, confirmed directly), so
every sub-68 capture is permanently inference-only -- there's no later
"go back and get the real warrant.txt" for these, unlike a merely
under-captured high-level skill. `__captures_campaign/` (all sub-35)
is the extreme case: 0 of 20 sessions have a `warrant.txt`, and never
will. See AI_RAMBLINGS.md's Tier I support detection writeup for what
this means for coverage below Tier II.

**A fourth source sits between manual identification and inference:
`resolve_via_embedding()` cross-checks `capture_pipeline.match_support_icon()`
(the same classifier already validated at 100% against 2094 real
ground-truth crops) against the text-narrowed candidate list, and
trusts it only when the two agree.** Text-based narrowing alone
routinely can't finish the job even when the actual pixels aren't
ambiguous -- two skills can each structurally offer both `Chain` and
`Multiple Projectiles` somewhere in their pool with no way to tell
which ONE a specific crop is from skill names alone, while the
classifier recognizes the actual icon (and, via its own tier-badge
correction, the actual tier) directly from pixels, no `tier_notes.json`
entry required. Checked directly: 99 of 108 real crops stuck in the
ambiguous-inferred bucket before this existed had a confident embedding
match that agreed with the structural candidates; the other 9 disagreed
and are correctly left for manual review rather than trusted blindly.
Source `"embedding_confirmed"` in the manifest distinguishes these from
`"manual"` (a human's own visual ID) and `"inferred"` (text narrowing
alone) -- always disclosed, never silently merged into either.

**A fifth source: a promoted campaign shadow-ground-truth identification.**
`tools/harvest_campaign_review.py` builds an independent record
(`assets/campaign_review/manual_truth.json`) of a human reading a
support's exact name/tier straight off the real in-game tooltip for a
sub-68 (no-warrant) mercenary, with candidates anchored only to the
contributing skill's real `PossibleSupports` pool -- zero icon-matching
involvement anywhere. That's built specifically to stay independent of
this catalog, for auditing `match_support_icon()` against something it
had no part in producing (see `tools/audit_campaign_truth.py`). But
several real (icon, tier) keys may never show up in a warrant-backed
capture at all -- `assets/support_icon_coverage.md`'s missing list
skews heavily toward Tier I ("Lesser") rolls precisely because those
are rarest to catch on camera at the high levels a warrant requires.
`tools/promote_campaign_truth.py` lets a human deliberately promote a
*specific* campaign identification into this catalog once they've
decided it's needed to close a real gap -- copying its crop into
`assets/harvested_supports/campaign_promoted/` and recording
`{full_hash: name}` in `assets/harvested_supports/campaign_promotions.json`
(hand-maintained by that tool, same read-only convention as
`tier_notes.json`/`manual_labels.json`). Trusted with the same
confidence as a real warrant.txt label -- source `"campaign_confirmed"`
in the manifest, always disclosed, never merged into `"ground_truth"`
or `"manual"`. Promoting a hash removes it from `manual_truth.json`
(not a duplication -- `campaign_promotions.json` is its new home)
specifically so it stops counting toward `audit_campaign_truth.py`'s
numbers once it's also a reference image: auditing the classifier
against an image that's now one of its OWN references would be
circular, not an independent check anymore.

Usage:
    python harvest_support_icons.py

Writes to assets/harvested_supports/ -- a NEW staging area, deliberately
not added to capture_pipeline.py's REFERENCE_CATEGORIES, so nothing
here is used for real matching until someone reviews it and promotes
it, same "pending until validated" caution `assets/README.md` already
applies to every other extracted-vs-captured asset category.

Reading the tier badge (I/II/III) off a crop by eye and recording it is
something a human can do trivially that image-matching code can't yet
(that's Part 2) -- so a hand-editable `tier_notes.json` sits alongside
the autogenerated manifest specifically for that: add
`{"unlabeled_3f1880068a": "II"}` (or "I"/"III") for any crop whose
numeral you can read -- the key can be exactly the filename as saved
(`unlabeled_3f1880068a.png`, with or without the `unlabeled_` prefix
and `.png` suffix) or the full hash from manifest.json, whichever you
have on hand; the next run narrows that cluster's candidates to only
identities the contributing skill(s) actually offer AT THAT TIER, often
down to one. `tier_notes.json` is never written by this script and
never overwritten by it -- only read.

Some crops stay ambiguous even with a tier, though, when a single-
occurrence crop's contributing skill offers many different real
supports at that icon+tier (nothing to cross-reference against yet).
For those, visually identifying the icon itself is something a human
can also do that this script can't -- so `manual_labels.json` (same
directory, same hand-maintained/read-only convention as
`tier_notes.json`) accepts `{"<hash-or-prefix>": "<exact literal
definitions/supports.json name>"}`, e.g. `{"942cb59279": "Trap and Mine
Damage"}`. Checked against the cluster's own structural data before
being trusted (the named support's icon must actually be one the
contributing skill(s) could show, and its tier must agree with any
tier_notes.json entry) -- a typo or a mismatched hash gets a warning
and is ignored, not silently written out as a wrong reference image.
"""

import glob
import hashlib
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from PIL import Image
import numpy as np

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TOOLS_DIR)

# capture_pipeline.py resolves its own asset paths relative to the
# current working directory (see its SUPPORT_REFERENCE_EMBEDDINGS_PATH
# etc.), so this script needs to run with PROJECT_ROOT as cwd to import
# and call it correctly -- chdir here rather than documenting "must run
# from the project root" (tools/match_support_icon.py's approach)
# because every path in THIS file is already PROJECT_ROOT-based, so
# nothing here cares what the original cwd was, and the alternative is
# a class of "wrong directory" mistakes for no real benefit.
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
import capture_pipeline as cp

REGIONS_PATH = os.path.join(PROJECT_ROOT, "definitions", "mercenary_regions.json")
SUPPORTS_BY_SKILLS_PATH = os.path.join(PROJECT_ROOT, "definitions", "supports_by_skills.json")
SUPPORTS_PATH = os.path.join(PROJECT_ROOT, "definitions", "supports.json")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "assets", "harvested_supports")
VARIANTS_DIR = os.path.join(OUTPUT_DIR, "variants")

# Same INSET capture_pipeline.py applies to every region's raw absolute box
# before cropping -- the grid's absolute coordinates in mercenary_regions.json
# are pre-inset, same convention as every other region, so this converts them
# into the coordinate frame of the already-cropped, already-inset supports.png
# files sitting in captures/.
INSET = 2

# A blank slot's cell has near-zero variance (flat background); an occupied
# one has real icon art + a gold border + a tier-numeral badge, all high-
# contrast against that background. Measured directly against a known-blank
# vs known-filled cell from a real capture: std ~1.0-1.3 blank, ~42 filled --
# 5.0 sits with a wide safety margin between the two.
OCCUPIED_STD_THRESHOLD = 5.0

_TIER_STRIP_RE = re.compile(r"^(?:Lesser |Greater )?(.*?)(?: I| II| III)?$")
_TIER_SUFFIX_RE = re.compile(r" (I|II|III)$")
_UNRECOGNIZED_TRAILER_RE = re.compile(r"^# unrecognized skill row\(s\), dropped:")
_SUPPORT_LINE_RE = re.compile(r"^(.*) \(Tier: (\d+)\)$")
TIER_NOTES_PATH = os.path.join(OUTPUT_DIR, "tier_notes.json")
MANUAL_LABELS_PATH = os.path.join(OUTPUT_DIR, "manual_labels.json")
CAMPAIGN_PROMOTIONS_PATH = os.path.join(OUTPUT_DIR, "campaign_promotions.json")
CAMPAIGN_PROMOTED_DIR = os.path.join(OUTPUT_DIR, "campaign_promoted")


def extract_tier(name: str) -> Optional[str]:
    m = _TIER_SUFFIX_RE.search(name)
    return m.group(1) if m else None


def load_tier_notes() -> Dict[str, str]:
    """Hand-maintained {hash: "I"|"II"|"III"} -- see this file's module
    docstring. Missing file (nothing annotated yet) is not an error."""
    if not os.path.isfile(TIER_NOTES_PATH):
        return {}
    with open(TIER_NOTES_PATH, encoding="utf-8") as f:
        return json.load(f)


def _normalize_tier_note_key(key: str) -> str:
    """A key exactly as it appears in an unlabeled_*.png filename -- the
    only form of the hash someone looking at the saved crops actually
    has on hand, without opening manifest.json to find the full one --
    e.g. "unlabeled_3f1880068a", "unlabeled_3f1880068a.png", or just
    "3f1880068a" all normalize to the same "3f1880068a" prefix."""
    key = key.strip()
    if key.startswith("unlabeled_"):
        key = key[len("unlabeled_"):]
    if key.endswith(".png"):
        key = key[:-len(".png")]
    return key


def _match_hash_override(full_hash: str, overrides: Dict[str, str]) -> List[Tuple[str, str]]:
    """Matches a cluster's full hash against a hand-maintained {key: value}
    override file's keys, which may be the full hash or just the
    truncated prefix used in an unlabeled_*.png filename (see
    _normalize_tier_note_key) -- that's the only thing available without
    cross-referencing manifest.json by hand, so this accepts it directly
    rather than requiring the full hash. Shared by tier_notes.json
    (values are "I"/"II"/"III") and manual_labels.json (values are exact
    literal `definitions/supports.json` names)."""
    return [
        (raw_key, value) for raw_key, value in overrides.items()
        if full_hash.startswith(_normalize_tier_note_key(raw_key))
    ]


def resolve_tier_note(full_hash: str, tier_notes: Dict[str, str]) -> Optional[str]:
    """See _match_hash_override. Warns (rather than picking one silently)
    only when matching keys actually DISAGREE on the tier -- two keys
    matching the same hash and agreeing (e.g. the same crop tagged once
    by its full hash and once by its filename prefix, both "III") is
    redundant, not ambiguous, and real: confirmed happening the first
    time a tagger session's output got merged with an earlier hand-added
    entry for the same cluster."""
    matches = _match_hash_override(full_hash, tier_notes)
    distinct_tiers = {tier for _, tier in matches}
    if len(distinct_tiers) > 1:
        print(f"  WARNING: tier_notes.json keys {[k for k, _ in matches]!r} disagree on the tier "
              f"for hash {full_hash} ({sorted(distinct_tiers)}) -- ignoring all of them for this cluster.")
        return None
    return matches[0][1] if matches else None


def load_manual_labels() -> Dict[str, str]:
    """Hand-maintained {hash-or-prefix: exact literal supports.json name}
    -- for a crop a human has visually identified by eye (real icon
    content, real name), something image-matching code can't do yet.
    Different in kind from tier_notes.json (which only narrows an
    already-computed candidate list): this names the crop outright, with
    the same confidence as ground truth, so it's verified against the
    cluster's own structural data before being trusted -- see
    resolve_manual_label. Missing file is not an error."""
    if not os.path.isfile(MANUAL_LABELS_PATH):
        return {}
    with open(MANUAL_LABELS_PATH, encoding="utf-8") as f:
        return json.load(f)


def resolve_manual_label(
    full_hash: str, manual_labels: Dict[str, str], supports: dict,
    common_icons: set, tier: Optional[str],
) -> Optional[str]:
    """Resolves a manual_labels.json entry for this cluster, but only
    after checking it's actually consistent with what's structurally
    known -- a typo'd name, or one that doesn't match this cluster's own
    icon/tier, should be caught here rather than silently trusted and
    written out as a wrong reference image. Warns and refuses (returns
    None) rather than guessing which side is right."""
    matches = _match_hash_override(full_hash, manual_labels)
    names = {name for _, name in matches}
    if len(names) > 1:
        print(f"  WARNING: manual_labels.json keys {[k for k, _ in matches]!r} disagree on the "
              f"name for hash {full_hash} ({sorted(names)}) -- ignoring all of them for this cluster.")
        return None
    if not names:
        return None
    name = next(iter(names))
    entry = supports.get(name)
    if entry is None:
        print(f"  WARNING: manual_labels.json names {name!r} for hash {full_hash}, but that's not "
              f"a real definitions/supports.json key -- check for a typo. Ignoring.")
        return None
    if common_icons and entry["icon"] not in common_icons:
        print(f"  WARNING: manual_labels.json names {name!r} for hash {full_hash}, but its icon "
              f"({entry['icon']!r}) isn't even one this cluster's contributing skill(s) could show "
              f"({sorted(common_icons)}) -- check for a mismatched hash. Ignoring.")
        return None
    if tier is not None and entry["tier_roman"] != tier:
        print(f"  WARNING: manual_labels.json names {name!r} (tier {entry['tier_roman']}) for hash "
              f"{full_hash}, but tier_notes.json says tier {tier!r} -- the two disagree. Ignoring "
              f"the manual label until this is resolved by hand.")
        return None
    return name


def load_campaign_promotions() -> Dict[str, str]:
    """Hand-maintained (written by tools/promote_campaign_truth.py, never
    edited by hand directly and never touched by this script) {full_hash:
    exact literal supports.json name} for a campaign shadow-ground-truth
    crop a human has chosen to promote into this catalog -- see this
    module's docstring for why that's trustworthy despite
    harvest_campaign_review.py being built to stay independent of this
    catalog. Missing file is not an error."""
    if not os.path.isfile(CAMPAIGN_PROMOTIONS_PATH):
        return {}
    with open(CAMPAIGN_PROMOTIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


def resolve_via_embedding(crop: Image.Image, candidates: set, common_icons: set,
                           supports: dict, identity) -> Tuple[Optional[str], list]:
    """Cross-checks capture_pipeline.match_support_icon() -- the SAME
    embedding classifier already validated at 100% against 2094 real
    ground-truth crops (see tools/match_support_icon.py) -- against what
    text-based narrowing already knows is structurally possible for this
    crop. Never trusted alone: a name the classifier's recognized icon
    doesn't even offer as a real member, or that isn't structurally
    possible per `candidates`/`common_icons`, is never returned.

    This exists because text-based candidate narrowing alone routinely
    can't finish the job even when the actual pixels aren't ambiguous at
    all -- e.g. two skills can each structurally offer both `Chain` and
    `Multiple Projectiles` somewhere in their pool with no way to tell
    which ONE icon a specific crop uses from skill names alone, while
    the classifier recognizes the actual icon on sight. Also routinely
    gets the TIER right via match_support_icon()'s own tier-badge
    correction even when no tier_notes.json entry exists for this hash
    at all, and even when there's only ONE candidate identity but that
    identity spans more than one tier (an identity of size 1 doesn't by
    itself say which tier -- confirmed on a real "PhysGainAs" crop with
    no tier_notes.json entry that this alone resolved).

    Returns (visual_key, matches) -- matches is the list of
    (name, tier_roman) members of that visual_key that survive the
    structural check. Caller distinguishes three real outcomes: 0
    matches (classifier disagreed with everything structurally possible
    -- stays unresolved, same as before this existed), exactly 1 (a real
    resolution), or 2+ (every one of them IS structurally possible --
    not a disagreement, a genuine icon+tier collision confirmed for this
    specific crop, same category as a ground-truth-discovered one, not
    something that needs -- or can get -- a human's help). visual_key is
    None only when the classifier itself had nothing confident to say.
    """
    visual_key = cp.match_support_icon(crop)
    if visual_key is None:
        return None, []
    members = cp._support_visual_key_to_names().get(visual_key)
    if not members:
        return visual_key, []
    matches = [
        (name, tier_roman) for name, _tier_int, tier_roman in members
        if identity(name) in candidates and supports.get(name, {}).get("icon") in common_icons
    ]
    return visual_key, matches


def load_grid() -> Tuple[List[Tuple[int, int]], List[Tuple[int, int]]]:
    """Returns (columns, rows) as lists of (start, end) pixel offsets, LOCAL
    to a saved supports.png crop (i.e. already adjusted for the supports
    region's own absolute origin + INSET)."""
    with open(REGIONS_PATH, encoding="utf-8") as f:
        regions = json.load(f)["regions"]
    supports_region = regions["supports"]
    grid = supports_region["grid"]
    origin_x = supports_region["absolute"]["x0"] + INSET
    origin_y = supports_region["absolute"]["y0"] + INSET
    columns = [(c["x0"] - origin_x, c["x1"] - origin_x) for c in grid["columns"]]
    rows = [(r["y0"] - origin_y, r["y1"] - origin_y) for r in grid["rows"]]
    return columns, rows


def strip_tier(name: str) -> str:
    """Strips a leading Lesser/Greater AND a trailing roman numeral --
    used ONLY for identity()'s bare-name sibling grouping (matching real
    Lesser/bare/Greater trios that share an icon but weren't given a
    populated `family`). NOT safe for looking up a literal
    `definitions/supports.json` key -- see strip_trailing_tier."""
    m = _TIER_STRIP_RE.match(name)
    return m.group(1) if m else name


def strip_trailing_tier(name: str) -> str:
    """Strips ONLY a trailing ' I'/' II'/' III' -- this, not strip_tier,
    is what recovers a real `definitions/supports.json` key from a
    `PossibleSupports` entry, since supports.json's own keys keep any
    Lesser/Greater prefix verbatim (`"Greater Curse Effect"` is its own
    key, not `"Curse Effect"`). Using strip_tier here was a real bug --
    it silently turned "Greater Curse Effect III" into a lookup for the
    nonexistent "Curse Effect" and dropped that support out of the icon
    map entirely, which is what caused a real cluster (Boiling Blood/
    Burning Arrow/Punishment sharing tier-III `mercsilverintsupportgem`)
    to come back with an impossible empty candidate set."""
    return re.sub(r" (?:I|II|III)$", "", name)


def build_identity_resolver(supports: Dict[str, dict]):
    """Same `identity()` concept used throughout this project's real support
    analysis (see AI_RAMBLINGS.md): family name when populated, else the
    corrected Lesser/bare/Greater grouping for supports with a real trio but
    a `family: null` data gap (e.g. Trigger Radius, Leech), else the
    support's own literal Name (a standalone identity)."""
    def identity(name: str) -> str:
        entry = supports.get(name)
        if entry is None:
            return name
        if entry["family"] is not None:
            return entry["family"]
        bare = strip_tier(name)
        icon = entry["icon"]
        siblings = [
            n for n, e in supports.items()
            if e["icon"] == icon and e["family"] is None and strip_tier(n) == bare
        ]
        if len(siblings) > 1:
            return f"[{bare}]"
        return name
    return identity


def build_identity_members(supports: Dict[str, dict], identity) -> Dict[str, List[str]]:
    """Reverse of identity(): {identity_value: [literal supports.json names
    that resolve to it]}. Exists because an identity value is sometimes an
    internal short code (family strings like "WED" or "DotMulti", not
    something a player would recognize) or a manufactured bare-name/family
    label ("[Trigger Radius]") -- this is what recovers the actual
    real display name(s) (e.g. "WED" -> "Elemental Damage with Attacks")
    for anyone reading the manifest by eye."""
    members: Dict[str, List[str]] = defaultdict(list)
    for name in supports:
        members[identity(name)].append(name)
    return {k: sorted(v) for k, v in members.items()}


def canonical_display_name(identity_value: str, members: List[str]) -> str:
    """Picks the one member with no Lesser/Greater/Gilded prefix as the
    human-facing name for a family (e.g. "Elemental Damage with Attacks"
    for family "WED", "Critical Chance" for family "CritChance" even
    though that family also covers "Gilded Jolt"/"Gilded Voltage") --
    excluding "Gilded " specifically, not just "Lesser "/"Greater ", is
    what a first version of this got wrong: alphabetically "Gilded Area
    per Projectile" sorts before "Increased Area of Effect" for family
    IncreaseAreaOfEffect, and "Gilded " doesn't start with "Lesser "/
    "Greater " either, so the plain, recognizable name was losing to the
    Gilded one on nothing but sort order. Falls back to the identity
    value itself on the rare family with no plain member at all."""
    for m in members:
        if not m.startswith(("Lesser ", "Greater ", "Gilded ")):
            return m
    return identity_value


def visual_key_for_name(name: str, supports: dict) -> Optional[str]:
    """GGG's own naming convention, not a re-slugified display name: the
    real icon basename exactly as `definitions/supports.json` stores it
    (already lowercase, no separators -- "firepenetration",
    "mercgoldsupportgem") plus the real tier's roman numeral, lowercased
    and joined with "_" -- e.g. "firepenetration_iii". Uses `tier_roman`
    straight from supports.json (the game's own `Tier` field) rather
    than guessing from the Name string -- guessing from a Lesser/
    Greater prefix (or its absence) is wrong for standalone entries like
    every "Gilded X" support, which has no prefix but is tier 3, not 2
    (confirmed against the raw data). This is the identity that matters
    for a REFERENCE IMAGE specifically -- our crops bake the rendered
    tier badge into the pixels (unlike GGG's own icon asset, which has
    no badge and is genuinely shared across all tiers), so two different
    real tiers of one family need two different reference files even
    though they're "the same icon" by GGG's own data model."""
    entry = supports.get(name)
    if entry is None or entry.get("tier_roman") is None:
        return None
    return f"{entry['icon']}_{entry['tier_roman'].lower()}"


def icon_identity_map_for_skill(
    skill_name: str, skills: dict, supports: dict, identity
) -> Optional[Dict[str, Dict[Optional[str], set]]]:
    """{icon: {tier: {identities that skill could show via that icon at
    that tier}}} for one skill's PossibleSupports -- NOT a flat identity
    set. Flattening across every icon in a skill's pool (an earlier
    version of this function did exactly that) is wrong: it lets two
    crops be "intersected" as sharing an identity just because both
    skills happen to offer that identity SOMEWHERE, even via a completely
    different, non-matching icon. Caught on a real cluster (Boiling
    Blood/Burning Arrow/Punishment, tier-III mercsilverintsupportgem)
    that came back with an impossible EMPTY intersection under the flat
    version -- these 3 skills share no common identity across their full
    pools, but they DO all offer some identity via this one shared icon
    (`DotMulti` for two of them, `Greater Curse Effect` for the third),
    which is the only fact this crop can actually tell us.

    Keeping tier as an inner key (rather than flattening it away too)
    is what lets a hand-supplied tier_notes.json entry narrow a cluster
    further once someone's read the numeral off the real crop -- see
    this module's docstring."""
    data = skills.get(skill_name)
    if data is None:
        return None
    result: Dict[str, Dict[Optional[str], set]] = {}
    for n in data["PossibleSupports"]:
        base = strip_trailing_tier(n)
        entry = supports.get(base)
        if entry is None:
            continue
        tier = extract_tier(n)
        result.setdefault(entry["icon"], {}).setdefault(tier, set()).add(identity(base))
    return result


def identities_for_icons(icon_map: Dict[Optional[str], set], icons: set, tier: Optional[str] = None) -> set:
    """Union of identities across the given icons -- across every tier if
    tier is None, else only that specific tier."""
    result: set = set()
    for icon in icons:
        tier_map = icon_map.get(icon, {})
        if tier is None:
            for s in tier_map.values():
                result |= s
        else:
            result |= tier_map.get(tier, set())
    return result


def parse_warrant_skills(text: str) -> Optional[List[str]]:
    """Returns the skill list in real on-screen top-to-bottom order, or
    None if this capture's warrant_generated.txt flagged a dropped/
    unrecognized row -- row position can't be trusted to line up with the
    fixed 6-row grid once an earlier row was silently dropped from the
    list (see build_warrant_extracted_text's docstring in
    capture_pipeline.py), so those captures are skipped entirely rather
    than risk mislabeling a crop with the wrong skill.

    Real, reproduced bug once supports got wired into
    build_warrant_extracted_text(): each skill's block is now `SkillName`
    followed by zero or more `SupportName (Tier: N)` lines before the
    next `--------` separator, but this function used to append EVERY
    non-separator line, treating each equipped support as if it were an
    additional skill. For a real 6-skill capture with, say, 2 supports on
    one skill and 1 on three others, that produced an 11-entry list
    instead of 6 -- every row_idx past the first skill then pointed at
    the WRONG skill (or a support name entirely) once handed to
    harvest_support_icons.py's row-indexed scan_row() calls, silently
    corrupting candidate narrowing for nearly every row. Caught via
    manifest.json anomalies (empty candidate set) and ambiguous-inferred
    entries whose `contributing_skills` were themselves support names
    like "Impale Chance (Tier: 2)" instead of real skill names -- not a
    hypothetical, confirmed directly against __captures_campaign's real
    warrant_generated.txt files. Fixed by only taking the FIRST line
    after each separator (the skill name itself) and skipping every
    other line until the next separator resets that expectation.
    """
    lines = text.strip("\n").split("\n")
    if any(_UNRECOGNIZED_TRAILER_RE.match(l) for l in lines):
        return None
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("Build:"))
    except StopIteration:
        return None
    i = start + 1
    if i < len(lines) and lines[i].startswith("Mercenary Level:"):
        i += 1
    skills = []
    expect_skill_name = True
    for l in lines[i:]:
        if l == "--------":
            expect_skill_name = True
            continue
        if l.startswith("Right click this item"):
            break
        if not l.strip():
            continue
        if expect_skill_name:
            skills.append(l)
            expect_skill_name = False
        # else: a support line under the skill just appended -- skip it
    return skills


def parse_warrant_ground_truth(text: str) -> List[Tuple[str, List[Tuple[str, int]]]]:
    """Parses a REAL `warrant.txt` (the raw copied item, not the OCR'd
    `warrant_generated.txt`) into [(skill_name, [(support_name, tier_int),
    ...]), ...], in real on-screen order for both skills and, within
    each skill, its equipped supports -- confirmed directly against a
    real capture that the on-screen LEFT-TO-RIGHT column order matches
    this listed order exactly. `support_name` is used as-is: it matches
    a `definitions/supports.json` key byte-for-byte (Lesser/Greater
    prefix included where the real support has one), no further
    stripping or lookup needed. A skill with no supports equipped
    (e.g. an aura with nothing linked) yields an empty list, not an
    absent entry -- row position still matters for the grid, so it
    can't just be skipped.

    Not OCR -- this is the exact text PoE's own item-copy feature
    produces, so unlike `parse_warrant_skills` there's no "dropped row"
    failure mode to guard against here.
    """
    lines = text.strip("\n").split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("Build:"))
    except StopIteration:
        return []
    i = start + 1
    if i < len(lines) and lines[i].startswith("Mercenary Level:"):
        i += 1
    blocks: List[Tuple[str, List[Tuple[str, int]]]] = []
    current_skill = None
    current_supports: List[Tuple[str, int]] = []
    expect_skill_name = False
    for l in lines[i:]:
        if l.startswith("Right click this item"):
            break
        if l == "--------":
            if current_skill is not None:
                blocks.append((current_skill, current_supports))
            current_skill = None
            current_supports = []
            expect_skill_name = True
            continue
        if not l.strip():
            continue
        if expect_skill_name:
            current_skill = l
            expect_skill_name = False
            continue
        m = _SUPPORT_LINE_RE.match(l)
        if m:
            current_supports.append((m.group(1), int(m.group(2))))
    return blocks


def find_capture_dirs() -> List[str]:
    patterns = [
        os.path.join(PROJECT_ROOT, "captures", "*"),
        os.path.join(PROJECT_ROOT, "__captures_*", "*"),
    ]
    dirs = []
    for pattern in patterns:
        for path in glob.glob(pattern):
            if os.path.isdir(path):
                dirs.append(path)
    return dirs


def crop_hash(crop: Image.Image) -> str:
    return hashlib.md5(crop.tobytes()).hexdigest()


def harvest():
    columns, rows = load_grid()

    with open(SUPPORTS_BY_SKILLS_PATH, encoding="utf-8") as f:
        skills = json.load(f)["skills"]
    with open(SUPPORTS_PATH, encoding="utf-8") as f:
        supports = json.load(f)["supports"]
    identity = build_identity_resolver(supports)
    identity_members = build_identity_members(supports, identity)
    tier_notes = load_tier_notes()
    manual_labels = load_manual_labels()

    capture_dirs = find_capture_dirs()

    # cluster_hash -> {"crop": Image, "occurrences": [(capture_dir, skill, row, col, ground_truth_name_or_None), ...]}
    clusters: Dict[str, dict] = {}
    skipped_no_data = 0
    skipped_dropped_rows = 0
    scanned_cells = 0
    occupied_cells = 0
    ground_truth_captures = 0
    inferred_captures = 0
    row_mismatches = 0

    def scan_row(im, brightness, row_idx, capture_dir, skill_name, ground_truth_names=None):
        """ground_truth_names: the exact literal support name for each
        occupied column, in order, when known -- None means 'infer later
        from PossibleSupports', same as before this ground-truth path
        existed."""
        nonlocal scanned_cells, occupied_cells
        if row_idx >= len(rows):
            return
        ry0, ry1 = rows[row_idx]
        for col_idx, (cx0, cx1) in enumerate(columns):
            scanned_cells += 1
            cell_brightness = brightness[ry0:ry1, cx0:cx1]
            if cell_brightness.std() < OCCUPIED_STD_THRESHOLD:
                continue
            occupied_cells += 1
            crop = im.crop((cx0, ry0, cx1, ry1))
            h = crop_hash(crop)
            gt_name = ground_truth_names[col_idx] if ground_truth_names and col_idx < len(ground_truth_names) else None
            if h not in clusters:
                clusters[h] = {"crop": crop, "occurrences": []}
            clusters[h]["occurrences"].append((capture_dir, skill_name, row_idx, col_idx, gt_name))

    for capture_dir in capture_dirs:
        supports_png = os.path.join(capture_dir, "supports.png")
        if not os.path.isfile(supports_png):
            skipped_no_data += 1
            continue

        real_warrant = os.path.join(capture_dir, "warrant.txt")
        ground_truth_blocks = None
        if os.path.isfile(real_warrant):
            with open(real_warrant, encoding="utf-8") as f:
                ground_truth_blocks = parse_warrant_ground_truth(f.read())
            if not any(supports for _, supports in ground_truth_blocks):
                ground_truth_blocks = None  # e.g. a warrant with zero linked supports anywhere

        im = Image.open(supports_png).convert("RGB")
        arr = np.array(im).astype(float)
        brightness = arr.mean(axis=2)

        if ground_truth_blocks is not None:
            ground_truth_captures += 1
            for row_idx, (skill_name, support_list) in enumerate(ground_truth_blocks):
                if row_idx >= len(rows):
                    break
                ry0, ry1 = rows[row_idx]
                occupied_idx = [
                    c for c, (cx0, cx1) in enumerate(columns)
                    if brightness[ry0:ry1, cx0:cx1].std() >= OCCUPIED_STD_THRESHOLD
                ]
                expected_idx = list(range(len(support_list)))
                if occupied_idx != expected_idx:
                    row_mismatches += 1
                    print(f"  WARNING: {capture_dir} row {row_idx} ({skill_name!r}) -- warrant.txt lists "
                          f"{len(support_list)} support(s) but occupied columns are {occupied_idx}, not "
                          f"{expected_idx}. Treating this row as un-grounded rather than mislabeling it.")
                    scan_row(im, brightness, row_idx, capture_dir, skill_name)
                    continue
                names = [n for n, _tier in support_list]
                scan_row(im, brightness, row_idx, capture_dir, skill_name, ground_truth_names=names)
            continue

        warrant_generated = os.path.join(capture_dir, "warrant_generated.txt")
        if not os.path.isfile(warrant_generated):
            skipped_no_data += 1
            continue
        with open(warrant_generated, encoding="utf-8") as f:
            skill_list = parse_warrant_skills(f.read())
        if skill_list is None:
            skipped_dropped_rows += 1
            continue
        inferred_captures += 1
        for row_idx, skill_name in enumerate(skill_list):
            scan_row(im, brightness, row_idx, capture_dir, skill_name)

    campaign_confirmed_names: Dict[str, str] = {}
    campaign_promotions = load_campaign_promotions()
    for full_hash, name in campaign_promotions.items():
        if full_hash in clusters:
            # Same exact crop also showed up in a real capture -- let that
            # occurrence's own resolution stand (ground truth or otherwise)
            # rather than silently overriding it; the promotion becomes
            # redundant, not lost (it's still recorded in
            # campaign_promotions.json for anyone reading it by hand).
            print(f"  NOTE: promoted campaign hash {full_hash} also appears in a real capture -- "
                  f"using that occurrence's own resolution, not the promotion.")
            continue
        crop_path = os.path.join(CAMPAIGN_PROMOTED_DIR, f"{full_hash}.png")
        if not os.path.isfile(crop_path):
            print(f"  WARNING: campaign_promotions.json names {name!r} for hash {full_hash}, but its "
                  f"promoted crop file is missing at {crop_path} -- re-run promote_campaign_truth.py. Skipping.")
            continue
        if name not in supports:
            print(f"  WARNING: campaign_promotions.json names {name!r} for hash {full_hash}, but that's "
                  f"not a real definitions/supports.json key -- check for a typo. Skipping.")
            continue
        crop = Image.open(crop_path).convert("RGB")
        recomputed_hash = crop_hash(crop)
        if recomputed_hash != full_hash:
            print(f"  WARNING: promoted crop file {crop_path} hashes to {recomputed_hash}, not the "
                  f"{full_hash} campaign_promotions.json expects -- file may be corrupted or "
                  f"mismatched. Skipping.")
            continue
        clusters[full_hash] = {
            "crop": crop,
            "occurrences": [("assets/campaign_review (promoted)", "(campaign shadow ground truth)", 0, 0, None)],
        }
        campaign_confirmed_names[full_hash] = name

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    # Clear PNGs (and the variants/ subfolder) from a previous run before
    # writing this one's -- otherwise a crop that gets a different (better)
    # label this time (e.g. once ground-truth data exists for it, or a
    # re-clustering shifts a hash suffix) leaves its old filename orphaned
    # on disk forever, alongside the new one. tier_notes.json and
    # manifest.json are untouched here (manifest.json is overwritten below
    # regardless; tier_notes.json is hand-maintained and never touched by
    # this script).
    for f in os.listdir(OUTPUT_DIR):
        if f.endswith(".png"):
            os.remove(os.path.join(OUTPUT_DIR, f))
    if os.path.isdir(VARIANTS_DIR):
        shutil.rmtree(VARIANTS_DIR)
    manifest = []
    labeled_count = 0

    anomaly_count = 0
    conflict_count = 0
    ground_truth_labeled_count = 0
    manual_labeled_count = 0
    embedding_confirmed_count = 0
    campaign_confirmed_count = 0
    tier_narrowed_count = 0
    for h, data in clusters.items():
        contributing_skills = sorted({occ[1] for occ in data["occurrences"]})
        ground_truth_labels = {occ[4] for occ in data["occurrences"] if occ[4] is not None}

        source = "inferred"
        anomaly = False
        candidates: set = set()
        tier = None
        tier_narrowed = False
        identity_value = None
        display_name = None
        visual_key = None

        campaign_confirmed_name = campaign_confirmed_names.get(h)

        if campaign_confirmed_name is not None:
            # A human read this crop's exact name/tier straight off the
            # real in-game tooltip during a sub-68 campaign encounter --
            # zero icon-matching involvement anywhere (see this module's
            # docstring and tools/promote_campaign_truth.py) -- trusted
            # with the same confidence as a real warrant.txt label, just
            # from a different real source. This only ever fires for a
            # cluster promote_campaign_truth.py created fresh above; a
            # hash that also appears in a real capture already resolves
            # via ground_truth_labels below and never reaches here.
            source = "campaign_confirmed"
            display_name = campaign_confirmed_name
            identity_value = identity(campaign_confirmed_name) if campaign_confirmed_name in supports else None
            visual_key = visual_key_for_name(campaign_confirmed_name, supports)
            campaign_confirmed_count += 1
        elif len(ground_truth_labels) == 1:
            # A real warrant.txt named this crop directly -- no inference
            # needed, no candidate list, just the exact literal
            # definitions/supports.json key as written in the item text.
            source = "ground_truth"
            display_name = next(iter(ground_truth_labels))
            identity_value = identity(display_name) if display_name in supports else None
            visual_key = visual_key_for_name(display_name, supports)
            ground_truth_labeled_count += 1
        elif len(ground_truth_labels) > 1:
            # NOT a bug: checked directly against real data -- every real
            # case of this is two+ different real supports that genuinely
            # share one icon+tier (e.g. `Gilded Jolt`/`Gilded Hopelessness`/
            # `Gilded Secondary Shots`, all `mercgoldsupportgem`), just
            # confirmed here by ground truth from DIFFERENT skills' real
            # warrant.txt text rather than inferred from PossibleSupports.
            # That makes this the single most reliable candidate list this
            # script can produce -- proven to have actually occurred, not
            # merely structurally possible -- so it's treated as an
            # ambiguous result with real candidates, same as the inference
            # path, not as an error.
            source = "ground_truth_ambiguous"
            candidates = set(ground_truth_labels)
            conflict_count += 1
            keys = {visual_key_for_name(c, supports) for c in candidates}
            if len(keys) == 1:
                visual_key = next(iter(keys))
            else:
                # Every real case checked shares one icon+tier -- if this
                # ever fires it means two candidates that hash-identical
                # somehow have different tiers, which should be
                # impossible (a different roman numeral is different
                # pixels), so surface it loudly rather than picking one.
                print(f"  WARNING: cluster {h}'s ground-truth-ambiguous candidates "
                      f"{sorted(candidates)} don't even agree on icon+tier ({keys}) -- "
                      f"investigate, this should never happen.")
        else:
            # No warrant.txt ground truth touched this cluster at all --
            # fall back to the original candidate-narrowing inference.
            icon_maps = [
                icon_identity_map_for_skill(skill, skills, supports, identity) for skill in contributing_skills
            ]
            icon_maps = [m for m in icon_maps if m is not None]
            common_icons = set.intersection(*[set(m.keys()) for m in icon_maps]) if icon_maps else set()

            if len(common_icons) == 0:
                # Every contributing skill's PossibleSupports pool should share
                # SOME icon, by construction (they all produced the same real
                # pixel-identical crop) -- an empty result here means either a
                # real gap in definitions/supports_by_skills.json for one of
                # these skills, or a row/skill mis-attribution bug, not a
                # genuine "no candidates" case. Surfaced, not silently dropped.
                anomaly = True
                anomaly_count += 1
            else:
                for m in icon_maps:
                    candidates |= identities_for_icons(m, common_icons)

            tier = resolve_tier_note(h, tier_notes)

            manual_name = None
            if not anomaly:
                manual_name = resolve_manual_label(h, manual_labels, supports, common_icons, tier)

            embedding_visual_key, embedding_matches = None, []
            if manual_name is None and not anomaly and len(candidates) >= 1:
                # >= 1, not > 1: even a single candidate IDENTITY doesn't
                # pin down the TIER if that identity has members at more
                # than one tier (e.g. "PhysGainAs") and no tier_notes.json
                # entry exists -- the classifier's own tier-badge reading
                # can still resolve that case, and gating this on > 1
                # meant it never even got tried (real bug, found via the
                # manual review page showing a single-candidate crop that
                # still wasn't auto-resolved).
                embedding_visual_key, embedding_matches = resolve_via_embedding(
                    data["crop"], candidates, common_icons, supports, identity)
            embedding_resolved = len(embedding_matches) == 1
            embedding_collision = len(embedding_matches) > 1

            if manual_name is not None:
                # A human visually identified this crop directly -- real
                # icon content, real name, something image-matching code
                # can't do yet. Already checked against this cluster's
                # own structural data in resolve_manual_label, so it's
                # trusted with the same confidence as ground truth, not
                # treated as just another candidate.
                source = "manual"
                display_name = manual_name
                identity_value = identity(manual_name) if manual_name in supports else None
                visual_key = visual_key_for_name(manual_name, supports)
                manual_labeled_count += 1
            elif embedding_resolved:
                # capture_pipeline.match_support_icon() confidently
                # recognized the actual icon+tier, and it agrees with what
                # text-based narrowing already knows is structurally
                # possible (see resolve_via_embedding's docstring) -- no
                # human needed for this one, unlike the manual/inferred
                # paths above and below it.
                embedding_name, embedding_tier_roman = embedding_matches[0]
                if tier is not None and tier != embedding_tier_roman:
                    print(f"  WARNING: tier_notes.json says tier {tier!r} for hash {h}, but the "
                          f"embedding classifier confidently resolved it as {embedding_name!r} "
                          f"(tier {embedding_tier_roman!r}) instead -- trusting the embedding "
                          f"(already validated at 100%, see tools/match_support_icon.py), but "
                          f"double check this tier_notes.json entry for a possible misread.")
                source = "embedding_confirmed"
                display_name = embedding_name
                identity_value = identity(embedding_name)
                visual_key = visual_key_for_name(embedding_name, supports)
                embedding_confirmed_count += 1
            elif embedding_collision:
                # Not a disagreement -- EVERY tied member is structurally
                # possible for the contributing skill(s), meaning the
                # classifier has pinned down the exact icon+tier and it
                # genuinely renders more than one real support identically
                # (e.g. Minion Damage / Minion Life). This is the SAME
                # category as a ground-truth-discovered collision, not
                # something more inference or a human could ever resolve
                # further -- setting visual_key here (and narrowing
                # `candidates` to just these tied identities, discarding
                # the broader per-skill set that could include totally
                # unrelated icons the same skill happens to also offer)
                # lets the existing by_key merge pass below fold this in
                # with any other real occurrence of the same visual_key
                # and correctly reclassify it as visual_key_ambiguous,
                # instead of leaving it looking like it needs a human's
                # help choosing between irrelevant options it was never
                # actually being asked to choose between.
                visual_key = embedding_visual_key
                candidates = {identity(name) for name, _tier_roman in embedding_matches}
            else:
                if tier and not anomaly:
                    # A human read the numeral off the real crop (see tier_notes.json
                    # in this module's docstring) -- narrow to only identities the
                    # contributing skill(s) actually offer AT THAT SPECIFIC TIER,
                    # intersected with the untiered candidates as a safety net (a
                    # bad hand-entered tier should never ADD a candidate that
                    # wasn't already structurally possible).
                    narrowed: set = set()
                    for m in icon_maps:
                        narrowed |= identities_for_icons(m, common_icons, tier=tier)
                    narrowed &= candidates
                    if narrowed:
                        candidates = narrowed
                        tier_narrowed = True

            if manual_name is None and not embedding_resolved and not embedding_collision and len(candidates) == 1:
                resolved_identity = next(iter(candidates))
                members = identity_members.get(resolved_identity, [resolved_identity])
                # Knowing the FAMILY isn't the same as knowing the TIER --
                # identity() collapses tier away entirely, so a resolved
                # single-candidate identity only pins down a specific,
                # single, correctly-tiered reference when the tier is
                # ALSO known: either a human confirmed it (tier_narrowed)
                # or this identity has exactly one real member at all (a
                # standalone entry, so there's no OTHER tier it could be).
                # Skipping this check would have been a real latent bug:
                # two genuinely different tiers of one family (e.g. tier
                # II "Fire Penetration" and tier III "Greater Fire
                # Penetration") both resolving to the SAME identity would
                # get the SAME display name and get merged as if one were
                # just a noisy near-duplicate of the other, which they are
                # not.
                if tier_narrowed:
                    resolved_tier_roman = tier
                elif len(members) == 1:
                    resolved_tier_roman = supports.get(members[0], {}).get("tier_roman")
                else:
                    resolved_tier_roman = None

                if resolved_tier_roman is not None:
                    # Real, reproduced bug: `members[0]` is just
                    # alphabetically first (build_identity_members sorts
                    # them), which can be a Gilded member of this family
                    # -- canonical_display_name() already special-cases
                    # "Gilded " out for the display NAME (see its own
                    # docstring), but this icon lookup never got the same
                    # fix. Confirmed on "IncreaseAreaOfEffect": alphabetically
                    # first member is "Gilded Area per Projectile" (icon
                    # mercgoldsupportgem, tier III only), so a tier-I
                    # resolution here produced label "Increased Area of
                    # Effect" (correctly Gilded-excluded) filed under
                    # visual_key "mercgoldsupportgem_i" (WRONG icon, and a
                    # tier that member doesn't even have) instead of the
                    # real "increasedaoe_i". Fixed by finding the specific
                    # member that actually exists at resolved_tier_roman
                    # and using ITS icon -- if more than one member shares
                    # that exact tier (a real family-level collision, e.g.
                    # a Gilded and a plain entry both landing on tier III),
                    # that's genuine ambiguity and stays unresolved rather
                    # than guessing between them.
                    #
                    # Second real bug in the same spirit, found via the
                    # manual review page: this filtered by tier alone, not
                    # icon -- a family whose members span more than one
                    # icon (e.g. "Duration" covers `increasedduration`
                    # ["More Duration"] and `reduceduration` ["Less
                    # Duration"], opposite effects sharing a family label)
                    # can have one member of EACH icon land on the exact
                    # same tier, so this refused to resolve even when only
                    # ONE of those icons was actually possible for the
                    # contributing skill(s). Fixed by also requiring the
                    # member's icon to be in `common_icons` -- the same
                    # structural fact resolve_via_embedding() and
                    # generate_manual_labeling_page.py already check.
                    tier_matches = [m for m in members
                                    if supports.get(m, {}).get("tier_roman") == resolved_tier_roman
                                    and supports.get(m, {}).get("icon") in common_icons]
                    if len(tier_matches) == 1:
                        identity_value = resolved_identity
                        display_name = canonical_display_name(identity_value, members)
                        icon = supports.get(tier_matches[0], {}).get("icon")
                        visual_key = f"{icon}_{resolved_tier_roman.lower()}" if icon else None
                        labeled_count += 1
                        if tier_narrowed:
                            tier_narrowed_count += 1
                    # else: more than one member of this family actually
                    # exists at this tier -- ambiguous, stays unresolved.
                # else: family is known but tier isn't -- stays in the
                # ambiguous bucket below rather than guessing a tier.

        candidate_detail = None
        if display_name is None and not anomaly:
            if source == "ground_truth_ambiguous":
                # candidates are already exact literal supports.json names
                # here (confirmed real ones, not identity codes needing
                # resolution) -- use them as-is rather than running them
                # through canonical_display_name, which is only meaningful
                # for the inferred path's identity-code candidates.
                candidate_detail = [
                    {"identity": identity(c), "name": c} for c in sorted(candidates)
                ]
            else:
                candidate_detail = [
                    {"identity": c, "name": canonical_display_name(c, identity_members.get(c, [c]))}
                    for c in sorted(candidates)
                ]

        manifest.append({
            "hash": h,
            "label": display_name,
            "identity": identity_value,
            "visual_key": visual_key,
            "source": source,
            "candidates": candidate_detail,
            "tier": tier,
            "tier_narrowed": tier_narrowed,
            "anomaly": anomaly,
            "occurrence_count": len(data["occurrences"]),
            "contributing_skills": contributing_skills,
            "file": None,  # filled in below, once every cluster's visual_key is known
            "example": {
                "capture": os.path.relpath(data["occurrences"][0][0], PROJECT_ROOT).replace("\\", "/"),
                "skill": data["occurrences"][0][1],
                "row": data["occurrences"][0][2],
                "col": data["occurrences"][0][3],
            },
        })

    # Write crops to disk only now that every cluster's visual_key is known
    # -- needed to tell, for a given key, which of possibly several
    # same-keyed clusters (e.g. capture-noise near-duplicates of the exact
    # same real icon+tier, confirmed real on "Added Cold") is the one that
    # becomes the main reference vs. a variant. Filenames use `visual_key`
    # (GGG's own icon basename + tier, e.g. "firepenetration_iii") rather
    # than a re-slugified display name -- keeps this aligned with how
    # definitions/supports.json's own "icon" field is written, instead of
    # introducing a second, inconsistent naming convention. `label` (the
    # human-readable support/family name) stays in the manifest for
    # anyone reading it, just not baked into the filename. The most
    # commonly-seen rendering (highest occurrence_count) is kept as the
    # primary `<visual_key>.png`; any others move to
    # `variants/<visual_key>/` instead of cluttering the top level with
    # near-identical files under the same name.
    by_key: Dict[str, List[dict]] = defaultdict(list)
    for entry in manifest:
        if entry["visual_key"] is not None:
            by_key[entry["visual_key"]].append(entry)

    visual_key_ambiguous_group_count = 0
    for visual_key, entries in by_key.items():
        # Real bug caught on Minion Damage/Minion Life sharing
        # `miniondamage_ii`: entries reaching the same visual_key can come
        # from DIFFERENT exact-hash clusters (confirmed real capture jitter
        # of 1-2px keeps genuinely-identical crops from hashing equal), so
        # each cluster can independently see only ONE ground-truth label by
        # chance of small sample size -- the earlier "conflicting labels
        # within one exact hash" check has nothing to catch there. Blindly
        # merging same-visual_key entries as "primary + variants" (as an
        # earlier version of this did, for the real but different case of
        # same-label capture noise on "Added Cold") would silently pick
        # whichever label had the most occurrences and hide a real,
        # proven ambiguity as if it were a settled answer. Checked here
        # instead, one level up from the per-hash check: if this
        # visual_key's entries don't all agree on label, the WHOLE group
        # is reclassified as ambiguous -- the icon+tier reference image is
        # still correct and worth keeping, just not attributed to one
        # specific real support.
        # An entry that's ALREADY ambiguous (ground_truth_ambiguous, from
        # the earlier within-one-exact-hash check) has label=None, but its
        # own candidates are still real, proven names for this icon+tier
        # -- excluding them here would miss real cases where some OTHER
        # instance of the same icon+tier confidently resolved to just ONE
        # of those names (confirmed real: `mercsilverstrintsupportgem_iii`
        # had 2 entries confidently labeled "Greater Physical as Extra"
        # sharing this visual_key with 2 already-ambiguous entries proving
        # "Greater Ironwood" also occurs there -- missing this would leave
        # the merged primary file looking falsely confident).
        labels = set()
        for e in entries:
            if e["label"] is not None:
                labels.add(e["label"])
            elif e["candidates"]:
                labels.update(c["name"] for c in e["candidates"])
        if len(labels) > 1:
            visual_key_ambiguous_group_count += 1
            for e in entries:
                e["source"] = "visual_key_ambiguous"
                e["label"] = None
                e["identity"] = None
                e["candidates"] = [
                    {"identity": identity(name), "name": name} for name in sorted(labels)
                ]

        entries.sort(key=lambda e: (-e["occurrence_count"], e["hash"]))
        primary = entries[0]
        primary_filename = f"{visual_key}.png"
        clusters[primary["hash"]]["crop"].save(os.path.join(OUTPUT_DIR, primary_filename))
        primary["file"] = primary_filename

        if len(entries) > 1:
            variant_dir = os.path.join(VARIANTS_DIR, visual_key)
            os.makedirs(variant_dir, exist_ok=True)
            for entry in entries[1:]:
                variant_filename = f"{visual_key}_{entry['hash'][:6]}.png"
                clusters[entry["hash"]]["crop"].save(os.path.join(variant_dir, variant_filename))
                entry["file"] = f"variants/{visual_key}/{variant_filename}"

    # Recomputed fresh from final `source` rather than trusting the
    # running counters from the per-cluster loop above -- those can't
    # know about a reclassification that only happens here, one pass
    # later, once every cluster's visual_key is known.
    ground_truth_labeled_count = sum(1 for e in manifest if e["source"] == "ground_truth")
    manual_labeled_count = sum(1 for e in manifest if e["source"] == "manual")
    embedding_confirmed_count = sum(1 for e in manifest if e["source"] == "embedding_confirmed")
    campaign_confirmed_count = sum(1 for e in manifest if e["source"] == "campaign_confirmed")
    labeled_count = sum(1 for e in manifest if e["source"] == "inferred" and e["label"] is not None)
    conflict_count = sum(1 for e in manifest if e["source"] == "ground_truth_ambiguous")
    visual_key_ambiguous_entry_count = sum(1 for e in manifest if e["source"] == "visual_key_ambiguous")
    inferred_ambiguous_count = sum(1 for e in manifest if e["source"] == "inferred" and e["label"] is None)

    for entry in manifest:
        if entry["file"] is None:  # no resolvable icon+tier at all yet
            entry["file"] = f"unlabeled_{entry['hash'][:10]}.png"
            clusters[entry["hash"]]["crop"].save(os.path.join(OUTPUT_DIR, entry["file"]))

    manifest.sort(key=lambda m: (m["visual_key"] is None, m["visual_key"] or "", -m["occurrence_count"]))
    manifest_path = os.path.join(OUTPUT_DIR, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({
            "note": "AUTOGENERATED FILE -- DO NOT MODIFY. Re-run harvest_support_icons.py "
                     "to regenerate as more real captures accumulate. To help narrow an "
                     "'unlabeled' cluster, read the tier off its saved crop and add "
                     "{\"<hash>\": \"I\"|\"II\"|\"III\"} to tier_notes.json (hand-maintained, "
                     "next to this file, never touched by this script) -- see harvest_support_icons.py's "
                     "module docstring.",
            "clusters": manifest,
        }, f, indent=2, ensure_ascii=False)

    if not os.path.isfile(TIER_NOTES_PATH):
        with open(TIER_NOTES_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump({}, f, indent=2)
    if not os.path.isfile(MANUAL_LABELS_PATH):
        with open(MANUAL_LABELS_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump({}, f, indent=2)

    print(f"Capture directories found: {len(capture_dirs)}")
    print(f"  used ground-truth warrant.txt: {ground_truth_captures}")
    print(f"  used inferred warrant_generated.txt (no usable warrant.txt): {inferred_captures}")
    print(f"  skipped (no supports.png / no warrant data at all): {skipped_no_data}")
    print(f"  skipped (dropped/unrecognized skill row in warrant_generated.txt): {skipped_dropped_rows}")
    if row_mismatches:
        print(f"  WARNING: {row_mismatches} row(s) had a warrant.txt support count that didn't match "
              f"the occupied columns on screen -- see warnings above, treated as un-grounded, not guessed.")
    print(f"Cells scanned: {scanned_cells}, occupied: {occupied_cells}")
    print(f"Unique (icon, tier) crops found: {len(clusters)}")
    print(f"  labeled from ground truth (warrant.txt text, zero ambiguity): {ground_truth_labeled_count}")
    print(f"  labeled from manual_labels.json (visual ID, human-confirmed): {manual_labeled_count}")
    print(f"  confirmed via capture_pipeline's embedding classifier (agrees with structurally-possible "
          f"candidates, no human needed): {embedding_confirmed_count}")
    print(f"  promoted from the campaign shadow-ground-truth (real tooltip read, zero icon-matching, "
          f"see tools/promote_campaign_truth.py): {campaign_confirmed_count}")
    print(f"  confidently auto-labeled by inference: {labeled_count} ({tier_narrowed_count} of those via a tier_notes.json entry)")
    print(f"  ambiguous, ground-truth-CONFIRMED real candidates, caught within one exact hash (two+ "
          f"different real supports genuinely share this icon+tier -- proven, not guessed): {conflict_count}")
    if visual_key_ambiguous_group_count:
        print(f"  ambiguous, ground-truth-CONFIRMED real candidates, caught across near-duplicate hashes "
              f"of the same icon+tier (same discovery, found one merge-pass later -- e.g. Minion Damage/"
              f"Minion Life): {visual_key_ambiguous_entry_count} entries across "
              f"{visual_key_ambiguous_group_count} distinct icon+tier groups")
    print(f"  ambiguous, inferred candidate list (needs a human or a tier read): {inferred_ambiguous_count}")
    print(f"  anomalies (empty candidate set -- worth investigating, not just accepting): {anomaly_count}")
    print(f"Output written to {OUTPUT_DIR} (manifest.json has full detail per cluster; "
          f"tier_notes.json is yours to edit)")


if __name__ == "__main__":
    harvest()
