"""
PoE Mercenary Encounter Capture Pipeline
-----------------------------------------
Hotkey-triggered capture -> crop into named regions -> extraction stubs -> log.

Setup:
    See DEPENDENCIES.md for full details (pip packages + the separate
    Tesseract OCR engine install, which pip cannot install for you).
    Quick version: pip install -r requirements.txt, then install
    Tesseract OCR separately and set TESSERACT_CMD below if needed.

Run:
    python capture_pipeline.py
    (press F9 while the mercenary encounter window is open and paused)
    (press ESC to quit)

This does NOT send any input to the game. It only reads pixels already on
your screen when you press the hotkey -- it's a passive screen-reader, not
a macro or automation tool.
"""

import json
import csv
import difflib
import os
import re
import shutil
import threading
import time
from datetime import datetime

import numpy as np
from scipy import ndimage
import cv2
import mss
from PIL import Image, ImageOps
import pytesseract
import keyboard  # global hotkey listener

try:
    import pyperclip
    _CLIPBOARD_AVAILABLE = True
except ImportError:
    _CLIPBOARD_AVAILABLE = False

CONFIG_PATH = "definitions/mercenary_regions.json"
CAPTURE_DIR = "captures"
LOG_PATH = "logs/mercenary_log.tsv"
HOTKEY = "f9"

# Path of Exile's live log file -- used to look up which map the most
# recent encounter happened in. This is the standard default install
# location; change it if PoE is installed elsewhere.
CLIENT_LOG_PATH = r"C:\Program Files (x86)\Grinding Gear Games\Path of Exile\logs\Client.txt"

# Fixed TSV/console column set.
LOG_FIELDNAMES = ["Name", "Type", "Infamous", "Gem 1", "Gem 2", "Gem 3", "Gem 4", "Map", "Exceptions"]

# Optional manual override -- leave as None to auto-detect (see
# _resolve_tesseract_cmd below). Only set this if Tesseract is installed
# somewhere non-standard and auto-detection fails.
TESSERACT_CMD = None  # e.g. r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Standard install locations per OS, checked in order if TESSERACT_CMD
# isn't set and Tesseract isn't already resolvable on PATH. Keeping this
# as a static list (rather than something that needs hand-editing per
# machine) is what lets the same downloaded script work without
# modification across different setups.
_DEFAULT_TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "/opt/homebrew/bin/tesseract",  # macOS, Homebrew on Apple Silicon
    "/usr/local/bin/tesseract",     # macOS, Homebrew on Intel / manual installs
    "/usr/bin/tesseract",           # Linux package managers
]


def _resolve_tesseract_cmd():
    if TESSERACT_CMD:
        return TESSERACT_CMD
    on_path = shutil.which("tesseract")
    if on_path:
        return on_path
    for path in _DEFAULT_TESSERACT_PATHS:
        if os.path.isfile(path):
            return path
    return None


def _require_tesseract_cmd():
    resolved = _resolve_tesseract_cmd()
    if not resolved:
        raise SystemExit(
            "Could not find Tesseract OCR.\n\n"
            "Checked PATH and the standard install locations for "
            "Windows/macOS/Linux, but none had it.\n\n"
            "Tesseract OCR is a separate program from the 'pytesseract' pip "
            "package -- pip cannot install it for you. Before running this "
            "script:\n"
            "  1. Read DEPENDENCIES.md for how to install Tesseract OCR and "
            "the Python packages (requirements.txt).\n"
            "  2. If you installed it somewhere non-standard, set "
            "TESSERACT_CMD at the top of capture_pipeline.py to the full "
            "path of your tesseract executable.\n"
        )
    pytesseract.pytesseract.tesseract_cmd = resolved


_require_tesseract_cmd()

# Set to False by tests.py (and anything else that deliberately exercises
# lots of intentional "didn't confidently match" cases -- fuzzy-match
# tolerance for OCR noise is a real, expected feature of this pipeline,
# not a bug) so those NOTEs don't clutter a pass/fail report that already
# has its own clear XX/ok markers for genuine regressions. Never changes
# what any function returns, only whether it prints. Live capture use
# leaves this True (the default) so a real capture's console output still
# shows everything.
NOTES_ENABLED = True


def _note(message: str) -> None:
    if NOTES_ENABLED:
        print(f"  NOTE: {message}")


# How far to inset each crop box (in px) to avoid catching any UI border /
# stray pixels right at the edge. Purely cosmetic safety margin.
INSET = 2


def load_regions(path=CONFIG_PATH):
    with open(path) as f:
        cfg = json.load(f)
    return cfg["regions"], cfg["calibration_resolution"]


def box_tuple(box, inset=INSET):
    return (box["x0"] + inset, box["y0"] + inset, box["x1"] - inset, box["y1"] - inset)


def build_crop_plan(regions):
    """
    Flattens the region config into a simple {name: (x0,y0,x1,y1)} plan
    of the actual leaf regions we want to crop out of a full capture.
    """
    plan = {
        "name": box_tuple(regions["name"]["absolute"]),
        "type_subtype": box_tuple(regions["type_subtype"]["absolute"]),
        "level": box_tuple(regions["level"]["absolute"]),
        "equipment_grid": box_tuple(regions["equipment_grid"]["absolute"]),
        "skills": box_tuple(regions["skills"]["absolute"]),
        "supports": box_tuple(regions["supports"]["absolute"]),
    }
    for qname, qbox in regions["rucksack"]["quadrants"].items():
        plan[f"rucksack_{qname}"] = box_tuple(qbox)
    return plan


def capture_full_screen(monitor_index=1):
    """Grabs the current screen straight into memory -- no file write."""
    with mss.MSS() as sct:
        monitor = sct.monitors[monitor_index]
        raw = sct.grab(monitor)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


# ---------------------------------------------------------------------------
# Extraction stubs -- fill these in as you build out OCR / template matching.
# Keeping them as separate functions means you can swap in local CV,
# the Claude API, or a mix, without touching the capture/crop plumbing.
# ---------------------------------------------------------------------------

_WORD_RUN_RE = re.compile(r"[A-Za-z][A-Za-z,.\- ]*[A-Za-z]")
# Every mercenary name follows "Name, the Title" -- Tesseract has been seen
# to misread the comma as a period ("Pahuto. the") and, separately, to
# drop the space after a correctly-read comma ("Zari,the"). Since the
# comma-then-space is a fixed, known pattern here (not something that
# varies name to name), normalizing both failure modes to ", the" is
# safe rather than guessing at general punctuation/spacing correction.
_NAME_COMMA_FIX_RE = re.compile(r"^(\w+)[.,]\s*the\b")
# The "Infamous" badge icon (crossed-blades art, seen on Ambusher-class
# mercenaries) has a sub-shape that occasionally survives the letter-
# shape filter in _isolate_text_row and gets OCR'd as a single stray
# leading character (observed twice now, always immediately before
# "Infamous"). Pixel-level fixes for this were tried and rejected: the
# icon-to-text gap is *smaller* than legitimate internal word-space gaps
# seen elsewhere, so no gap threshold can exclude the icon without also
# fragmenting real multi-word text. "Infamous" never legitimately has
# anything but whitespace before it in this field, so stripping a lone
# leading letter here is safe and targeted rather than fighting the
# image heuristics further.
_STRAY_ICON_PREFIX_RE = re.compile(r"^[A-Za-z]\s+(?=Infamous\b)")


def _isolate_text_row(crop: Image.Image):
    """Finds the real text glyphs within a region crop and returns an
    upscaled, binarized image ready for OCR.

    Deliberately optimized for recall over precision: crop generously
    enough that a real character is (almost) never clipped, even at the
    cost of occasionally including a stray extra word from background
    art or an adjacent HUD element. A missing character is an invisible
    failure -- the logged name is just quietly wrong, and nothing
    flags it. An extra word is a visible one -- a human glancing at the
    name field can immediately spot "Xyz Porua Apara" and delete "Xyz ",
    the same way stray icon-leak prefixes on the type field already get
    handled today.

    This replaces a much more elaborate precision-oriented approach
    (anchor on the tallest letter, exclude by dark-backing window,
    cluster by baseline and height, ...) that was iterated on
    extensively and ultimately reverted: each refinement fixed the
    specific capture in front of it while clipping a real character in
    a previously-working one, because the signals that looked
    discriminating in one capture (a gap size, a height ratio, a
    baseline) had a counterexample in another. Given a human is
    already expected to review the name field's output over time (see
    the "name isn't a functional field, review as issues come up"
    decision this followed), biasing toward "never lose a real
    character" and letting extra noise be manually culled is a more
    robust trade than continuing to chase precision.
    """
    arr = np.array(crop.convert("RGB")).astype(float) / 255.0
    gray = arr.mean(axis=2)
    ink_mask = gray > 0.45

    labeled, n = ndimage.label(ink_mask, structure=np.ones((3, 3)))
    if n == 0:
        return None

    # Still filter to letter-like shapes (not much wider than tall, not
    # a tiny antialiasing speck) -- this excludes the thin decorative
    # flourish line above the name banner (a long, only a few px thick
    # shape fails this test) without risking any real character, since
    # a real letter's own bounding box always passes it.
    objs = ndimage.find_objects(labeled)
    letter_like = []
    for i, sl in enumerate(objs):
        if sl is None:
            continue
        h = sl[0].stop - sl[0].start
        w = sl[1].stop - sl[1].start
        if h >= 4 and w <= h * 1.3:
            letter_like.append(sl)
    if not letter_like:
        return None

    # The full vertical union of every letter-like component -- real
    # text and any background noise/HUD bleed alike -- so a real
    # character's ascender or descender is never clipped. No X-axis
    # exclusion at all: the crop stays the full width of the region.
    y0 = min(sl[0].start for sl in letter_like)
    y1 = max(sl[0].stop for sl in letter_like)
    pad_y = 3
    band_y0 = max(0, y0 - pad_y)
    band_y1 = min(arr.shape[0], y1 + pad_y)

    text_crop = crop.crop((0, band_y0, crop.width, band_y1))
    scale = 4
    text_crop = text_crop.resize((text_crop.width * scale, text_crop.height * scale), Image.LANCZOS)
    gray_c = np.array(ImageOps.grayscale(text_crop))
    bw = np.where(gray_c > 100, 0, 255).astype(np.uint8)
    return Image.fromarray(bw)


def extract_text(crop: Image.Image) -> str:
    """OCR a text region (name, type/subtype).

    Applies glyph-isolation preprocessing (see _isolate_text_row, which
    is deliberately biased toward including everything real rather than
    excluding everything fake), then cleans Tesseract's raw output:
    strips stray leading/trailing symbols by keeping only the longest
    run of letters/spaces/comma/period/hyphen, and fixes the
    "Name. the Title" -> "Name, the Title" misread that Tesseract makes
    consistently on this font's comma glyph.

    --psm 11 (sparse text, no particular reading order) tried first,
    rather than --psm 7 (assume a single text line) -- the crop is
    deliberately wide/generous and can contain multiple disconnected
    clusters of text (the real name plus whatever background noise or
    HUD elements ended up in frame), and --psm 11 usually finds the real
    text as one of its (possibly several, newline-separated) detected
    fragments, with the longest-word-run regex below picking that
    fragment out.

    Falls back to --psm 7 on the SAME processed crop if --psm 11 finds
    nothing at all. Confirmed with a real capture (two crops of the same
    mercenary name, byte-identical in the actual text-glyph pixels,
    differing only in incidental background-art noise at the crop's
    edges): --psm 11 returned a completely empty result on one of them,
    deterministically (retried 3x, same empty result every time), while
    --psm 7 recovered the correct name immediately on that exact same
    failing crop. So neither mode is reliably strictly better than the
    other on every real capture -- trying both, in this order, costs a
    second Tesseract call only in the (otherwise total-failure) case
    where the first finds nothing.

    Remaining rare per-letter misreads (e.g. y/v confusion on some
    stylized capitals) are expected -- catch those with the known-values
    list from the build plan (Phase 2) rather than fighting the OCR
    engine further; that also flags anything genuinely unrecognized for
    manual review instead of silently logging a bad string.
    """
    processed = _isolate_text_row(crop)
    if processed is None:
        return None
    for psm in ("11", "7"):
        raw = pytesseract.image_to_string(processed, config=f"--psm {psm}").strip()
        matches = _WORD_RUN_RE.findall(raw)
        if matches:
            break
    else:
        return None
    best = max(matches, key=len).strip()
    best = _NAME_COMMA_FIX_RE.sub(r"\1, the", best)
    best = _STRAY_ICON_PREFIX_RE.sub("", best)
    return best or None


_INFAMOUS_PREFIX_RE = re.compile(r"^Infamous\s+", re.IGNORECASE)


KNOWN_TYPES_PATH = "definitions/mercenary_types.json"
_known_types_cache = None


def _load_known_mercenary_types(path: str = KNOWN_TYPES_PATH):
    """Loads the full list of valid type+infamy combinations as they
    actually occur (definitions/mercenary_types.json) -- e.g. "Sniper",
    "Infamous Sniper", but only "Infamous Warpriest of the Ruckus" with
    no bare counterpart (that mercenary is always Infamous), and only
    bare "Flamehand" with no Infamous counterpart (that one never is).
    A plain list of 34 base type names can't represent this -- a type
    isn't simply "has_infamy: true/false", since some types are
    Infamous-only and others can never be Infamous at all (confirmed
    directly, not assumed) -- so the valid combinations are enumerated
    explicitly here instead of derived from a boolean flag."""
    global _known_types_cache
    if _known_types_cache is not None:
        return _known_types_cache
    if not os.path.isfile(path):
        _known_types_cache = []
        return _known_types_cache
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    _known_types_cache = data.get("types", [])
    return _known_types_cache


_known_base_types_cache = None


def _load_known_mercenary_base_types(path: str = KNOWN_TYPES_PATH):
    """Derives the set of base type names (Infamous stripped) from the
    full combination list -- used wherever code needs "is this a
    recognized mercenary type" independent of Infamous status (e.g.
    resolve_gem_presence, and gems_by_mercenary.json/skills_by_mercenary.json,
    which are both keyed by base type name only, since Infamous doesn't
    change which gem/skills a type has). Doing it this way rather than
    maintaining a separate base-types list means "Warpriest of the
    Ruckus" -- which never appears bare in the source list, only as
    "Infamous Warpriest of the Ruckus" -- is still correctly recognized
    as a real type when resolve_gem_presence checks a bare type name
    against it, one source of truth instead of two lists that could
    drift out of sync."""
    global _known_base_types_cache
    if _known_base_types_cache is not None:
        return _known_base_types_cache
    base_types = set()
    for full in _load_known_mercenary_types(path):
        base_types.add(_INFAMOUS_PREFIX_RE.sub("", full).strip())
    _known_base_types_cache = base_types
    return _known_base_types_cache


def match_mercenary_type(candidate: str, pool=None, cutoff: float = 0.6) -> str:
    """Corrects an OCR-read string against a known, finite pool of valid
    values -- `pool` defaults to every base type name (Infamous
    stripped, see _load_known_mercenary_base_types), since that's the
    part of a type reading that actually benefits from fuzzy
    correction: OCR noise/box-clipping (the Eruptor/icon-width problem)
    lands on the type name itself, not on whether "Infamous" was
    present, which OCR reads reliably as a whole distinct word. Pass
    the full type+infamy combination list explicitly (see
    parse_mercenary_type) when validating a combination as a whole
    rather than correcting a base type name.

    Matching against the base-type pool rather than the full
    "Infamous X" combination list is deliberate, not just simpler: a
    genuinely impossible OCR read like "Infamous Flamehand" (a type
    confirmed to never be Infamous) was found to fuzzy-match an
    unrelated but textually-similar real combination ("Infamous
    Stormhand") when matched as one combined string -- a worse error
    than the base type alone would ever produce, since "Flamehand" by
    itself matches unambiguously. Infamy is resolved independently in
    parse_mercenary_type instead, and the full combination is checked
    against the known-valid list only to flag an unusual reading, never
    to silently substitute a different type for it.

    A mercenary's type is always exactly one of the values in the given
    pool -- unlike the name field (open-ended, so it can't be
    fuzzy-matched this way), OCR noise on the type field can be
    corrected with confidence because there's a fixed, small set of
    valid answers. This is what resolved the Eruptor/icon-width
    problem: rather than chasing a pixel boundary that works for one
    icon shape and clips another, matching "Ruptor" (post-
    capitalization of the OCR miss "ruptor") against this list finds
    "Eruptor" as the clear best match regardless of which side of the
    text an icon happened to clip.

    If nothing scores above `cutoff`, the original candidate is returned
    unchanged and a warning is printed -- silently substituting a wrong
    "closest" guess for a genuinely unrecognized reading would be worse
    than leaving it as-is for manual review, per the original build
    plan's approach to OCR misreads.
    """
    if pool is None:
        pool = sorted(_load_known_mercenary_base_types())
    if not pool or not candidate:
        return candidate
    matches = difflib.get_close_matches(candidate, pool, n=1, cutoff=cutoff)
    if matches:
        return matches[0]
    _note(f"{candidate!r} didn't confidently match any known value in the given pool -- "
          f"logged as-is; check definitions/mercenary_types.json if this is new/valid.")
    return candidate


SKILLS_DEFINITION_PATH = "definitions/skills_by_mercenary.json"
_mercenary_skills_cache = None

# A handful of entries in the game-extracted skill/support tables are
# dev-only placeholders for unused build slots (e.g. a mercenary with
# fewer real Secondary skills than the schema's slot count still gets a
# filler entry) -- these can never legitimately appear in a real capture,
# so they're excluded from the fuzzy-matching pools here rather than
# risking a garbled OCR read getting "corrected" into one of them.
#
# Filtered in code, not via a JSON flag on the source files: both
# definitions/skills_by_mercenary.json and supports_by_skills.json are
# "AUTOGENERATED FILE -- DO NOT MODIFY" outputs of
# tools/extract_mercenary_skills.py, so any flag hand-added directly to
# them would be silently lost the next time that script re-runs. This is
# the same tension already logged, unresolved, in AI_RAMBLINGS.md for an
# "active: false" flag on mercenary_types.json -- worth resolving the
# same way for both if it's picked up.
_PLACEHOLDER_SKILL_NAMES = {"Do Nothing"}
_PLACEHOLDER_NAME_RE = re.compile(r"^\[DNT\]", re.IGNORECASE)


def _is_placeholder_skill_name(name: str) -> bool:
    return name in _PLACEHOLDER_SKILL_NAMES or bool(_PLACEHOLDER_NAME_RE.match(name))


def _load_mercenary_skills(path: str = SKILLS_DEFINITION_PATH):
    """Loads and caches the per-type skill pools
    (definitions/skills_by_mercenary.json), keyed by the FULL type+infamy
    combination (e.g. "Stormhand" and "Infamous Stormhand" as separate
    keys) exactly as the game-extracted data represents them -- this is
    autogenerated by tools/extract_mercenary_skills.py and, unlike the
    hand-entered file it replaced, is always complete (every one of the
    65 combinations has real Primary/Secondary/Utility pools, never
    null)."""
    global _mercenary_skills_cache
    if _mercenary_skills_cache is not None:
        return _mercenary_skills_cache
    if not os.path.isfile(path):
        _mercenary_skills_cache = {}
        return _mercenary_skills_cache
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    _mercenary_skills_cache = data.get("mercenaries", {})
    return _mercenary_skills_cache


def _skill_pool_for_entry(entry: dict) -> list:
    combined = entry.get("Primary", []) + entry.get("Secondary", []) + entry.get("Utility", [])
    return [name for name in combined if not _is_placeholder_skill_name(name)]


def known_skills_for_type(mercenary_type: str, path: str = SKILLS_DEFINITION_PATH) -> list:
    """Returns every skill (Primary+Secondary+Utility combined, placeholder
    entries excluded) known for this mercenary type, or [] if the type
    isn't recognized at all.

    `mercenary_type` is expected to be the FULL type+infamy combination
    (e.g. "Infamous Warpriest"), matching how skills_by_mercenary.json is
    keyed -- infamy is preserved rather than stripped here, since the
    source data always has a distinct record per combination and there's
    no confirmed guarantee (yet) that an Infamous variant's skill pool is
    identical to its non-Infamous counterpart (see AI_RAMBLINGS.md's open
    "infamous/non-infamous consolidation" question). If the exact
    combination isn't found -- e.g. a caller only has the bare base type
    on hand -- this falls back to stripping a leading "Infamous " and
    trying again, rather than returning nothing outright.
    """
    if not mercenary_type:
        return []
    mercenaries = _load_mercenary_skills(path)
    entry = mercenaries.get(mercenary_type)
    if entry is None:
        entry = mercenaries.get(_INFAMOUS_PREFIX_RE.sub("", mercenary_type).strip())
    if entry is None:
        return []
    return _skill_pool_for_entry(entry)


def all_known_skills(path: str = SKILLS_DEFINITION_PATH) -> list:
    """Returns the union of every skill (placeholder entries excluded)
    across every known type+infamy combination -- a broader (but less
    precise) fallback pool for matching when the specific mercenary's own
    type isn't recognized. Complete across all 65 combinations, since
    skills_by_mercenary.json no longer has partially-filled-in entries."""
    skills = []
    for entry in _load_mercenary_skills(path).values():
        skills.extend(_skill_pool_for_entry(entry))
    return skills


def match_skill_name(candidate: str, mercenary_type: str = None, cutoff: float = 0.6) -> str:
    """Corrects an OCR-read skill name against known skill pools, the
    same approach as match_mercenary_type but with a two-tier fallback:
    prefer the specific mercenary's own skill pool (narrower, so a match
    is more confident and less likely to confuse two similarly-named
    skills across different types), falling back to the pool of every
    known skill across every type if the specific type isn't recognized.
    `mercenary_type`, if given, should be the full type+infamy
    combination (e.g. "Infamous Warpriest") -- see known_skills_for_type
    for why infamy is preserved rather than stripped here. Case-
    insensitive (the in-game skill list renders in all-caps, unlike the
    mixed-case warrant text these pools were transcribed from) --
    matched against lowercased pool entries, but returns the pool's own
    original casing, not the candidate's.

    If nothing scores above cutoff -- including when NEITHER pool has
    any entries, e.g. a genuinely unrecognized type with no global data
    either -- the original candidate is returned as-is with a warning,
    per the same reasoning as match_mercenary_type: a wrong "closest"
    guess is worse than an unresolved value flagged for manual review.
    """
    if not candidate:
        return candidate
    pool = known_skills_for_type(mercenary_type) or all_known_skills()
    if not pool:
        return candidate
    lower_to_original = {s.lower(): s for s in pool}
    matches = difflib.get_close_matches(candidate.lower(), lower_to_original.keys(), n=1, cutoff=cutoff)
    if matches:
        return lower_to_original[matches[0]]
    _note(f"skill {candidate!r} didn't confidently match any known skill "
          f"(mercenary_type={mercenary_type!r}) -- logged as-is; check "
          f"definitions/skills_by_mercenary.json if this is a new/valid skill.")
    return candidate


_CURLY_QUOTES_RE = re.compile(r"[\u2018\u2019]")


_SKILL_OCR_PSM_MODES = (3, 6)


def _extract_skill_names_pass(crop: Image.Image, mercenary_type: str, psm: int) -> list:
    raw = pytesseract.image_to_string(crop, config=f"--psm {psm}")
    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    lines = [_CURLY_QUOTES_RE.sub("'", line) for line in lines]
    return [match_skill_name(line, mercenary_type) for line in lines]


def extract_skill_names(crop: Image.Image, mercenary_type: str = None) -> list:
    """OCRs the skills region and returns a list of skill name strings, in
    on-screen top-to-bottom order (typically 6 for a level 83 mercenary,
    per every real capture examined so far, but this doesn't hard-code
    that count).

    Runs BOTH --psm 3 (Tesseract's default full-page segmentation) and
    --psm 6 (assume a single uniform block of text) and keeps whichever
    pass matched MORE names against the known-skill pool (ties go to
    psm 3) -- neither mode alone is reliable. psm 3 was the original
    choice, until a real capture (Infamous Bloodletter, `test_data/
    skills/infamous_bloodletter_corvil.png`) showed it can silently drop
    entire real skill rows (read only 2 of 6, no error). Switching
    outright to psm 6 fixed that capture but broke a much larger real
    check: sweeping every real capture with a paired warrant.txt on hand
    (66 captures) found psm 6 dropping 1-4 real skills on 9 of them,
    every one of which psm 3 read completely correctly. So this isn't
    "psm 6 is better" or "psm 3 is better" -- each drops rows the other
    doesn't, on different real captures, and picking one over the other
    outright just trades one set of silent failures for a different one.
    Taking the pass with more pool-matched names, rather than trying to
    merge both passes' lines, sidesteps a real ordering hazard: the
    known-skill pool's own order (Primary+Secondary+Utility) does NOT
    reliably match true on-screen order (confirmed directly -- e.g.
    Infamous Warpriest's pool lists Beacons of Faith before Dominating
    Blow, but the real screen order is the reverse), so a partial pass's
    lines can't just be reordered by pool position and merged in; the
    whole line list from a single pass is the only thing guaranteed to
    preserve real screen order. See AI_RAMBLINGS.md's skill-name OCR
    section for the real before/after numbers on both changes.

    Both passes tolerate a small icon-bleed noise pattern (a short
    garbage token glued onto the front of a real name's line, or a lone
    garbage line by itself) -- the same general "leading noise" pattern
    tolerated elsewhere in this project (see extract_text/
    parse_mercenary_type) -- filtered out downstream by
    match_skill_name's fuzzy pool match (a genuine no-match is logged
    and returned as-is, not corrected into a wrong skill) and, for
    warrant_extracted.txt specifically, build_warrant_extracted_text's
    known-skill-pool membership filter.

    Each line is normalized (curly apostrophes -> straight, matching
    the warrant-transcribed known-skills data) and corrected via
    match_skill_name -- the in-game list renders in all-caps, so this
    is what recovers the actual display casing rather than guessing at
    which words to capitalize.

    `mercenary_type`, if passed, should be the full type+infamy
    combination (e.g. "Infamous Warpriest") -- see
    known_skills_for_type's docstring for why.
    """
    pool = set(known_skills_for_type(mercenary_type)) or set(all_known_skills())
    passes = [_extract_skill_names_pass(crop, mercenary_type, psm) for psm in _SKILL_OCR_PSM_MODES]
    return max(passes, key=lambda lines: sum(1 for name in lines if name in pool))


_LEVEL_DIGITS_RE = re.compile(r"(\d+)")


def extract_level(crop: Image.Image):
    """OCRs the level region ("Lvl NN") and returns the level as an int,
    or None if no digits were found.

    Uses --psm 11 directly on the raw crop, no _isolate_text_row
    preprocessing -- tested against 8 real screenshots (Lvl 66, 68, 73,
    79, 80, 81, 82, 83) and read every one correctly on the first try,
    unlike --psm 7/6 which badly garbled 6 of those 8 (misreading the
    box's own border/corners as text). See definitions/
    mercenary_regions.json's "level" region note for the calibration
    screenshots' provenance and an important caveat: those were
    CTRL+PRTSC captures, not mss, so this is validated against the
    region's real position and the text's OCR-readability, but not yet
    against an actual mss capture end to end.
    """
    raw = pytesseract.image_to_string(crop, config="--psm 11").strip()
    match = _LEVEL_DIGITS_RE.search(raw)
    if match is None:
        return None
    return int(match.group(1))


def parse_mercenary_type(raw_type_text: str):
    """Splits the type_subtype field's OCR text into (infamy, mercenary_type).

    Normalizes case first (each word capitalized -- observed OCR output
    has included both all-lowercase and correctly-cased readings of the
    exact same in-game text), strips a stray single leading letter from
    icon bleed if one precedes "Infamous" specifically, then resolves
    infamy and the base type INDEPENDENTLY: "Infamous" is detected by
    simple prefix match (OCR reads this reliably as a whole distinct
    word -- it isn't the part that needs fuzzy correction), and the
    remaining base type name is fuzzy-matched against the known base
    type pool (match_mercenary_type) to correct OCR noise/box-clipping
    the same way it always has (e.g. "ruptor" -> "Eruptor").

    The resulting (infamy, base type) combination is then checked
    against the full list of valid combinations in
    definitions/mercenary_types.json purely to WARN if it's not one
    actually confirmed to occur (e.g. "Infamous Flamehand," a type
    confirmed to never be Infamous) -- never to silently substitute a
    different type. An earlier version of this function fuzzy-matched
    the whole "Infamous X" string as one combined value instead, which
    let a genuinely impossible reading get corrected to an unrelated
    but textually-similar real combination (e.g. "Infamous Flamehand"
    -> "Infamous Stormhand") -- a worse error than just flagging it.
    """
    if not raw_type_text:
        return None, None
    normalized = " ".join(word.capitalize() for word in raw_type_text.split())
    normalized = _STRAY_ICON_PREFIX_RE.sub("", normalized)
    if _INFAMOUS_PREFIX_RE.match(normalized):
        infamy, base = "Infamous", _INFAMOUS_PREFIX_RE.sub("", normalized).strip()
    else:
        infamy, base = None, normalized
    base = match_mercenary_type(base)

    combined = f"{infamy} {base}" if infamy else base
    known_combinations = _load_known_mercenary_types()
    if known_combinations and combined not in known_combinations:
        _note(f"{combined!r} isn't a known type+infamy combination (infamy={infamy!r}, "
              f"type={base!r}) -- logged as read; check definitions/mercenary_types.json if "
              f"this is a new/valid combination, since it may indicate infamy was misread.")
    return infamy, base


REFERENCES_DIR = "assets"

# _load_reference_icons only scans these specific subfolders under
# REFERENCES_DIR, not every folder present -- this is what makes it safe
# to keep not-yet-validated reference candidates (e.g. assets/pending_gems/,
# staged raw-game-asset composites not yet confirmed to pass match_icon's
# threshold) alongside the real ones without them silently entering the
# active matching pool. Add a category here once its folder is meant to
# actually be searched.
REFERENCE_CATEGORIES = ["gems"]

_reference_icon_cache = None

# Naming convention for every file under assets/<category>/: lowercase
# with underscores, no spaces or punctuation (spectral_helix_of_trarthus.png,
# not "Spectral Helix of Trarthus.png"). match_icon returns the filename
# stem as-is, so keeping this consistent matters for anything downstream
# that compares/logs those names. Use slugify_reference_name() when saving
# a new reference icon from a display name (e.g. OCR'd or user-provided
# text) so it's never typed by hand inconsistently.
_SLUG_INVALID_CHARS_RE = re.compile(r"[^a-z0-9]+")


def slugify_reference_name(display_name: str) -> str:
    """Converts a display name (e.g. "Spectral Helix of Trarthus") into the
    assets/ naming convention (e.g. "spectral_helix_of_trarthus")."""
    slug = _SLUG_INVALID_CHARS_RE.sub("_", display_name.strip().lower())
    return slug.strip("_")


def display_name_from_slug(slug: str) -> str:
    """Converts an assets/ filename slug back to a human-readable
    display name for console/log output (e.g. "spectral_helix_of_trarthus"
    -> "Spectral Helix Of Trarthus"). The on-disk reference filename
    always stays lowercase_with_underscores per the naming convention --
    this only affects how the matched name is displayed, never how it's
    stored. Original word casing (e.g. "of" vs "Of") isn't recoverable
    from the slug alone, so every word is capitalized rather than
    guessing at grammar."""
    return " ".join(word.capitalize() for word in slug.split("_"))


def _load_reference_icons(references_dir: str = REFERENCES_DIR, categories=None):
    """Loads every reference icon under references_dir/<category>/<name>.png,
    for each category in `categories` (defaulting to REFERENCE_CATEGORIES),
    once, and caches the result. Adding a new known icon to an already-
    allowed category is just dropping a cropped reference PNG in the
    right folder -- no code change needed; the cache picks it up next
    process restart. A folder under references_dir that isn't in
    REFERENCE_CATEGORIES (e.g. pending_gems/) is never scanned at all,
    regardless of what it contains.

    Loaded as RGBA, not flattened to RGB: a reference sourced from raw game
    asset files (a gem's colored-stone base composited with its gold
    symbol overlay, as opposed to a screenshot) carries real transparency
    outside the icon's silhouette, and match_icon uses that alpha channel
    to mask out background/transparent pixels from the comparison rather
    than let them count as content to match. A plain screenshot-derived
    reference has no meaningful transparency, so it loads with alpha=255
    everywhere -- match_icon then falls back to comparing the full
    image, exactly as before, so existing screenshot-based references
    keep working unchanged."""
    global _reference_icon_cache
    if _reference_icon_cache is not None:
        return _reference_icon_cache

    categories = categories if categories is not None else REFERENCE_CATEGORIES
    refs = []
    for category in categories:
        category_path = os.path.join(references_dir, category)
        if not os.path.isdir(category_path):
            continue
        for fname in sorted(os.listdir(category_path)):
            if fname.lower().endswith((".png", ".jpg", ".jpeg")):
                name = os.path.splitext(fname)[0]
                img = Image.open(os.path.join(category_path, fname)).convert("RGBA")
                refs.append((category, name, img))
    _reference_icon_cache = refs
    return refs


def _reference_category_for_name(name: str, references_dir: str = REFERENCES_DIR) -> str:
    """Looks up which assets/<category>/ folder a matched name came
    from (e.g. "gems"). Used to label log/console output by category
    (gem= vs. a future item=/scarab= etc.) without match_icon itself
    needing to return more than the plain name everywhere it's used."""
    for category, ref_name, _img in _load_reference_icons(references_dir):
        if ref_name == name:
            return category
    return None


_ICON_COMPARE_SIZE = (48, 48)


def _icon_similarity_score(candidate_crop: Image.Image, ref_img: Image.Image, size=_ICON_COMPARE_SIZE) -> float:
    """Normalized cross-correlation score (cv2.matchTemplate,
    TM_CCORR_NORMED) between one candidate crop and ONE reference icon,
    using the reference's alpha channel as a match mask -- the exact
    comparison match_icon(), is_gem_present(), and
    disambiguate_blade_ambusher_gem() all need, factored out here so it's
    computed identically in all three rather than three independently-
    maintained copies of the same math (see match_icon's docstring for
    why TM_CCORR_NORMED and the alpha mask specifically).

    `ref_img` is converted to RGBA internally regardless of its input
    mode -- callers everywhere else in this file already get RGBA from
    _load_reference_icons, but converting here too (a plain RGB crop
    gets alpha=255 everywhere, i.e. no masking, via PIL's own RGB->RGBA
    conversion) means passing a raw, un-converted crop as a makeshift
    reference -- e.g. scoring a crop against itself -- can't crash on a
    missing alpha channel instead of just working."""
    candidate = np.array(candidate_crop.convert("RGB").resize(size), dtype=np.float32)
    ref_rgba = np.array(ref_img.convert("RGBA").resize(size))
    ref_rgb = ref_rgba[:, :, :3].astype(np.float32)
    mask = (ref_rgba[:, :, 3] > 127).astype(np.uint8) * 255
    result = cv2.matchTemplate(candidate, ref_rgb, cv2.TM_CCORR_NORMED, mask=mask)
    return float(np.nan_to_num(result, nan=-1.0).max())


# Scale factors tried by _icon_similarity_score_multiscale, expressed as a
# fraction of _ICON_COMPARE_SIZE. This narrow range (not the wider 0.5-1.3
# used once, separately, for the cross-domain composite-vs-real-capture
# alignment experiment in assets/README.md -- a game sprite at a totally
# different native resolution than a screen capture plausibly needs a big
# scale correction) is what actually produced the validated real-gem-floor
# vs. real-non-gem-ceiling separation for gem PRESENCE (see assets/README.md,
# "Multi-scale/position search for gem PRESENCE"): two real captures of the
# same UI at the same resolution shouldn't need much scale correction
# relative to each other -- this is mostly a POSITION search with a little
# scale slack, not a scale search. Widen this only alongside a fresh
# validation pass, not by assumption.
_ICON_MULTISCALE_FACTORS = [0.85, 0.90, 0.95, 1.0, 1.05, 1.10, 1.15]


def _icon_similarity_score_multiscale(candidate_region: Image.Image, ref_img: Image.Image,
                                       scale_factors=_ICON_MULTISCALE_FACTORS, base_size=_ICON_COMPARE_SIZE[0]):
    """Like _icon_similarity_score, but searches for the reference at its
    best-fitting SCALE and POSITION within a candidate region that's
    larger than the reference, instead of assuming both are already the
    same size and aligned.

    Not yet used by match_icon()/is_gem_present()/
    disambiguate_blade_ambusher_gem() -- those still use the fixed-size,
    fixed-position _icon_similarity_score. This exists as a validated
    building block (see assets/README.md's "Multi-scale/position search
    for gem PRESENCE" and the compositing-alignment follow-up, both of
    which prototyped this exact search ad hoc before it was factored out
    here) for whichever of those adopts real search -- deliberately
    scoped to just the comparison itself, not wired into production
    behavior, since that adoption needs its own threshold recalibration
    and a real (not glued-from-inset-crops) wider capture region first.

    `candidate_region` must be larger than the reference at every scale
    tried, or that scale is skipped entirely (returns -1.0 for it) --
    unlike _icon_similarity_score, which always resizes both images to
    the same fixed size regardless of their original dimensions.

    Returns (best_score, best_scale, best_position) -- the position is
    (x, y) of the matched region's top-left corner in candidate_region's
    own coordinates, needed by any caller that wants to know WHERE the
    match landed (e.g. to tell which rucksack quadrant it falls in),
    not just whether one exists.
    """
    region = np.array(candidate_region.convert("RGB"), dtype=np.float32)
    rh, rw = region.shape[:2]
    ref_rgba_full = ref_img.convert("RGBA")

    best_score, best_scale, best_pos = -1.0, None, None
    for scale in scale_factors:
        size = max(8, int(round(base_size * scale)))
        if size >= min(rh, rw):
            continue
        ref_resized = ref_rgba_full.resize((size, size))
        ref_rgba = np.array(ref_resized)
        ref_rgb = ref_rgba[:, :, :3].astype(np.float32)
        mask = (ref_rgba[:, :, 3] > 127).astype(np.uint8) * 255
        result = cv2.matchTemplate(region, ref_rgb, cv2.TM_CCORR_NORMED, mask=mask)
        result = np.nan_to_num(result, nan=-1.0)
        _min_val, score, _min_loc, loc = cv2.minMaxLoc(result)
        if score > best_score:
            best_score, best_scale, best_pos = float(score), scale, loc
    return best_score, best_scale, best_pos


def match_icon(crop: Image.Image, references_dir: str = REFERENCES_DIR, min_score: float = 0.94, min_margin: float = 0.025) -> str:
    """Template-match an icon region (rucksack quadrant, skill/support slot)
    against the reference set under assets/<category>/<name>.png (only
    categories listed in REFERENCE_CATEGORIES are ever searched).

    Uses normalized cross-correlation (cv2.matchTemplate, TM_CCORR_NORMED)
    rather than raw mean-squared-error -- MSE compares exact pixel values
    and so is fragile to brightness/framing differences between the
    candidate and a reference sourced from a different rendering pipeline
    (measured directly: an official wiki render of a gem we already had a
    real in-game reference for scored worse against that real reference
    than our match threshold allowed, purely from rendering/alignment
    differences, not different content -- see assets/README.md).
    Normalized cross-correlation is inherently more tolerant of that kind
    of variance since it measures correlation/shape similarity rather
    than exact pixel agreement.

    A reference's alpha channel (see _load_reference_icons) is used as a
    match mask, so transparent/background pixels outside the icon's own
    silhouette don't count toward the score at all -- this is what
    actually makes a raw game-asset-sourced reference (transparent
    background, no UI border) usable against a screenshot-cropped
    candidate (dark UI border, drop shadow) without those framing
    differences hurting the score, rather than needing the two to be
    cropped identically.

    Accepts a match only if the best score clears min_score AND beats
    the second-best candidate by at least min_margin -- not just "is
    this score high enough" but "is this confidently the best answer,
    not a near-tie with a different gem." Both thresholds are taken
    from a similar independently-built PoE mercenary tool (see project
    history) rather than picked from our own two-reference sample: our
    own measurement (self-match 1.0 vs. the only cross-gem pair we can
    test, 0.89) leaves too little margin below min_score to trust a
    threshold derived from it alone, so borrowing externally-validated
    numbers is safer until we have enough references of our own to
    calibrate properly.

    Single fixed comparison size for now (no multi-scale/resolution
    search) -- deferred per the project plan until this works reliably
    in one environment; see docs for that as a tracked follow-up.
    """
    refs = _load_reference_icons(references_dir)
    if not refs:
        return None

    scores = [(_icon_similarity_score(crop, ref_img), name) for _category, name, ref_img in refs]

    scores.sort(key=lambda t: -t[0])
    best_score, best_name = scores[0]
    runner_up = scores[1][0] if len(scores) > 1 else -1.0
    if best_score >= min_score and best_score - runner_up >= min_margin:
        return best_name
    return None


# Calibrated empirically against real mss-sourced captures: 7 genuine
# gem instances (5 distinct gems, including 3 independent Siege Ballista
# captures from different encounters/quadrants) scored [0.7541, 1.0000]
# against the best-matching known gem reference, while 7 real non-gem
# rucksack items (currency orbs, cards, a scarab) scored [0.6059, 0.7345]
# -- cleanly separable at the time, with the gap's midpoint landing at
# 0.74. This is a much easier bar than match_icon()'s identity threshold
# (0.94): all 12 Trarthus gems share enough visual language (colored gem
# + gold overlay + sparkle glow) that telling "some gem" from "not a
# gem" is reliable even though telling *which* gem apart is not (see
# resolve_gem_presence's docstring for why identity resolution now goes
# through mercenary type instead of image matching).
#
# Revised after a real false positive: an Ancient Orb (currency, no
# relation to gem art at all) scored 0.7440 -- just above the original
# 0.74 threshold, and only surfaced as a bug because it happened to
# score high enough to register as "present" for a mercenary type with
# no gem pool at all (see resolve_gem_presence). An Orb of Unmaking from
# the same capture scored 0.7383 -- correctly below both the old and
# new threshold, but confirms non-gem scores can genuinely reach this
# high.
#
# Revised AGAIN after a second, more marginal false positive: an Orb of
# Scouring scored 0.7508 against a
# *different* mercenary's rucksack -- above the 0.75 this was just
# raised to. Moved again to 0.755, sitting between this new non-gem
# ceiling (0.7508) and the current gem floor (0.7541) -- a margin of
# only ~0.003 on the gem side. Flagging plainly rather than quietly
# re-tuning again: this is the second real misfire in a row, each one
# closer to the gem floor than the last, and the underlying mechanism
# (raw correlation against a small reference set) is starting to show
# the same kind of thin, non-robust separation that match_icon's
# fine-grained identity matching hit before it was abandoned as
# unreliable (see project history / "Disambiguation strategies" in
# assets/README.md). If a third real misfire narrows this further, that
# would be a real signal to reconsider whether presence detection needs
# a sturdier approach too, not just another threshold nudge.
#
# THE THIRD MISFIRE HAPPENED: an Exalted Orb (capture 20260912_214055,
# rucksack_top_left, real currency, no gem association) scored 0.7568 --
# ABOVE this threshold outright, not just close to it. With this sample
# added to the real data (test_data/currencies/exalted_orb.png,
# tests.py's run_gem_presence_tests), the real gem floor (0.7541,
# test_data/gems/top_left.png) and the real non-gem ceiling (0.7568)
# now OVERLAP. No threshold value separates them anymore -- this is the
# "sturdier approach needed" signal predicted above, not a fourth nudge.
# A same-day frozen-ResNet18-embedding test (see assets/README.md)
# separates the same two samples cleanly (gem floor 0.9250, non-gem
# ceiling 0.7564, ~0.17 margin) -- a validated candidate fix, not yet
# adopted here since it would make torch/torchvision a hard production
# dependency (see assets/README.md's install-burden discussion) rather
# than the optional/experimental role it's had so far this session.
GEM_PRESENCE_THRESHOLD = 0.755


def is_gem_present(crop: Image.Image, references_dir: str = REFERENCES_DIR, threshold: float = GEM_PRESENCE_THRESHOLD) -> bool:
    """Loosely checks whether a rucksack slot contains *some* known gem,
    without attempting to identify *which* one -- see GEM_PRESENCE_THRESHOLD
    for why this coarser question has a reliable answer where match_icon's
    fine-grained identity question currently doesn't. Used as the first
    step of the gem-detection stopgap: presence here, identity resolved
    afterward from the mercenary's own type (see resolve_gem_presence).
    """
    refs = _load_reference_icons(references_dir)
    if not refs:
        return False
    best_score = max((_icon_similarity_score(crop, ref_img) for _category, _name, ref_img in refs), default=-1.0)
    return best_score >= threshold


# Real, measured margin from validating this against every real known-gem
# and known-false-positive session on hand (8 vs. 9 -- see assets/README.md,
# "Multi-scale/position search for gem PRESENCE"): real gem floor 0.8209,
# real non-gem ceiling 0.7966. This threshold sits at their midpoint, same
# convention as GEM_PRESENCE_THRESHOLD above. The margin (~0.024) is thin --
# the same size that's broken GEM_PRESENCE_THRESHOLD twice before -- so
# treat this as a real but still-early calibration, likely to need
# revision as more real false positives turn up, not a settled number.
RUCKSACK_GEM_PRESENCE_THRESHOLD = 0.8088

# How far past each quadrant's own boundary the search window extends, in
# px, before gluing four adjacent quadrants into one region (see
# _glue_rucksack_region). Validated at 10/16/22/30px -- all four produced
# IDENTICAL results (see assets/README.md), meaning the correct match
# never needed to reach past its own quadrant's boundary; the margin
# mainly gives _icon_similarity_score_multiscale's scale search (up to
# 1.15x) room to not clip against the window edge. 16 is a mid-of-range
# pick, not independently optimized -- safe to tune up (more real
# neighboring content considered, more compute) or down (less of both)
# if real data ever calls for it.
RUCKSACK_WINDOW_MARGIN = 16


def _glue_rucksack_region(crops: dict):
    """Reassembles the four rucksack quadrant crops into one contiguous
    region, plus each quadrant's (x0, y0, x1, y1) bounds within that
    region -- real neighboring content from the SAME encounter, not
    synthetic padding. Possible at all because the four quadrants are
    confirmed (definitions/mercenary_regions.json) to perfectly tile the
    parent rucksack box with zero gaps; a small ~4px seam appears at each
    internal border regardless, from each saved crop's own 2px cosmetic
    INSET margin (filled black here) -- real UI divider content, not
    captured separately, but not load-bearing for matching either.

    `crops` must have all four "rucksack_<quadrant>" keys (matching
    process_capture's crop dict) -- this is the caller-facing entry point
    for the windowed multi-scale presence check below, not something a
    single isolated crop can substitute for.
    """
    tl = crops["rucksack_top_left"]
    tr = crops["rucksack_top_right"]
    bl = crops["rucksack_bottom_left"]
    br = crops["rucksack_bottom_right"]
    canvas = Image.new("RGB", (tl.width + tr.width, tl.height + bl.height), (0, 0, 0))
    canvas.paste(tl, (0, 0))
    canvas.paste(tr, (tl.width, 0))
    canvas.paste(bl, (0, tl.height))
    canvas.paste(br, (tl.width, tl.height))
    bounds = {
        "rucksack_top_left": (0, 0, tl.width, tl.height),
        "rucksack_top_right": (tl.width, 0, canvas.width, tl.height),
        "rucksack_bottom_left": (0, tl.height, tl.width, canvas.height),
        "rucksack_bottom_right": (tl.width, tl.height, canvas.width, canvas.height),
    }
    return canvas, bounds


def is_gem_present_in_rucksack(crops: dict, references_dir: str = REFERENCES_DIR,
                                margin: int = RUCKSACK_WINDOW_MARGIN,
                                threshold: float = RUCKSACK_GEM_PRESENCE_THRESHOLD) -> dict:
    """Per-quadrant gem presence for a whole rucksack at once, using real
    multi-scale/position search (_icon_similarity_score_multiscale)
    against real neighboring content (_glue_rucksack_region) instead of
    is_gem_present()'s single fixed-size, fixed-position comparison on an
    isolated crop.

    Exists because is_gem_present() was measured to fail on nearly half
    of real non-gem examples once enough of them existed (see
    assets/README.md, GEM_PRESENCE_THRESHOLD's code comment) -- no
    threshold value fixes that; real search room does, at real cost (see
    below). is_gem_present() itself is untouched and still used for
    single-crop test fixtures (test_data/gems/, test_data/currencies/,
    test_data/scarabs/) --
    this is the whole-rucksack-aware entry point process_capture() uses
    instead of four independent is_gem_present() calls, kept SEPARATE
    (not a signature change to is_gem_present) so nothing that already
    depends on scoring one isolated crop in isolation breaks.

    Each of the four quadrants gets its own independent yes/no (searched
    within its own boundary plus `margin`, not the whole rucksack at
    once) so multi-gem counting (up to 4 per encounter) keeps working
    exactly as before -- this does not collapse to a single whole-
    rucksack yes/no.

    Performance: ~30ms per quadrant (7 references x 7 scales), ~120ms for
    all four -- against ~1ms total for the four is_gem_present() calls it
    replaces. Real, not free, but well under anything noticeable on a
    keypress-triggered capture. Tune via `margin` (window size) and the
    module-level _ICON_MULTISCALE_FACTORS (scale count/range) if this
    ever needs to trade accuracy for speed or vice versa -- see
    assets/README.md for the measured cost of a few different settings.

    Returns {"rucksack_top_left": bool, ...} -- same four keys
    process_capture() already stores in its record, so this is a drop-in
    replacement for the loop of is_gem_present() calls there.
    """
    refs = _load_reference_icons(references_dir)
    if not refs:
        return {k: False for k in ("rucksack_top_left", "rucksack_top_right",
                                    "rucksack_bottom_left", "rucksack_bottom_right")}
    canvas, bounds = _glue_rucksack_region(crops)
    w, h = canvas.size
    present = {}
    for quadrant, (x0, y0, x1, y1) in bounds.items():
        window = canvas.crop((max(0, x0 - margin), max(0, y0 - margin),
                               min(w, x1 + margin), min(h, y1 + margin)))
        best = max(_icon_similarity_score_multiscale(window, ref_img)[0] for _c, _n, ref_img in refs)
        present[quadrant] = best >= threshold
    return present


def resolve_gem_presence(mercenary_type: str, gem_count: int, blade_ambusher_matches: list = None):
    """Resolves the identity of `gem_count` detected-present gems using
    only the mercenary's own type -- the stopgap for match_icon's
    unreliable fine-grained identity matching (see project history: real
    independent captures of the same gem scored 0.68-0.75 against each
    other, indistinguishable from different-gem scores in the same
    range). Every mercenary type has exactly one associated gem except
    Blade Ambusher, which has two -- since a merc's rucksack can only
    contain copies of ITS OWN signature gem (never a different
    mercenary's), knowing the type is enough to name every detected gem
    for the 10 unambiguous types, with zero image-matching risk. This is
    still the ONLY path for those 10 types -- deferring to the 1:1
    type->gem binding for every mercenary except Blade Ambusher is
    deliberate, not a placeholder pending more image-matching work.

    `blade_ambusher_matches`, if given, is the per-present-slot list
    process_capture() builds via disambiguate_blade_ambusher_gem() --
    the one type this resolves through real image comparison instead of
    the type lookup, since Blade Ambusher's skills/warrant data has been
    confirmed to give zero disambiguating information here (see
    disambiguate_blade_ambusher_gem's docstring). Each entry is a gem
    slug or None (present but not confidently resolved). When absent
    (every other type, or a Blade Ambusher call site that hasn't been
    updated), this falls back to the original "Unknown (<type>)"
    behavior below rather than guessing.

    Returns a list of `gem_count` display-name strings. For an
    unambiguous type, every entry is that type's one gem. For Blade
    Ambusher with matches unresolved (or no matches given at all),
    entries are "Unknown (<type>)" rather than guessing, and a note is
    returned so the caller can flag it instead of silently logging an
    unresolved value.

    A recognized mercenary type with NO gem pool at all (23 of the 34
    known types -- only 11 have any Trarthus gem) is handled
    differently from a genuinely unrecognized type: a merc can only
    ever carry its own type's signature gem, and this type doesn't have
    one, so any "presence" detected here is almost certainly a false
    positive from the coarse image check (see GEM_PRESENCE_THRESHOLD)
    rather than a real, unidentified gem -- confirmed directly (see
    project history: an Ancient Orb currency item scored just above
    threshold for a Shock Ambusher capture, which has no gem pool at
    all). Nothing is logged for this case, only a note, rather than
    fabricating an "Unknown" placeholder for something that almost
    certainly isn't a gem. A truly unrecognized type (not in the known
    mercenary list at all -- e.g. a bad OCR/fuzzy-match result) can't
    be ruled out the same way, so that case stays a cautious "Unknown".
    """
    if gem_count <= 0:
        return [], None
    expected = expected_gems_for_type(mercenary_type)
    if len(expected) == 1:
        return [display_name_from_slug(expected[0])] * gem_count, None
    if len(expected) > 1:
        if blade_ambusher_matches is not None:
            names = [display_name_from_slug(m) if m else f"Unknown ({mercenary_type})"
                     for m in blade_ambusher_matches]
            unresolved = sum(1 for m in blade_ambusher_matches if m is None)
            note = None
            if unresolved:
                note = (f"{unresolved} of {gem_count} gem(s) detected for {mercenary_type!r} "
                        f"couldn't be confidently disambiguated between "
                        f"{', '.join(display_name_from_slug(g) for g in expected)} -- logged as Unknown")
            return names, note
        note = (f"{gem_count} gem(s) detected for {mercenary_type!r}, which has multiple "
                f"possible gems ({', '.join(display_name_from_slug(g) for g in expected)}) -- "
                f"identity can't be resolved from type alone yet")
        return [f"Unknown ({mercenary_type})"] * gem_count, note
    if mercenary_type in _load_known_mercenary_base_types():
        note = (f"{gem_count} gem(s) reported present for {mercenary_type!r}, which has no "
                f"gem pool at all -- likely a false positive from the coarse presence check "
                f"(see GEM_PRESENCE_THRESHOLD), not logged as a gem")
        return [], note
    note = f"{gem_count} gem(s) detected but {mercenary_type!r} is not a recognized mercenary type"
    return ["Unknown"] * gem_count, note


GEM_TYPES_PATH = "definitions/gems_by_mercenary.json"
_gem_types_cache = None


def _load_gem_type_associations(path: str = GEM_TYPES_PATH):
    """Loads and caches the gem<->mercenary association list, keyed by gem
    SLUG (matching match_icon's return format) rather than display name,
    so callers can compare directly against a rucksack slot's raw match
    without an extra display-name round trip.

    Each entry's "gem" field is normally a single display-name string,
    but may be an array instead (currently only Blade Ambusher, which
    has two possible gems) -- both are flattened into the same list of
    (gem_slug, mercenary) pairs here, so every downstream consumer
    (expected_gems_for_type, resolve_gem_with_type_crosscheck) only
    ever deals with the flat, normalized form and never needs to know
    which mercenaries have one gem versus several."""
    global _gem_types_cache
    if _gem_types_cache is not None:
        return _gem_types_cache
    if not os.path.isfile(path):
        _gem_types_cache = []
        return _gem_types_cache
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    pairs = []
    for a in data.get("associations", []):
        gems = a["gem"] if isinstance(a["gem"], list) else [a["gem"]]
        for gem in gems:
            pairs.append((slugify_reference_name(gem), a["mercenary"]))
    _gem_types_cache = pairs
    return _gem_types_cache


def expected_gems_for_type(mercenary_type: str, path: str = GEM_TYPES_PATH):
    """Returns the gem slug(s) (definitions/gems_by_mercenary.json) known
    to be associated with a mercenary type. Empty for an unrecognized
    type, one slug for every type except Blade Ambusher, which has two."""
    if not mercenary_type:
        return []
    return [gem for gem, t in _load_gem_type_associations(path) if t == mercenary_type]


def resolve_gem_with_type_crosscheck(matched_gem: str, mercenary_type: str):
    """Cross-checks an icon-matched gem slug against the mercenary's type,
    for types with exactly one known associated gem (every type except
    Blade Ambusher, which has two and so can't be disambiguated by type
    alone).

    This corrects a matched gem, or confirms it, but deliberately never
    INVENTS one: if match_icon found nothing (None), that most likely
    means the slot holds something that isn't a gem at all (currency, a
    scarab), not an unrecognized gem -- so a type with a unique expected
    gem is not used to fill in a detection from nothing, only to
    validate/correct a detection that was already claimed.

    Returns (final_gem_slug, note) -- note is None when nothing changed
    (no association data for this type, multiple possible gems, or the
    match already agreed), and a short string when a correction was
    made, so the caller can decide whether to surface it.
    """
    if matched_gem is None:
        return None, None
    expected = expected_gems_for_type(mercenary_type)
    if len(expected) != 1:
        return matched_gem, None
    if matched_gem == expected[0]:
        return matched_gem, None
    return expected[0], (
        f"gem corrected via type cross-check: icon matched "
        f"{display_name_from_slug(matched_gem)!r} but {mercenary_type!r} "
        f"is only associated with {display_name_from_slug(expected[0])!r}"
    )


# Blade Ambusher is the only mercenary type with more than one possible
# gem (definitions/gems_by_mercenary.json) -- every other type is resolved via the
# 1:1 type->gem binding in resolve_gem_presence, with zero image-
# matching risk. This is the one deliberate exception, and it's scoped
# to exactly these two slugs on purpose (see disambiguate_blade_ambusher_gem).
BLADE_AMBUSHER_GEM_SLUGS = ("spectral_throw_of_trarthus", "spectral_helix_of_trarthus")


def disambiguate_blade_ambusher_gem(crop: Image.Image, references_dir: str = REFERENCES_DIR, min_margin: float = 0.1) -> str:
    """Resolves WHICH of Blade Ambusher's two possible gems a rucksack
    slot crop shows -- Spectral Throw of Trarthus or Spectral Helix of
    Trarthus -- the one case resolve_gem_presence's type-based lookup
    can't answer (every other mercenary type has exactly one associated
    gem; this one has two). Skill/warrant data can't help either:
    confirmed directly against definitions/skills_by_mercenary.json and
    a real warrant that Blade Ambusher always has BOTH "of Trarthus"
    skills equipped (SecondaryCount == pool size), so which skills the
    mercenary knows carries zero disambiguating information.

    Deliberately NOT a call to match_icon() against the full reference
    pool -- this only ever compares the crop against these two specific
    candidates. That distinction matters: a real, reproduced near-
    collision exists between Spectral Throw and Bladefall of Trarthus
    (0.9387 against two independent real Bladefall captures -- see
    assets/README.md), which would sit inside match_icon()'s normal
    min_score/min_margin search and risk a wrong answer. It's irrelevant
    here because the caller already knows, from is_gem_present() plus
    the mercenary's own type, that the crop is one of exactly these two
    -- Bladefall was never a real candidate for this slot to begin with.

    Validated against real same-gem variance on both sides, not a
    single reference photo (see assets/README.md): 7 independent real
    Spectral Helix captures scored 0.9824-1.0000 against each other,
    while the one real Spectral Throw capture on hand scored only
    0.7352-0.7430 against every one of those 7 -- a real, ~0.24 margin.
    `min_margin` defaults well below that measured gap (rather than at
    it) to leave real headroom for Spectral Throw's own same-gem
    variance, which hasn't been measured yet (n=1 on that side so far).

    Returns the winning gem's slug (one of BLADE_AMBUSHER_GEM_SLUGS), or
    None if the two candidates' scores don't clear min_margin apart --
    an unresolved reading is left unresolved rather than guessed, same
    policy as every other fuzzy-match function in this file.
    """
    refs = {name: img for _category, name, img in _load_reference_icons(references_dir)
            if name in BLADE_AMBUSHER_GEM_SLUGS}
    if len(refs) < 2:
        return None

    scores = {name: _icon_similarity_score(crop, ref_img) for name, ref_img in refs.items()}

    best_name = max(scores, key=scores.get)
    other_name = next(n for n in BLADE_AMBUSHER_GEM_SLUGS if n != best_name)
    if scores[best_name] - scores[other_name] >= min_margin:
        return best_name
    return None


def process_capture(crops: dict) -> dict:
    """Runs extraction across all crops and returns a flat record ready to log.

    Rucksack slots record gem PRESENCE (is_gem_present_in_rucksack), not
    identity -- match_icon's fine-grained identity matching has been
    shown unreliable on real independent captures (same-gem and
    different-gem scores overlap), while presence detection ("is this
    some gem at all") is more tractable on the same real data. Identity
    gets resolved from the mercenary's own type instead, in
    build_log_row -- see resolve_gem_presence.

    Uses is_gem_present_in_rucksack() (real multi-scale/position search
    across all four quadrants at once), not four independent
    is_gem_present() calls -- the latter was measured to fail on nearly
    half of real non-gem examples once enough of them existed (see
    GEM_PRESENCE_THRESHOLD's code comment and assets/README.md); no
    threshold fix existed for that, only real search room did.
    """
    mercenary_name = extract_text(crops["name"])
    if mercenary_name is None:
        # Unlike match_mercenary_type/match_skill_name, extract_text has
        # no fuzzy-match pool to fall back on -- a total OCR failure here
        # (confirmed to happen on a real capture despite the name's own
        # glyph pixels being pristine, see extract_text's docstring) has
        # no auto-correction available. Printed the same way as those
        # other NOTEs so it's visible during the capture session, rather
        # than only discoverable later by opening the capture folder and
        # noticing the Name column is silently blank.
        _note("name extraction returned nothing -- Name column will be blank; "
              "check the capture folder's name.png if this keeps happening.")
    infamy, mercenary_type = parse_mercenary_type(extract_text(crops["type_subtype"]))
    combined_type = f"{infamy} {mercenary_type}".strip() if infamy else mercenary_type
    rucksack_slots = ("rucksack_top_left", "rucksack_top_right", "rucksack_bottom_left", "rucksack_bottom_right")
    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "mercenary_name": mercenary_name,
        "infamy": infamy,
        "mercenary_type": mercenary_type,
        "mercenary_level": extract_level(crops["level"]),
        # supports region is a multi-item region -- once slot subdivision
        # is worked out for icon matching, expand this into a per-slot field
        "skills": extract_skill_names(crops["skills"], mercenary_type=combined_type),
    }
    record.update(is_gem_present_in_rucksack(crops))

    # Blade Ambusher is the only type with more than one possible gem --
    # every other type is fully resolved by resolve_gem_presence's 1:1
    # type->gem lookup later, with no image-matching risk at all, and
    # stays on that path unchanged. This is the one deliberate exception:
    # while the crop is still on hand (resolve_gem_presence itself only
    # ever receives a gem COUNT, by design -- see its docstring), run the
    # narrow two-candidate disambiguation on each present slot. The
    # result list's order matches which slots were present, not the
    # fixed rucksack_slots order, since that's what resolve_gem_presence
    # needs to line results up against gem_count.
    if mercenary_type == "Blade Ambusher":
        record["blade_ambusher_gem_matches"] = [
            disambiguate_blade_ambusher_gem(crops[slot])
            for slot in rucksack_slots if record[slot]
        ]
    return record


def build_warrant_extracted_text(record: dict) -> str:
    """Formats process_capture()'s record into the same plain-text layout
    as a real Mercenary Warrant (compare against any captures/<timestamp>/
    warrant.txt) -- built entirely from what's OCR'd off the live
    encounter panel, no need to manually take the warrant item.

    One thing a real warrant.txt has that this doesn't: **Supports** --
    every skill's linked-support block is omitted entirely. Supports sit
    in the same hard image-matching problem domain as gems (see
    AI_RAMBLINGS.md's "Warrant-text alignment for skill/support ground
    truth"), unsolved as of this function.

    Mercenary Level IS included now (via extract_level() on the "level"
    region) -- if extraction fails (record["mercenary_level"] is None,
    e.g. a bad OCR read), the "Mercenary Level:" line is left out of the
    Build block entirely rather than writing a fake value.

    Skill names are filtered to `known_skills_for_type`'s (or, failing
    that, `all_known_skills`'s) pool before being included -- a real
    stray OCR row that doesn't match any known skill (confirmed to
    happen -- see AI_RAMBLINGS.md's skill-name OCR validation) is
    dropped from the skill-block list rather than emitted as a fake
    skill. It is NOT silently discarded, though: dropping a row without
    a trace means the only difference between "this mercenary really
    only has 5 visible skills" and "OCR ate one" would be an ephemeral
    console NOTE at capture time (see match_skill_name) -- invisible to
    anyone who isn't watching the terminal, and permanently lost once the
    console scrolls past it. Every genuinely dropped row is instead
    listed in a trailing comment line after the warrant-mimicking footer
    (so it doesn't corrupt the real-warrant-format body itself),
    persisted to disk in `warrant_extracted.txt` every time. The full,
    unfiltered OCR output is still available in `record["skills"]` too,
    for anyone debugging a specific capture.

    "Genuinely dropped" excludes one confirmed real false-alarm case: a
    long skill name that visually WRAPS to two lines in-game (e.g.
    "Corrupted Blade Vortex of the Scythe" renders as "CORRUPTED BLADE
    VORTEX" / "OF THE SCYTHE"). Tesseract reads each visual line as its
    own row, so this produces two OCR candidates for one real skill --
    the first (a real prefix of the full name) fuzzy-matches correctly,
    but the wrapped continuation ("THE SCYTHE") doesn't match anything on
    its own and would otherwise get flagged as an unrecognized/dropped
    row even though nothing was actually lost (confirmed directly against
    a real capture and its paired rematch's real warrant.txt: all 6
    skills were correct, the flag line was the only wrong part). Any
    unmatched candidate that's a case-insensitive substring of an
    ALREADY-matched skill name in this same result is assumed to be
    exactly this wrapped-continuation case and excluded from the flag
    list -- a real wrong/garbled OCR reading coincidentally being a
    verbatim substring of some other real matched skill name is not
    impossible, but unlikely enough not to worry about compared to how
    often multi-word skill names with "of the X" endings wrap.
    """
    mercenary_type = record.get("mercenary_type") or ""
    infamy = record.get("infamy")
    combined_type = f"{infamy} {mercenary_type}".strip() if infamy else mercenary_type
    known_pool = set(known_skills_for_type(combined_type)) or set(all_known_skills())
    raw_skills = record.get("skills") or []
    skills = [name for name in raw_skills if name in known_pool]
    unrecognized = [
        name for name in raw_skills
        if name not in known_pool
        and not any(name.lower() in matched.lower() for matched in skills)
    ]

    lines = [
        "Item Class: Map Fragments",
        "Rarity: Normal",
        "Mercenary Warrant",
        "--------",
        record.get("mercenary_name") or "",
        "--------",
        f"Build: {combined_type}",
    ]
    level = record.get("mercenary_level")
    if level is not None:
        lines.append(f"Mercenary Level: {level}")
    lines.append("--------")
    for skill in skills:
        lines.append(skill)
        lines.append("--------")
    lines.append("Right click this item to view Mercenary details.")
    lines.append("Can be used in a personal Map Device alongside a Map to have this "
                 "previously fought Mercenary reappear in the area for a rematch.")
    if unrecognized:
        lines.append("")
        lines.append(f"# unrecognized skill row(s), dropped: {', '.join(unrecognized)}")
    return "\n".join(lines) + "\n"


_MAP_ENTRY_RE = re.compile(r": You have entered (.+)\.\s*$")
_CLIENT_LOG_TAIL_BYTES = 200_000  # generous over how far back "current map" could be


def get_current_map(log_path: str = CLIENT_LOG_PATH):
    """Returns the most recently entered non-hideout map name, by scanning
    PoE's live Client.txt log for "You have entered <name>." lines.

    Hideout entries share that exact same log line format -- the
    distinguishing fact used here is that every hideout's display name
    always literally ends in "Hideout" (e.g. "Cosmic Turtle Hideout",
    whatever the player has named/chosen theirs), which a real map name
    never does. Entering a map and later exiting to a hideout aren't
    guaranteed to alternate 1:1 (a player can log out or exit the game
    directly from a map instead) -- so this always reports whichever
    non-hideout area was most recently entered, not a maintained
    "current location" state that assumes strict alternation.

    Only reads the tail of the file (last _CLIENT_LOG_TAIL_BYTES) rather
    than the whole thing -- Client.txt grows continuously over a play
    session and can get large, but the current map was, by definition,
    entered recently.
    """
    if not os.path.isfile(log_path):
        return None
    try:
        size = os.path.getsize(log_path)
        with open(log_path, "rb") as f:
            if size > _CLIENT_LOG_TAIL_BYTES:
                f.seek(size - _CLIENT_LOG_TAIL_BYTES)
            data = f.read()
        text = data.decode("utf-8", errors="ignore")
    except OSError:
        return None

    for line in reversed(text.splitlines()):
        m = _MAP_ENTRY_RE.search(line)
        if m:
            name = m.group(1).strip()
            if not name.endswith("Hideout"):
                return name
    return None


def build_log_row(record: dict, exceptions: str = "") -> dict:
    """Maps process_capture()'s internal record onto the fixed TSV/console
    column set: Name, Type, Infamous, Gem 1-4, Map, Exceptions.

    Gem 1-4: counts how many of the four rucksack slots show a gem
    PRESENT (record[slot] is a bool now, from is_gem_present -- see
    process_capture), then resolves all of their identities at once from
    the mercenary's own type (resolve_gem_presence), compacted from the
    start -- a rucksack with one gem always gets it into "Gem 1"
    regardless of which physical quadrant it was in. Up to 4 gems, some
    encounters have none. Every type except Blade Ambusher is fully
    resolved by that 1:1 type->gem binding alone. Blade Ambusher is
    resolved via record["blade_ambusher_gem_matches"] (real per-slot
    image comparison, see disambiguate_blade_ambusher_gem) when
    process_capture populated it; a present slot that couldn't be
    confidently told apart still comes back as "Unknown (<type>)" with a
    note, same as before, rather than guessing.

    Exceptions is always blank here -- it's an explicitly manual field
    (rematch, forced-infamy scarabs, reduced-infamy atlas passives, etc.)
    that only the person watching the encounter can know; this just
    reserves the column so it can be filled in by hand afterward.
    """
    rucksack_slots = (
        "rucksack_top_left", "rucksack_top_right",
        "rucksack_bottom_left", "rucksack_bottom_right",
    )
    gem_count = sum(1 for slot in rucksack_slots if record[slot])
    gem_names, note = resolve_gem_presence(
        record["mercenary_type"], gem_count, record.get("blade_ambusher_gem_matches"))
    if note:
        _note(note)
    gem_names += [""] * (4 - len(gem_names))  # pad to exactly 4 columns

    return {
        "Name": record["mercenary_name"] or "",
        "Type": record["mercenary_type"] or "",
        "Infamous": "Y" if record["infamy"] else "",
        "Gem 1": gem_names[0],
        "Gem 2": gem_names[1],
        "Gem 3": gem_names[2],
        "Gem 4": gem_names[3],
        "Map": get_current_map() or "",
        "Exceptions": exceptions,
    }


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def append_log(row: dict, log_path=LOG_PATH, fieldnames=LOG_FIELDNAMES):
    """Appends one row to the log using the fixed column set.

    Tab-separated rather than comma-separated: a mercenary name always
    contains a comma ("Name, the Title"), and while a properly quoted
    CSV handles that fine on a true File > Import, a plain clipboard
    copy-paste into Google Sheets doesn't respect CSV quoting -- it
    naively splits on every comma, so a name's own comma spills its
    second half into the next column and misaligns the whole row. None
    of our fields will ever contain a literal tab, and tab is the
    delimiter Sheets/Excel already expect on a plain paste, so this
    sidesteps the problem entirely rather than requiring File > Import
    every time.

    Also guards against schema drift: if the file already exists from
    before this column set (or delimiter) was introduced, or was
    hand-edited into a different shape, blindly appending with a fixed
    fieldnames list would write misaligned data under a stale header
    with no warning. Instead, if an existing header doesn't match, the
    old file is preserved untouched under a timestamped backup name and
    a fresh file with the current header is started -- so nothing
    already logged is silently corrupted or lost.
    """
    file_exists = os.path.isfile(log_path)
    if file_exists:
        with open(log_path, newline="") as f:
            existing_header = next(csv.reader(f, delimiter="\t"), None)
        if existing_header is not None and existing_header != fieldnames:
            backup_path = f"{log_path}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
            os.replace(log_path, backup_path)
            print(f"NOTE: {log_path} had a different column layout -- "
                  f"preserved as {backup_path} and started a new log with the current columns.")
            file_exists = False

    log_dir = os.path.dirname(log_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    with open(log_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Main capture handler
# ---------------------------------------------------------------------------

# Manual exception tags -- these describe things only the person watching
# the encounter can know (a rematch, an atlas passive or scarab changing
# infamy odds), never something the pipeline could infer from pixels.
EXCEPTION_KEYS = {"r": "Rematch", "n": "No Infamous chance", "i": "Infamy/Renown"}
EXCEPTION_PROMPT_TIMEOUT = 10  # seconds

# Guards against two captures' tag prompts overlapping. If a second F9
# lands while an earlier capture is still waiting on R/N/I, the SAME
# three keys would otherwise end up bound twice over -- a single
# keypress would then satisfy both prompts at once and tag whichever
# capture didn't actually match what was pressed. Simplest safe
# behavior: only one prompt is ever active; a capture that arrives while
# one is already running just logs with no tag rather than competing
# for the same key bindings.
_tagging_lock = threading.Lock()


def _prompt_for_exception(timeout: float = EXCEPTION_PROMPT_TIMEOUT) -> str:
    """Gives the user a short window to tag a just-finished capture with
    one of EXCEPTION_KEYS before its CSV row is written. Returns the
    chosen label, or "" if the window times out with nothing pressed.

    Runs in its own thread (see _defer_capture below), not the keyboard
    library's hook thread -- blocking that thread for up to `timeout`
    seconds would also delay it noticing the quit key or the next F9
    press for the same reason a slow/failing capture callback could
    (see _safe_on_capture).
    """
    result = {"label": ""}
    event = threading.Event()

    def _make_handler(label):
        def _handler():
            result["label"] = label
            event.set()
        return _handler

    handles = [
        keyboard.add_hotkey(key, _make_handler(label))
        for key, label in EXCEPTION_KEYS.items()
    ]
    # ENTER ends the wait immediately without changing whatever label (if
    # any) was already chosen -- lets the write be forced through right
    # away instead of always sitting out the full timeout.
    handles.append(keyboard.add_hotkey("enter", event.set))

    key_hint = ", ".join(f"[{k.upper()}]={v}" for k, v in EXCEPTION_KEYS.items())
    print(f"  Tag this capture? {key_hint} -- [ENTER] to skip wait -- "
          f"{timeout:.0f}s to respond, otherwise left blank.")

    event.wait(timeout=timeout)

    for handle in handles:
        keyboard.remove_hotkey(handle)

    return result["label"]


def copy_row_to_clipboard(row: dict):
    """Copies the row as a single tab-separated line to the system
    clipboard, ready to paste directly as a new row in Google Sheets (or
    Excel) -- pasting tab-separated text is what both already expect
    from a plain clipboard paste, no Import step needed. Silently skips
    if pyperclip isn't installed or the OS clipboard isn't reachable
    (e.g. no display server) -- this is a convenience, not something
    that should ever block or fail a capture."""
    if not _CLIPBOARD_AVAILABLE:
        return
    try:
        pyperclip.copy("\t".join(str(v) for v in row.values()))
    except Exception:
        pass


def _defer_capture(record: dict, row: dict, session_dir: str):
    """Waits for an optional exception tag, then writes and prints the
    final row. Runs off the keyboard hook thread entirely (see the
    docstrings on _prompt_for_exception and _safe_on_capture for why
    that matters) -- daemon=True so a still-pending tag prompt can't
    keep the process alive after the quit key is pressed.
    """
    try:
        if _tagging_lock.acquire(blocking=False):
            try:
                row["Exceptions"] = _prompt_for_exception()
            finally:
                _tagging_lock.release()
        else:
            print("  (another capture was triggered -- logging this one without exception)")

        append_log(row)
        copy_row_to_clipboard(row)
        print(f"[{record['timestamp']}] captured -> {session_dir}  " +
              "  ".join(f"{k}={v!r}" for k, v in row.items()))
    except Exception:
        import traceback
        traceback.print_exc()
        print("Logging this capture failed (see traceback above).")


def on_capture(crop_plan):
    full = capture_full_screen()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = os.path.join(CAPTURE_DIR, ts)
    os.makedirs(session_dir, exist_ok=True)

    crops = {}
    for region_name, box in crop_plan.items():
        crop = full.crop(box)
        crops[region_name] = crop
        crop.save(os.path.join(session_dir, f"{region_name}.png"))

    record = process_capture(crops)
    row = build_log_row(record)  # Exceptions starts blank; _defer_capture may fill it

    warrant_extracted_path = os.path.join(session_dir, "warrant_extracted.txt")
    with open(warrant_extracted_path, "w", newline="\n") as f:
        f.write(build_warrant_extracted_text(record))

    threading.Thread(target=_defer_capture, args=(record, row, session_dir), daemon=True).start()


def _safe_on_capture(crop_plan):
    """Wraps on_capture so a failure during any single capture (a bad OCR
    read, an icon-match error, a file-write hiccup) can never propagate
    into the keyboard library's own internal event-dispatch thread. An
    uncaught exception there can kill that thread outright -- and since
    it's the same thread responsible for detecting ALL further key
    events, the practical symptom is the whole script going dead (or
    exiting) after exactly one capture, with no further hotkey presses
    -- including the quit key -- ever being noticed again. Catching here
    means a bad capture gets logged and skipped instead of taking the
    whole listener down with it."""
    try:
        on_capture(crop_plan)
    except Exception:
        import traceback
        traceback.print_exc()
        print("Capture failed (see traceback above) -- still listening for the next press.")


def main():
    regions, calib_res = load_regions()
    crop_plan = build_crop_plan(regions)

    with mss.MSS() as sct:
        current_res = (sct.monitors[1]["width"], sct.monitors[1]["height"])
    if tuple(calib_res) != current_res:
        print(f"WARNING: config was calibrated at {calib_res}, "
              f"current screen is {current_res}. Crops will likely be misaligned -- "
              f"re-run the calibration step at this resolution.")

    print(f"Ready. Press [{HOTKEY.upper()}] over a paused mercenary encounter to capture. "
          f"Press Ctrl+C to quit.")

    keyboard.add_hotkey(HOTKEY, lambda: _safe_on_capture(crop_plan))

    # A plain sleep loop rather than a keyboard-registered quit hotkey --
    # Ctrl+C (SIGINT) is handled by Python's own default signal handling
    # in the main thread regardless of what the keyboard library's
    # internal hook thread is doing, so this doesn't depend on that
    # library's exit-handling behavior at all (the same reasoning that
    # moved capture logging off keyboard.wait() earlier applies here,
    # just more directly: no keyboard-library call is involved in
    # detecting quit at all now).
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nCtrl+C pressed -- exiting.")


if __name__ == "__main__":
    main()
