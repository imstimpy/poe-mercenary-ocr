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
from collections import Counter
from datetime import datetime

import numpy as np
from scipy import ndimage
import cv2
import mss
from PIL import Image, ImageOps
import pytesseract
import keyboard  # global hotkey listener
import onnxruntime as ort  # gem-presence embedding model -- see is_gem_present's docstring

try:
    import pyperclip
    _CLIPBOARD_AVAILABLE = True
except ImportError:
    _CLIPBOARD_AVAILABLE = False

CONFIG_PATH = "definitions/mercenary_regions.json"
CAPTURE_DIR = "captures"
CAMPAIGN_CAPTURE_DIR = "captures_campaign"
LOG_PATH = "logs/mercenary_log.tsv"
HOTKEY = "f9"

# Path of Exile's live log file -- used to look up which map the most
# recent encounter happened in. This is the standard default install
# location; change it if PoE is installed elsewhere.
CLIENT_LOG_PATH = r"C:\Program Files (x86)\Grinding Gear Games\Path of Exile\logs\Client.txt"

# Fixed TSV/console column set.
LOG_FIELDNAMES = ["Timestamp", "Name", "Type", "Infamous", "Gem 1", "Gem 2", "Gem 3", "Gem 4", "Map", "Exceptions"]

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

# Mercenary names can legitimately contain an apostrophe ("Ven'zi Kaldri").
# Tesseract sometimes reads that glyph as a real apostrophe, sometimes as a
# curly quote, and sometimes (confirmed on a real capture) as the Unicode
# replacement character � -- all three must stay INSIDE the run (not
# just tolerated at the edges, where the leading/trailing [A-Za-z] anchors
# already exclude them) or the run breaks into two fragments at the
# apostrophe and max(matches, key=len) silently keeps the wrong (often
# longer, name-suffix-only) fragment instead of the full name.
_WORD_RUN_RE = re.compile(r"[A-Za-z][A-Za-z,.\-'‘’� ]*[A-Za-z]")
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
# A second, distinct kind of leading noise -- confirmed on a real
# non-Infamous capture ("ae Ruktara, the Unrelenting", true name
# "Ruktara, the Unrelenting"): background-art texture at the crop's edge
# read by --psm 11 as its own short lowercase word, and -- unlike the
# "Infamous" icon-bleed case above -- landed space-joined onto the real
# name INSIDE the same _WORD_RUN_RE match rather than as a separable
# second fragment, so the longest-run heuristic can't strip it on its
# own. A real mercenary name always starts with a capitalized proper
# name (never a lowercase word), so any short all-lowercase leading word
# here is guaranteed noise, not clipped real content.
_STRAY_NAME_PREFIX_RE = re.compile(r"^[a-z]{1,3}\s+(?=[A-Z])")
# Same background-art noise, mirrored on the crop's RIGHT edge (confirmed
# on a real capture, "oe Pradin Prowl-linger af", true name "Pradin
# Prowl-linger" -- both edges of the isolated row show the same speckled
# noise texture by eye). A real mercenary name never ends in a lowercase
# word either (every known real name ends capitalized, incl. the last
# word of a "Name, the Title" or hyphenated-surname form), so this is
# the safe suffix mirror of _STRAY_NAME_PREFIX_RE above.
#
# A THIRD kind of edge noise -- confirmed on a real capture ("Alak
# Prowl-linger PORE", true name "Alak Prowl-linger"): not background-art
# texture this time, but an actual on-screen orange UI badge ("ORB")
# sitting inside the wide/generous name crop, misread by --psm 11 as
# "PORE" and space-joined onto the name the same way. Deliberately a
# SEPARATE all-uppercase alternative rather than widening the lowercase
# class above to be case-insensitive: a real title can legitimately end
# in a short Title-Case word (e.g. "the Azadin Howler"), which must
# never be stripped, but no real name segment is ever rendered fully
# uppercase, so requiring EVERY letter to be uppercase is what keeps
# this safe regardless of the noise token's length. No leading-edge
# instance of this confirmed yet -- add a _STRAY_NAME_PREFIX_RE mirror
# if/when one turns up, rather than guessing at it now.
_STRAY_NAME_SUFFIX_RE = re.compile(r"(?<=[A-Za-z])\s+(?:[a-z]{1,3}|[A-Z]{1,6})$")


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
    # Normalize every apostrophe-like glyph _WORD_RUN_RE let through mid-run
    # (curly quotes, and Tesseract's � misread) to a plain apostrophe.
    for glyph in ("‘", "’", "�"):
        best = best.replace(glyph, "'")
    best = _NAME_COMMA_FIX_RE.sub(r"\1, the", best)
    best = _STRAY_ICON_PREFIX_RE.sub("", best)
    best = _STRAY_NAME_PREFIX_RE.sub("", best)
    best = _STRAY_NAME_SUFFIX_RE.sub("", best)
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


def _max_skill_count_for_type(mercenary_type: str, path: str = SKILLS_DEFINITION_PATH):
    """Returns PrimaryCount+SecondaryCount+UtilityCount for this type+infamy
    combination -- the full on-screen skill-row count once every slot is
    unlocked (level 83 in every real capture seen so far) -- or None if
    the type isn't recognized. Same infamy fallback as
    known_skills_for_type. Used by extract_skill_names to recognize when
    every real skill for this type has already been matched, so anything
    left over can't be one more skill, whatever it looks like."""
    if not mercenary_type:
        return None
    mercenaries = _load_mercenary_skills(path)
    entry = mercenaries.get(mercenary_type)
    if entry is None:
        entry = mercenaries.get(_INFAMOUS_PREFIX_RE.sub("", mercenary_type).strip())
    if entry is None:
        return None
    return entry.get("PrimaryCount", 0) + entry.get("SecondaryCount", 0) + entry.get("UtilityCount", 0)


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


def match_skill_name(candidate: str, mercenary_type: str = None, cutoff: float = 0.6, warn: bool = True) -> str:
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
    either -- the original candidate is returned as-is, per the same
    reasoning as match_mercenary_type: a wrong "closest" guess is worse
    than an unresolved value flagged for manual review. `warn=False`
    (used by extract_skill_names' individual OCR passes) suppresses the
    NOTE here, since a single pass failing to match is meaningless noise
    on its own -- extract_skill_names logs its own NOTE, once, only for
    a skill that's still unresolved after picking the better-performing
    pass.

    Two real, reproduced failure modes found via a full real-warrant.txt-
    vs-regenerated audit (see AI_RAMBLINGS.md) sit ahead of the plain
    difflib match below, both because raw ratio() alone got the wrong
    answer with real capture data on hand to prove it:

    1. Exact-prefix preference. A skill name that visually wraps to two
       on-screen lines (see extract_skill_names' docstring) produces a
       first OCR line that's a complete, literal prefix of the true full
       name -- e.g. "Leap Slam of" for "Leap Slam of Groundbreaking".
       ratio() rewards raw character overlap regardless of length, so a
       mercenary that ALSO has the shorter "Leap Slam" as an independent
       real skill saw "leap slam of" score higher against "leap slam"
       (0.857) than against the correct, longer "leap slam of
       groundbreaking" (0.615) -- confirmed on two independent real
       captures. An exact prefix relationship is a far stronger, more
       specific signal than any ratio score, so it's checked first and
       trusted outright, but only when exactly one pool entry qualifies
       -- more than one real prefix match is rare enough to fall through
       to ordinary scoring rather than guess between them.
    2. Near-tie first-word disambiguation. Icon-bleed noise can glue a
       short garbage token onto the front of a real line AND garble part
       of the real name itself in the same reading (confirmed real case:
       "GLACIAL HAMMER" read as "dl GLACIAL Eee"), landing ratio() in a
       genuine near-tie between the correct name and an unrelated one
       that happens to share a similar length/letter overlap ("Glacial
       Hammer" 0.643 vs "Vaal Glacial Hammer" 0.667 -- the wrong, longer
       name narrowly won). Rather than trust a sub-0.05 margin, this
       strips candidate's own leading word (the suspected noise token)
       and checks which of the top two candidates' OWN first word then
       matches -- "glacial" unambiguously belongs to "Glacial Hammer",
       not "Vaal Glacial Hammer". Only trusted when it picks exactly one
       of the two; otherwise falls through to the same "don't guess, log
       it" refusal as a below-cutoff read.
    """
    if not candidate:
        return candidate
    pool = known_skills_for_type(mercenary_type) or all_known_skills()
    if not pool:
        return candidate
    lower_candidate = candidate.lower()
    lower_to_original = {s.lower(): s for s in pool}

    # A candidate that's ALREADY a complete, exact match for some pool
    # entry always wins outright -- checked before the prefix heuristic
    # below specifically because a real, valid, standalone skill name can
    # itself be a strict prefix of a DIFFERENT real skill's name (e.g.
    # "Leap Slam" is both its own real skill AND a prefix of "Leap Slam
    # of Groundbreaking"); without this, the prefix rule would wrongly
    # "correct" a perfectly correct exact read into the longer name every
    # time (confirmed: broke the real "Leap Slam" fixtures below the
    # first version of this fix was tested against).
    if lower_candidate in lower_to_original:
        return lower_to_original[lower_candidate]

    # Exact-prefix preference, but ONLY when the candidate is at least as
    # long as the shortest real name in this pool -- otherwise a short
    # icon-bleed noise token is trivially a "prefix" of some unrelated
    # long name by pure chance (confirmed real regression: "tr", 2
    # characters of noise, is a literal prefix of "Triggerblades" and got
    # wrongly promoted to a full match before this length floor existed,
    # even though "tr" is shorter than every real skill name and could
    # never be one on its own -- same floor extract_skill_names' own
    # shape check applies for exactly this reason).
    min_pool_len = min((len(p) for p in lower_to_original), default=0)
    if len(lower_candidate) >= min_pool_len:
        prefix_matches = [
            lower for lower in lower_to_original
            if lower != lower_candidate and lower.startswith(lower_candidate)
        ]
        if len(prefix_matches) == 1:
            return lower_to_original[prefix_matches[0]]

    matches = difflib.get_close_matches(lower_candidate, lower_to_original.keys(), n=2, cutoff=cutoff)
    if not matches:
        if warn:
            _note(f"skill {candidate!r} didn't confidently match any known skill "
                  f"(mercenary_type={mercenary_type!r}) -- logged as-is; check "
                  f"definitions/skills_by_mercenary.json if this is a new/valid skill.")
        return candidate
    if len(matches) == 1:
        return lower_to_original[matches[0]]

    best, second = matches[0], matches[1]
    best_score = difflib.SequenceMatcher(None, lower_candidate, best).ratio()
    second_score = difflib.SequenceMatcher(None, lower_candidate, second).ratio()
    if best_score - second_score >= 0.05:
        return lower_to_original[best]

    # Near-tie: one of the two pool entries is often exactly the other
    # with one or more extra LEADING words (the common "Vaal X" naming
    # pattern) -- resolved by checking whether that extra word actually
    # appears anywhere in the raw candidate text. OCR reliably reads a
    # whole extra word when it's really there far more often than it
    # garbles WITHIN one, so its total absence is strong evidence the
    # shorter name is correct. Confirmed on two independent real cases
    # ratio() got wrong by a narrow margin: "Burning Arrow" vs "Vaal
    # Burning Arrow" (candidate had no "vaal" anywhere), "Glacial Hammer"
    # vs "Vaal Glacial Hammer" (candidate's second word was itself
    # garbled into "Eee", but still no "vaal").
    best_words, second_words = best.split(), second.split()
    resolved = None
    if len(best_words) < len(second_words) and second_words[len(second_words) - len(best_words):] == best_words:
        extra = " ".join(second_words[:len(second_words) - len(best_words)])
        resolved = best if extra not in lower_candidate else second
    elif len(second_words) < len(best_words) and best_words[len(best_words) - len(second_words):] == second_words:
        extra = " ".join(best_words[:len(best_words) - len(second_words)])
        resolved = second if extra not in lower_candidate else best
    if resolved:
        return lower_to_original[resolved]
    if warn:
        _note(f"skill {candidate!r} matched two known skills almost equally well "
              f"({lower_to_original[best]!r} vs {lower_to_original[second]!r}) -- "
              f"logged as-is rather than guessing; check definitions/skills_by_mercenary.json.")
    return candidate


_CURLY_QUOTES_RE = re.compile(r"[\u2018\u2019]")


# Every real skill name across all 65 mercenary type+infamy combinations
# (definitions/skills_by_mercenary.json) is built only from these
# characters -- confirmed by checking all of them directly (the one
# exception, "[DNT] Unused", is a placeholder already excluded from the
# usable pool elsewhere). A candidate containing anything outside this
# set can't be a real skill regardless of OCR pass.
_SKILL_NAME_CHARS_RE = re.compile(r"^[A-Za-z' :]+$")


_SKILL_OCR_PSM_MODES = (3, 6)


def _extract_skill_names_pass(crop: Image.Image, mercenary_type: str, psm: int) -> list:
    """Real, reproduced bug (see AI_RAMBLINGS.md): `image_to_string`'s raw
    text stream trusts Tesseract's own internal block-traversal order,
    which isn't always true top-to-bottom screen order -- confirmed on
    two independent real captures where one skill's line came out dead
    last in the stream despite sitting 4th-of-6 on screen, with every
    OTHER line read and fuzzy-matched correctly (so this isn't an OCR
    accuracy problem `match_skill_name` could ever fix). `image_to_data`
    exposes each recognized word's own (block, paragraph, line) grouping
    and pixel `top` position; grouping words into lines by that triple
    and then sorting LINES by `top` recovers genuine visual order
    regardless of which order Tesseract's own segmentation happened to
    traverse them in.
    """
    data = pytesseract.image_to_data(crop, config=f"--psm {psm}", output_type=pytesseract.Output.DICT)
    lines_by_key = {}
    for i, text in enumerate(data["text"]):
        text = text.strip()
        if not text:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        entry = lines_by_key.setdefault(key, {"words": [], "top": data["top"][i]})
        entry["words"].append((data["left"][i], text))
        entry["top"] = min(entry["top"], data["top"][i])
    ordered = sorted(lines_by_key.values(), key=lambda entry: entry["top"])
    lines = [" ".join(word for _left, word in sorted(entry["words"])) for entry in ordered]
    lines = [_CURLY_QUOTES_RE.sub("'", line) for line in lines]
    return [match_skill_name(line, mercenary_type, warn=False) for line in lines]


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
    parse_mercenary_type). A glued-on prefix is absorbed by
    match_skill_name's fuzzy pool match same as always. A LONE garbage
    line can never fuzzy-match anything, so it's dropped outright rather
    than kept as a fake "unresolved skill" candidate, via two layered
    checks:

    1. Quota check (primary, most reliable): if every real skill for
       this type is ALREADY matched (`_max_skill_count_for_type`'s
       PrimaryCount+SecondaryCount+UtilityCount), any extra unmatched
       line can't be one more skill no matter what it looks like --
       there's nothing left to be. This is what actually explains every
       real lone-garbage-line case confirmed so far, including ones
       that don't share any obvious shape (a Fallen Reverend capture
       produced 'CSaee' -- 5 letters, ordinary shape, no length or
       character-class tell at all) -- and, as a side effect, resolves
       a second known false alarm the same way: a long skill name that
       visually wraps to two on-screen lines (e.g. "CORRUPTED BLADE
       VORTEX" / "OF THE SCYTHE") produces a real second OCR line that
       can't match anything on its own, previously surfaced as its own
       spurious NOTE ('THE SCYTHE') despite nothing being missing --
       once the quota's already full, this drops the same way.
    2. Shape check (fallback, for when the quota ISN'T yet full -- an
       unrecognized type, or a real lower-level mercenary showing fewer
       than its full skill count, still unconfirmed either way): too
       short to be real (shorter than every real skill name in the
       pool -- the shortest confirmed real one is "Arc", 3 characters)
       OR containing a character no real skill name has ever used (see
       _SKILL_NAME_CHARS_RE) -- either alone is enough to drop it,
       since a real skill name violates neither. Confirmed real across
       two other independent captures: a Combatant capture produced
       '~', 'ri', 'mi', 'bi', '(R)' as extra one-or-two-character
       lines, a Kineticist capture produced 'ear \xa2' (5 characters,
       past this check's length floor but caught by its character
       check) -- both read from the skill icons' own art bleeding into
       the OCR crop.

    Either check is what match_skill_name's fuzzy match alone can't do
    on its own (it has no concept of "this can't be real"), and both
    keep such lines out of warrant_generated.txt's "unrecognized skill
    row(s), dropped" footer, where they'd otherwise look like a real
    skill got missed even though nothing was actually lost.

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
    min_skill_length = min((len(s) for s in pool), default=0)
    max_skill_count = _max_skill_count_for_type(mercenary_type)
    passes = [_extract_skill_names_pass(crop, mercenary_type, psm) for psm in _SKILL_OCR_PSM_MODES]
    chosen = max(passes, key=lambda lines: sum(1 for name in lines if name in pool))
    matched_count = len({name for name in chosen if name in pool})
    quota_full = max_skill_count is not None and matched_count >= max_skill_count
    result = []
    for name in chosen:
        if name in pool:
            if result and result[-1] == name:
                # A skill name that visually wraps to two on-screen lines
                # can have BOTH the truncated first line (resolved via
                # match_skill_name's exact-prefix preference) and the bare
                # continuation line independently fuzzy-match the SAME
                # full name -- confirmed real case: "Leap Slam of
                # Groundbreaking" wrapped to "LEAP SLAM OF" / "GROUNDBREAKING",
                # both correctly resolving to this name on their own, which
                # would otherwise produce two rows for one real skill and
                # shift every later row's alignment by one. A mercenary's
                # skill list never contains the same name twice (unlike
                # supports -- see _resolve_same_skill_collisions, this
                # doesn't apply the other way), so an immediately adjacent
                # duplicate is always this wrap artifact, never a genuine
                # repeat.
                continue
            result.append(name)
            continue
        if quota_full:
            continue  # every real skill for this type is already accounted for -- can't be one more, whatever this looks like
        if len(name) < min_skill_length or not _SKILL_NAME_CHARS_RE.match(name):
            continue  # structurally can't be a real skill -- icon-bleed noise, not worth flagging
        result.append(name)
        _note(f"skill {name!r} didn't confidently match any known skill in "
              f"either OCR pass (mercenary_type={mercenary_type!r}) -- logged "
              f"as-is; check definitions/skills_by_mercenary.json if this is "
              f"a new/valid skill.")
    return result


_LEVEL_DIGITS_RE = re.compile(r"(\d+)")

# Mercenary levels track the game's own character level cap (100) -- real
# captures on hand span 27-83. Used only as an implausibility check on a
# --psm 11 reading (see extract_level), not as a validated ceiling in its
# own right.
_LEVEL_PLAUSIBLE_MAX = 100


def extract_level(crop: Image.Image):
    """OCRs the level region ("Lvl NN") and returns the level as an int,
    or None if no digits were found under either pass.

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

    Real, reproduced failure once real mss data existed below the 8
    calibration screenshots' 66-83 range: a genuinely clean "Lvl 32" crop
    (test_data/mercenary_levels/lvl32.png) reads as "Lvl 5250" under
    --psm 11 -- image_to_data shows this as ONE low-confidence word
    token, not multiple merged fragments, i.e. --psm 11 is misreading the
    "32" glyphs themselves, not picking up border/corner noise the way
    --psm 7/6 did. OCR confidence itself doesn't separate this from
    already-correct reads (this case's conf=38 sits BETWEEN several
    correct reads' own confidences, e.g. Lvl 83 at conf=0 and Lvl 79 at
    conf=26 -- checked directly against every real crop on hand, not
    assumed), so it can't be the trigger. Implausibility of the VALUE can
    be, though: no real mercenary level is 5250. When the --psm 11 result
    is missing or exceeds _LEVEL_PLAUSIBLE_MAX, retry with --psm 3, which
    reads this specific crop correctly (32) precisely because it uses
    different glyph segmentation -- confirmed against all 10 real crops
    on hand this never overrides an already-correct --psm 11 reading,
    since --psm 11 is only wrong on this one.
    """
    raw = pytesseract.image_to_string(crop, config="--psm 11").strip()
    match = _LEVEL_DIGITS_RE.search(raw)
    level = int(match.group(1)) if match else None
    if level is not None and level <= _LEVEL_PLAUSIBLE_MAX:
        return level

    raw = pytesseract.image_to_string(crop, config="--psm 3").strip()
    match = _LEVEL_DIGITS_RE.search(raw)
    return int(match.group(1)) if match else None


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


_DISPLAY_NAME_LOWERCASE_WORDS = {"of"}


def display_name_from_slug(slug: str) -> str:
    """Converts an assets/ filename slug back to a human-readable
    display name for console/log output (e.g. "spectral_helix_of_trarthus"
    -> "Spectral Helix of Trarthus"). The on-disk reference filename
    always stays lowercase_with_underscores per the naming convention --
    this only affects how the matched name is displayed, never how it's
    stored. Every real "<X> of <Y>" gem name in definitions/
    gems_by_mercenary.json keeps "of" lowercase and never leads with it,
    so it's the one word excluded from capitalization; every other word
    is capitalized (a real regression caught this: Sanguimancer's gem
    displayed as "Storm Call Of Trarthus" before this fix)."""
    return " ".join(
        word if word in _DISPLAY_NAME_LOWERCASE_WORDS else word.capitalize()
        for word in slug.split("_")
    )


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
# SUPERSEDED -- kept only as the historical record of why pixel
# correlation was abandoned for gem presence (three real false-positive
# misfires, each threshold nudge closer to the gem floor than the last,
# until this one and RUCKSACK_GEM_PRESENCE_THRESHOLD below stopped being
# separable by any threshold at all). No longer read by is_gem_present().
#
# A same-day frozen-ResNet18-embedding test (see assets/README.md)
# separates the same two samples cleanly (gem floor 0.9250, non-gem
# ceiling 0.7564, ~0.17 margin) -- a validated candidate fix, not yet
# adopted here since it would make torch/torchvision a hard production
# dependency (see assets/README.md's install-burden discussion) rather
# than the optional/experimental role it's had so far this session.
GEM_PRESENCE_THRESHOLD = 0.755

# SUPERSEDED -- see GEM_PRESENCE_THRESHOLD above. Real, measured margin
# from validating this against every real known-gem and known-false-
# positive session on hand (8 vs. 9 -- see assets/README.md, "Multi-
# scale/position search for gem PRESENCE"): real gem floor 0.8209, real
# non-gem ceiling 0.7966. No longer read by is_gem_present_in_rucksack().
RUCKSACK_GEM_PRESENCE_THRESHOLD = 0.8088

# Real validation numbers (AI_RAMBLINGS.md's "Embeddings for gem
# PRESENCE" and its ONNX-export follow-up): a frozen, ImageNet-pretrained
# ResNet18's penultimate-layer features, compared by cosine similarity,
# checked against every real gem sample and every real non-gem sample on
# hand (35 vs. 62) -- real gem floor 0.9202, real non-gem ceiling 0.8273,
# ZERO overlap. This threshold sits at their midpoint, same convention as
# every presence threshold before it, but with a real ~0.093 margin
# instead of the razor's edge that eventually broke pixel correlation
# three times over (GEM_PRESENCE_THRESHOLD/RUCKSACK_GEM_PRESENCE_THRESHOLD
# above). Unlike the pixel-correlation approach, plain isolated per-
# quadrant crops score well here with no multi-scale/position search
# needed -- a global-average-pooled CNN embedding isn't nearly as
# sensitive to small alignment differences as raw pixel correlation was,
# so is_gem_present_in_rucksack() no longer needs _glue_rucksack_region/
# _icon_similarity_score_multiscale at all (both kept below, unused for
# presence now, since they're still relevant to the separate, still-open
# identity-matching improvement idea logged in AI_RAMBLINGS.md).
GEM_EMBEDDING_PRESENCE_THRESHOLD = 0.8737

GEM_EMBEDDING_MODEL_PATH = os.path.join("assets", "models", "gem_embedding_resnet18.onnx")
GEM_EMBEDDING_REFERENCES_PATH = os.path.join("assets", "models", "gem_reference_embeddings.json")

_gem_embedding_session = None
_gem_reference_embeddings_cache = None


def _load_gem_embedding_session(model_path: str = GEM_EMBEDDING_MODEL_PATH):
    """Loads (once, cached) the ONNX gem-embedding model via onnxruntime.
    See tools/export_gem_embedding_model.py for how this file is built
    and why ONNX rather than shipping torch/torchvision directly:
    onnxruntime is a light, inference-only dependency (~46MB installed,
    measured) versus torch's ~546MB -- the export itself needs the full
    torch stack, but only as a one-time dev-side step, not something end
    users of this pipeline need. Returns None (callers fail closed, not
    crash) if the model hasn't been exported yet."""
    global _gem_embedding_session
    if _gem_embedding_session is None:
        if not os.path.isfile(model_path):
            return None
        _gem_embedding_session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    return _gem_embedding_session


def _load_gem_reference_embeddings(path: str = GEM_EMBEDDING_REFERENCES_PATH) -> dict:
    """Loads (once, cached) the precomputed {gem name: 512-d embedding}
    reference set -- generated once by tools/export_gem_embedding_model.py,
    never recomputed at capture time (embedding all 7 references on every
    single capture would work too, just slower for no benefit -- they
    never change between runs)."""
    global _gem_reference_embeddings_cache
    if _gem_reference_embeddings_cache is None:
        if not os.path.isfile(path):
            _gem_reference_embeddings_cache = {}
        else:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            _gem_reference_embeddings_cache = {
                name: np.array(vec, dtype=np.float32) for name, vec in data["embeddings"].items()
            }
    return _gem_reference_embeddings_cache


# ImageNet normalization constants -- must match tools/export_gem_embedding_model.py's
# torchvision.transforms.Normalize exactly, since this reimplements that
# same preprocessing with plain PIL/numpy rather than torchvision (production
# only carries onnxruntime, not torch/torchvision -- the whole point of the
# ONNX export).
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _embed_crop(crop: Image.Image, session) -> np.ndarray:
    """Runs one crop through the embedding model, returning its
    L2-normalized 512-d feature vector. Resize uses BILINEAR explicitly
    -- torchvision.transforms.Resize's own default -- so this matches
    tools/export_gem_embedding_model.py's preprocessing exactly rather
    than drifting on Pillow's own default resize filter."""
    img = crop.convert("RGB").resize((224, 224), Image.BILINEAR)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    arr = (arr - _IMAGENET_MEAN) / _IMAGENET_STD
    arr = arr.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)  # NCHW
    vec = session.run(["embedding"], {"input": arr})[0][0]
    return vec / np.linalg.norm(vec)


def _gem_embedding_score(crop: Image.Image) -> float:
    """Best cosine similarity between `crop`'s embedding and every
    reference gem's precomputed embedding -- the coarse "is this ANY
    gem" signal is_gem_present()/is_gem_present_in_rucksack() accept
    against GEM_EMBEDDING_PRESENCE_THRESHOLD. Returns -1.0 (fails every
    real threshold) rather than raising if the exported model or
    reference file isn't present -- a missing export should degrade to
    "no gem detected", not crash a live capture."""
    session = _load_gem_embedding_session()
    refs = _load_gem_reference_embeddings()
    if session is None or not refs:
        return -1.0
    vec = _embed_crop(crop, session)
    return max(float(np.dot(vec, ref_vec)) for ref_vec in refs.values())


def is_gem_present(crop: Image.Image, threshold: float = GEM_EMBEDDING_PRESENCE_THRESHOLD) -> bool:
    """Checks whether a rucksack slot contains *some* known gem, without
    attempting to identify *which* one -- see GEM_EMBEDDING_PRESENCE_THRESHOLD
    for the real validation numbers this is calibrated against. Used as
    the first step of the gem-detection stopgap: presence here, identity
    resolved afterward from the mercenary's own type (see
    resolve_gem_presence).

    Uses embeddings (a frozen ResNet18's penultimate-layer features, via
    _gem_embedding_score), not the pixel-correlation `_icon_similarity_score`
    this function used previously -- see GEM_PRESENCE_THRESHOLD's code
    comment above for why that approach was abandoned.
    """
    return _gem_embedding_score(crop) >= threshold


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
    process_capture's crop dict).

    NOT used by is_gem_present_in_rucksack() anymore -- embeddings
    (see GEM_EMBEDDING_PRESENCE_THRESHOLD) don't need the windowed
    multi-scale search this was built for, since a CNN embedding isn't
    nearly as sensitive to small alignment differences as raw pixel
    correlation was. Kept because it's still relevant to the separate,
    still-open identity-matching improvement idea logged in
    AI_RAMBLINGS.md ("the natural next step for better gem
    differentiation is applying _icon_similarity_score_multiscale to
    identity matching generally").
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


def is_gem_present_in_rucksack(crops: dict, threshold: float = GEM_EMBEDDING_PRESENCE_THRESHOLD) -> dict:
    """Per-quadrant gem presence for a whole rucksack at once -- the
    whole-rucksack-aware entry point process_capture() uses instead of
    four independent is_gem_present() calls, kept as its own function
    (not a signature change to is_gem_present) purely so multi-gem
    counting (up to 4 per encounter) stays explicit about scoring each
    quadrant independently, not collapsing to a single whole-rucksack
    yes/no.

    Used to do real windowed multi-scale/position search
    (_icon_similarity_score_multiscale against _glue_rucksack_region)
    to work around pixel correlation's alignment sensitivity -- no
    longer needed now that both this and is_gem_present() score via
    embeddings (see GEM_EMBEDDING_PRESENCE_THRESHOLD), which isn't
    sensitive to small alignment differences the same way. Each
    quadrant is now just an independent is_gem_present()-equivalent
    call on its own isolated crop.

    Returns {"rucksack_top_left": bool, ...} -- same four keys
    process_capture() already stores in its record.
    """
    quadrants = ("rucksack_top_left", "rucksack_top_right",
                 "rucksack_bottom_left", "rucksack_bottom_right")
    return {q: _gem_embedding_score(crops[q]) >= threshold for q in quadrants}


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


def disambiguate_blade_ambusher_gem(crop: Image.Image, min_margin: float = 0.02) -> str:
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
    candidates, via the same embedding model as is_gem_present (see
    GEM_EMBEDDING_PRESENCE_THRESHOLD's docstring for why embeddings
    replaced pixel correlation project-wide). That distinction matters
    regardless of scoring method: a real, reproduced near-collision
    exists between Spectral Throw and Bladefall of Trarthus (see
    assets/README.md), which would sit inside match_icon()'s normal
    min_score/min_margin search and risk a wrong answer. It's irrelevant
    here because the caller already knows, from is_gem_present() plus
    the mercenary's own type, that the crop is one of exactly these two
    -- Bladefall was never a real candidate for this slot to begin with.

    Switched from raw pixel correlation (_icon_similarity_score) after
    that approach broke on a real capture (20260915_170339): Spectral
    Throw scored HIGHER than the true answer, Spectral Helix (0.7592 vs
    0.6856) -- a wrong ranking, not just a narrow margin. Embeddings
    rank all 10 real samples on hand correctly (2 canonical crops + 7
    Spectral Helix + 1 Spectral Throw variance samples, including this
    exact previously-broken one), with margins from 0.0264 (this same
    capture, now ranked correctly but tightly) up to 0.1197. Unlike
    GEM_EMBEDDING_PRESENCE_THRESHOLD, there's no confirmed real WRONG-
    ranking case on hand to calibrate a ceiling against -- `min_margin`
    is set well below the worst observed CORRECT margin (0.0264) rather
    than at a validated floor/ceiling midpoint, so this is a lower-
    confidence bound than presence detection's, worth revisiting as
    more real Spectral Throw variance turns up (n=2 so far, still much
    thinner than Helix's n=7).

    Returns the winning gem's slug (one of BLADE_AMBUSHER_GEM_SLUGS), or
    None if the two candidates' scores don't clear min_margin apart --
    an unresolved reading is left unresolved rather than guessed, same
    policy as every other fuzzy-match function in this file.
    """
    session = _load_gem_embedding_session()
    references = _load_gem_reference_embeddings()
    refs = {name: np.array(references[name]) for name in BLADE_AMBUSHER_GEM_SLUGS if name in references}
    if session is None or len(refs) < 2:
        return None

    vec = _embed_crop(crop, session)
    scores = {name: float(np.dot(vec, ref_vec)) for name, ref_vec in refs.items()}

    best_name = max(scores, key=scores.get)
    other_name = next(n for n in BLADE_AMBUSHER_GEM_SLUGS if n != best_name)
    if scores[best_name] - scores[other_name] >= min_margin:
        return best_name
    return None


# ---------------------------------------------------------------------------
# Support icon identification -- promoted from tools/match_support_icon.py
# and tools/harvest_support_icons.py once both were validated at 100%
# against every real crop on hand (1,971 real validation crops, see
# AI_RAMBLINGS.md's "Local embedding model as the eventual support-
# matching approach"). assets/harvested_supports/ is built offline by
# harvest_support_icons.py from real warrant.txt ground truth; this only
# consumes its output (via the precomputed support_reference_embeddings.json,
# see tools/export_support_reference_embeddings.py), the same "reference
# catalog built separately, matched live here" split gems already use.
# ---------------------------------------------------------------------------

SUPPORT_REFERENCE_EMBEDDINGS_PATH = os.path.join("assets", "models", "support_reference_embeddings.json")
SUPPORTS_DEFINITION_PATH = "definitions/supports.json"
SUPPORTS_BY_SKILLS_PATH = "definitions/supports_by_skills.json"

# A cell's flat background has near-zero brightness variance; an occupied
# one has real icon art + a gold border + a tier badge, all high-contrast
# against it. Same validated value tools/harvest_support_icons.py uses
# (measured directly: ~1.0-1.3 blank, ~42 occupied).
SUPPORT_OCCUPIED_STD_THRESHOLD = 5.0

# Every real match measured on hand (700-sample check across real
# warrant.txt-backed captures) scored 0.981 or higher, so 0.95 leaves
# real margin below every known-good case. Provisional, not validated
# the same rigorous floor/ceiling way GEM_EMBEDDING_PRESENCE_THRESHOLD
# was: there's no confirmed real "wrong icon" score to calibrate a true
# gap against, since 105 of the 159 real (icon, tier) combinations are
# harvested so far (see assets/support_icon_coverage.md) -- a genuinely
# uncovered support showing up live has no proven safety net beyond
# this margin, only this margin's existence at all.
SUPPORT_MATCH_MIN_SCORE = 0.95

_support_reference_embeddings_cache = None
_supports_definition_cache = None
_support_visual_key_to_names_cache = None
_supports_by_skills_cache = None


def _load_support_reference_embeddings(path: str = SUPPORT_REFERENCE_EMBEDDINGS_PATH) -> dict:
    """Cached loader for the precomputed {visual_key: 512-d embedding}
    reference set (tools/export_support_reference_embeddings.py). Returns
    {} if the file doesn't exist -- match_support_icon() then fails
    closed (returns None) rather than crashing a live capture."""
    global _support_reference_embeddings_cache
    if _support_reference_embeddings_cache is not None:
        return _support_reference_embeddings_cache
    if not os.path.isfile(path):
        _support_reference_embeddings_cache = {}
        return _support_reference_embeddings_cache
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    _support_reference_embeddings_cache = {
        k: np.array(v, dtype=np.float32) for k, v in data.get("embeddings", {}).items()
    }
    return _support_reference_embeddings_cache


def _load_supports_definition(path: str = SUPPORTS_DEFINITION_PATH) -> dict:
    global _supports_definition_cache
    if _supports_definition_cache is not None:
        return _supports_definition_cache
    if not os.path.isfile(path):
        _supports_definition_cache = {}
        return _supports_definition_cache
    with open(path, encoding="utf-8") as f:
        _supports_definition_cache = json.load(f).get("supports", {})
    return _supports_definition_cache


def _support_visual_key_to_names(path: str = SUPPORTS_DEFINITION_PATH) -> dict:
    """Reverse lookup: visual_key ("<icon>_<tier roman, lowercase>") ->
    [(support display name, tier int, tier roman), ...] -- more than one
    entry means a real, ground-truth-CONFIRMED icon+tier collision (two
    or more real supports render pixel-identical, see
    assets/support_icon_coverage.md), not an unresolved gap;
    resolve_support_name() culls this list against the specific skill's
    own PossibleSupports before falling back to reporting every member."""
    global _support_visual_key_to_names_cache
    if _support_visual_key_to_names_cache is not None:
        return _support_visual_key_to_names_cache
    groups = {}
    for name, entry in _load_supports_definition(path).items():
        key = f"{entry['icon']}_{entry['tier_roman'].lower()}"
        groups.setdefault(key, []).append((name, entry["tier"], entry["tier_roman"]))
    _support_visual_key_to_names_cache = groups
    return _support_visual_key_to_names_cache


def _load_supports_by_skills(path: str = SUPPORTS_BY_SKILLS_PATH) -> dict:
    """Cached loader for definitions/supports_by_skills.json -- each
    skill's real PossibleSupports pool, name+tier baked into one string
    (e.g. "Cooldown Recovery II"), used to cull an icon+tier collision
    down to only the candidate(s) the specific skill in question can
    actually roll (see resolve_support_name)."""
    global _supports_by_skills_cache
    if _supports_by_skills_cache is not None:
        return _supports_by_skills_cache
    if not os.path.isfile(path):
        _supports_by_skills_cache = {}
        return _supports_by_skills_cache
    with open(path, encoding="utf-8") as f:
        _supports_by_skills_cache = json.load(f).get("skills", {})
    return _supports_by_skills_cache


def resolve_support_name(visual_key: str, skill_name: str = None):
    """Maps a resolved (icon, tier) key back to a display string ready
    for warrant_generated.txt, e.g. "Brutality (Tier: 2)".

    For a real icon+tier collision (see _support_visual_key_to_names),
    culls the candidate list against `skill_name`'s own real
    PossibleSupports pool (definitions/supports_by_skills.json) first --
    a collision at the ICON level doesn't mean every colliding name is
    actually a real option for THIS skill (confirmed directly: Holy
    Relic's pool includes "Cooldown Recovery II" but not "Shock Chance
    II"/"DoT Multiplier II"/"Chaos Penetration II", even though all four
    share one icon+tier in general). Only trusts the cull if it leaves
    at least one candidate -- an empty result would mean this specific
    (icon, tier) reading doesn't match anything `skill_name` can roll at
    all, a data inconsistency worth showing the full honest list for
    rather than hiding behind a wrong narrowing. Falls back to every
    original candidate, joined with " or " (e.g. "Minion Damage (Tier:
    3) or Minion Life (Tier: 3)") rather than guessing which one it is
    -- same "don't guess, flag it" policy as every other fuzzy-match
    function in this file. Returns None if visual_key isn't a real known
    key at all (shouldn't happen given match_support_icon() only ever
    returns a key from its own reference set, but defensive rather than
    assumed).
    """
    members = _support_visual_key_to_names().get(visual_key)
    if not members:
        return None
    if len(members) > 1 and skill_name:
        possible = set(_load_supports_by_skills().get(skill_name, {}).get("PossibleSupports", []))
        culled = [m for m in members if f"{m[0]} {m[2]}" in possible]
        if culled:
            members = culled
    return " or ".join(f"{name} (Tier: {tier})" for name, tier, _roman in members)


def _resolve_same_skill_collisions(row_supports: list) -> list:
    """A skill never rolls the exact same support twice -- confirmed
    directly against every real ground-truth capture on hand (663 real
    skill rows, zero literal duplicates). So when N slots in one row
    share the IDENTICAL unresolved collision string
    (resolve_support_name()'s "X or Y" formatting) and that collision
    has exactly N members, each candidate is forced to appear exactly
    once across those N slots -- a real capture confirmed this isn't
    hypothetical (a Reanimator's Raise Zombie of Gigantism rolled both
    "Minion Damage" and "Minion Life" at once, each printed as the same
    "...or..." string). This can't determine which PHYSICAL slot maps
    to which name (the image alone never says that), but it CAN fully
    resolve what the row actually contains -- "Minion Damage (Tier: 1)"
    once and "Minion Life (Tier: 1)" once, instead of the same
    ambiguous string printed twice, since a reader cares what the skill
    has, not which column it's in. Assigned in sorted order for a
    deterministic result, not because real slot order is known. Only
    fires when slot count matches candidate count exactly -- e.g. 2
    slots sharing a 3-way collision stays unresolved, since forcing a
    guess between which 2 of 3 would be exactly the kind of guessing
    this project's "don't guess, flag it" policy exists to avoid.
    """
    counts = Counter(row_supports)
    resolved = list(row_supports)
    for combo, count in counts.items():
        if " or " not in combo:
            continue
        members = combo.split(" or ")
        if len(members) != count:
            continue
        assignment = iter(sorted(members))
        for i, s in enumerate(resolved):
            if s == combo:
                resolved[i] = next(assignment)
    return resolved


# Same fixed badge position/thresholds validated in tools/match_support_icon.py
# (100% across all 1,971 real validation crops) -- see that file's own
# docstrings for the full derivation and the bugs found and fixed along
# the way (a 2D connected-component version that let a real bar silently
# merge with unrelated icon art, then a color-uniformity gap that let one
# icon's own gradient-shaded art masquerade as a flat badge bar).
_SUPPORT_BADGE_Y_FRAC = (0.55, 1.0)
_SUPPORT_BADGE_X_FRAC = (0.35, 1.0)
_SUPPORT_TIER_FOR_BAR_COUNT = {1: "i", 2: "ii", 3: "iii"}
_SUPPORT_BADGE_BAR_HEIGHT_FRAC = 0.35
_SUPPORT_BADGE_CORE_TRIM = 2
# Real, reproduced false NEGATIVE found via the campaign shadow-ground-truth
# audit (see AI_RAMBLINGS.md): two real Tier I "Lightning Penetration"
# crops have a genuine, correctly-located 11px badge bar (well above the
# length threshold) that reads core_std 12.0/13.3 -- a subtle
# highlight/shine gradient in this icon's own bar art, not noise. That's
# above the original 10.0 ceiling (tuned only against the poison-icon
# false positive, where real bars measured 0.0-1.7 and the fake art
# measured 31.7-56.6 -- a sample too narrow to have caught this). Raised
# to 20.0: comfortably covers both known real-bar ranges (0.0-1.7 and
# 12.0-13.3) while staying well clear of the fake-art floor (31.7).
_SUPPORT_BADGE_CORE_STD_MAX = 20.0


def _longest_true_run(column) -> tuple:
    """Returns (start_index, length) of the longest unbroken run of True
    values in `column`."""
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


def _resolve_support_tier_from_badge(crop: Image.Image):
    """Reads the tier directly off the roman-numeral badge in a fixed
    corner of a support crop, as a second opinion to match_support_icon's
    whole-crop embedding guess (which is only 88.3% accurate on exact
    tier despite 100% icon accuracy -- see tools/match_support_icon.py).
    Finds each column's longest unbroken run of the badge's gold color,
    requires the run's trimmed core to also be near-uniform in color
    (excludes gradient-shaded icon art that happens to be gold and tall),
    and counts groups of adjacent qualifying columns. Returns "i"/"ii"/
    "iii", or None if the count isn't exactly 1, 2, or 3."""
    arr = np.array(crop.convert("RGB")).astype(int)
    h, w, _ = arr.shape
    y0, y1 = int(h * _SUPPORT_BADGE_Y_FRAC[0]), int(h * _SUPPORT_BADGE_Y_FRAC[1])
    x0, x1 = int(w * _SUPPORT_BADGE_X_FRAC[0]), int(w * _SUPPORT_BADGE_X_FRAC[1])
    roi = arr[y0:y1, x0:x1]
    r, g, b = roi[..., 0], roi[..., 1], roi[..., 2]
    mask = (r > 170) & (r > b + 40) & (g > b + 15)
    roi_h = y1 - y0
    threshold = roi_h * _SUPPORT_BADGE_BAR_HEIGHT_FRAC

    qualifying_columns = []
    for x in range(mask.shape[1]):
        start, length = _longest_true_run(mask[:, x])
        if length < threshold:
            qualifying_columns.append(False)
            continue
        if length <= _SUPPORT_BADGE_CORE_TRIM * 2:
            qualifying_columns.append(True)
            continue
        core = roi[start + _SUPPORT_BADGE_CORE_TRIM:start + length - _SUPPORT_BADGE_CORE_TRIM, x]
        qualifying_columns.append(core.std(axis=0).sum() <= _SUPPORT_BADGE_CORE_STD_MAX)

    bar_count = 0
    previous_qualified = False
    for qualified in qualifying_columns:
        if qualified and not previous_qualified:
            bar_count += 1
        previous_qualified = qualified
    return _SUPPORT_TIER_FOR_BAR_COUNT.get(bar_count)


def match_support_icon(crop: Image.Image):
    """Identifies a support-icon crop's (icon, tier) identity: the
    whole-crop embedding picks the icon family (100% accurate across
    every real crop on hand), then _resolve_support_tier_from_badge()
    overrides the tier when it gets a confident reading (see that
    function's docstring for why the embedding alone under-reads tier
    specifically). Returns the resolved visual_key (e.g. "brutality_ii"),
    or None if the model/reference embeddings aren't available, or the
    best match doesn't clear SUPPORT_MATCH_MIN_SCORE -- an unresolved
    reading is left unresolved rather than guessed, same policy as
    every other fuzzy-match function in this file."""
    session = _load_gem_embedding_session()
    refs = _load_support_reference_embeddings()
    if session is None or not refs:
        return None

    vec = _embed_crop(crop, session)
    scores = {vk: float(np.dot(vec, ref_vec)) for vk, ref_vec in refs.items()}
    best = max(scores, key=scores.get)
    if scores[best] < SUPPORT_MATCH_MIN_SCORE:
        return None

    badge_tier = _resolve_support_tier_from_badge(crop)
    if badge_tier is not None:
        badge_key = f"{best.rsplit('_', 1)[0]}_{badge_tier}"
        # Checked against _support_visual_key_to_names() (every REAL (icon,
        # tier) combination in definitions/supports.json), not `refs` (only
        # what's been harvested so far) -- gating on `refs` here silently
        # broke every Tier I correction where that tier hadn't been
        # harvested yet (47 of 54 missing coverage keys are Tier I, see
        # assets/support_icon_coverage.md), since the badge reader has
        # nothing to do with whether a reference IMAGE exists for the
        # corrected tier. Real, reproduced: across 129 real crops from
        # __captures_campaign, the badge correctly read "i" 117 times, but
        # this gate let only 2 of those actually come out as tier "i".
        # resolve_support_name() only needs the visual_key string to look
        # up a name, not a reference embedding, so this doesn't need one
        # either.
        if badge_key in _support_visual_key_to_names():
            return badge_key
    return best


def _load_supports_grid(path: str = CONFIG_PATH):
    """Returns (columns, rows) as lists of (start, end) pixel offsets,
    LOCAL to an already-cropped supports.png (adjusted for the supports
    region's own absolute origin + INSET) -- same convention and same
    source data (definitions/mercenary_regions.json's regions.supports.grid)
    as tools/harvest_support_icons.py's own load_grid(), kept as a
    separate copy rather than a shared import since tools/ scripts are a
    distinct offline batch-job category from this live capture path (see
    that file's own module docstring)."""
    regions, _calib = load_regions(path)
    grid = regions["supports"]["grid"]
    origin_x = regions["supports"]["absolute"]["x0"] + INSET
    origin_y = regions["supports"]["absolute"]["y0"] + INSET
    columns = [(c["x0"] - origin_x, c["x1"] - origin_x) for c in grid["columns"]]
    rows = [(r["y0"] - origin_y, r["y1"] - origin_y) for r in grid["rows"]]
    return columns, rows


def extract_support_names(crop: Image.Image, skill_names: list = None) -> list:
    """Subdivides an already-cropped supports.png into its fixed 6-row x
    5-column icon grid and runs match_support_icon()/resolve_support_name()
    against every occupied cell (an empty cell -- this skill has fewer
    than 5 equipped supports, or fewer than 6 real skills at all -- is
    detected the same brightness-variance way tools/harvest_support_icons.py
    does).

    `skill_names`, if given, should be extract_skill_names()'s own
    per-row output (record["skills"], row-aligned by construction --
    both functions independently number rows 0-5 by the same on-screen
    grid position) -- passed through to resolve_support_name() so a real
    icon+tier collision can be culled down to only the support(s) that
    row's own skill can actually roll, per definitions/supports_by_skills.json.
    Without it, every real candidate in a collision is reported.

    Returns exactly 6 entries (one per skill row, same top-to-bottom
    order as extract_skill_names -- row 0 = Primary skill slot, etc.,
    per mercenary_regions.json's grid note), each a list of formatted
    "<name> (Tier: <n>)" strings in on-screen left-to-right order for
    that row (empty list if that row has no supports, or no skill at
    all). An occupied cell that couldn't be confidently matched becomes
    "Unknown" rather than being silently dropped -- the row still needs
    an entry at that position, same "don't guess, but don't hide it
    either" reasoning as resolve_gem_presence's "Unknown (<type>)".

    Each row is passed through _resolve_same_skill_collisions() before
    being returned -- when one skill's own row has multiple slots
    sharing the exact same unresolved icon+tier collision, and that
    collision's member count matches the slot count exactly, a real
    game constraint (a skill never rolls the same support twice)
    forces each candidate to appear once, so the row comes back fully
    resolved instead of repeating the same "X or Y" string per slot.
    """
    columns, rows = _load_supports_grid()
    arr = np.array(crop.convert("RGB")).astype(float)
    brightness = arr.mean(axis=2)

    result = []
    for row_idx, (ry0, ry1) in enumerate(rows):
        skill_name = skill_names[row_idx] if skill_names and row_idx < len(skill_names) else None
        row_supports = []
        for cx0, cx1 in columns:
            if brightness[ry0:ry1, cx0:cx1].std() < SUPPORT_OCCUPIED_STD_THRESHOLD:
                continue
            cell = crop.crop((cx0, ry0, cx1, ry1))
            visual_key = match_support_icon(cell)
            name = resolve_support_name(visual_key, skill_name) if visual_key else None
            row_supports.append(name if name else "Unknown")
        result.append(_resolve_same_skill_collisions(row_supports))
    return result


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
    skill_names = extract_skill_names(crops["skills"], mercenary_type=combined_type)
    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "mercenary_name": mercenary_name,
        "infamy": infamy,
        "mercenary_type": mercenary_type,
        "mercenary_level": extract_level(crops["level"]),
        "skills": skill_names,
        # skill_names passed through so a real icon+tier collision (see
        # resolve_support_name) can be culled to only what each row's own
        # skill can actually roll, not just reported as every candidate
        # the icon+tier could ever mean across all 271 skills.
        "supports": extract_support_names(crops["supports"], skill_names=skill_names),
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

    Supports ARE included now, one line per equipped support under its
    skill (e.g. "Brutality (Tier: 2)"), from record["supports"] --
    extract_support_names()'s per-row list, aligned by row INDEX against
    raw_skills (not the known-pool-filtered `skills` list below), since
    both extract_skill_names() and extract_support_names() independently
    number rows 0-5 by the same on-screen grid position. A real icon+tier
    collision (two or more real supports render pixel-identical, see
    assets/support_icon_coverage.md) prints as "X (Tier: N) or Y (Tier:
    N)" rather than guessing; an occupied cell that couldn't be
    confidently matched prints as "Unknown".

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
    persisted to disk in `warrant_generated.txt` every time. The full,
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
    raw_supports = record.get("supports") or []
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
    for row_idx, skill in enumerate(raw_skills):
        if skill not in known_pool:
            continue
        lines.append(skill)
        if row_idx < len(raw_supports):
            lines.extend(raw_supports[row_idx])
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


def build_log_row(record: dict, exceptions: str = "", timestamp: str = "") -> dict:
    """Maps process_capture()'s internal record onto the fixed TSV/console
    column set: Timestamp, Name, Type, Infamous, Gem 1-4, Map, Exceptions.

    `timestamp` is the capture folder's own name (see on_capture's `ts`),
    not record["timestamp"] (a separately-generated ISO string stamped
    later, mid-OCR) -- using the folder name lets a log row be matched
    back to its captures/<timestamp>/ directory by exact string equality.

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

    Exceptions defaults to blank here, but on_capture always passes the
    tag chosen once at script start (see _prompt_for_session_options)
    -- it's an explicitly manual field (rematch, forced-infamy scarabs,
    reduced-infamy atlas passives, etc.) that only the person watching
    the encounter can know, and the same answer applies to every
    capture for the rest of the run.
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
        "Timestamp": timestamp,
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
# Asked once at script start (see _prompt_for_session_options) and
# applied to every capture logged for the rest of the run -- these are
# session-level facts (an atlas passive's infamy odds don't change
# encounter to encounter within a single mapping session), not something
# that needs re-asking per capture.
EXCEPTION_KEYS = {"r": "Rematch", "n": "No Infamous chance", "i": "Infamy/Renown"}


CAMPAIGN_KEY = "c"


def _prompt_for_session_options() -> tuple[str, str]:
    """Asks once, before the capture hotkey is armed, for the single
    option that applies to every capture this run: an exception tag
    (EXCEPTION_KEYS) OR campaign mode (CAMPAIGN_KEY) -- mutually
    exclusive, not two independent settings asked separately. A session
    dedicated to the campaign shadow-ground-truth workflow (see
    tools/harvest_campaign_review.py) is never also a Rematch/No
    Infamous chance/Infamy session in practice, so folding them into one
    choice keeps this to a single keypress instead of two sequential
    prompts.

    Returns (session_exception, capture_dir):
    - session_exception is the chosen EXCEPTION_KEYS label, or "" if
      ENTER or CAMPAIGN_KEY was pressed instead.
    - capture_dir is CAMPAIGN_CAPTURE_DIR only if CAMPAIGN_KEY was
      pressed, else CAPTURE_DIR -- kept in a completely separate
      directory so campaign captures never mix into the normal
      captures/ folder the main harvest_support_icons.py scans, which
      matters here specifically: the whole point is auditing what the
      icon classifier says against human-read tooltips, so campaign
      captures must never silently feed the same classifier they're
      meant to check.

    Blocks indefinitely -- unlike the old per-capture version this
    replaced, nothing is time-sensitive yet at this point in the script
    (no captures have started, there's no live gameplay moment to avoid
    stalling), so there's no reason to time out and risk silently
    defaulting to "no tag" on a session-wide fact worth getting right.
    """
    result = {"exception": "", "capture_dir": CAPTURE_DIR}
    event = threading.Event()

    def _make_exception_handler(label):
        def _handler():
            result["exception"] = label
            event.set()
        return _handler

    def _campaign_handler():
        result["capture_dir"] = CAMPAIGN_CAPTURE_DIR
        event.set()

    handles = [
        keyboard.add_hotkey(key, _make_exception_handler(label))
        for key, label in EXCEPTION_KEYS.items()
    ]
    handles.append(keyboard.add_hotkey(CAMPAIGN_KEY, _campaign_handler))
    # ENTER confirms "none of the above" without changing whatever was
    # already chosen.
    handles.append(keyboard.add_hotkey("enter", event.set))

    key_hint = ", ".join(f"[{k.upper()}]={v}" for k, v in EXCEPTION_KEYS.items())
    print(f"Tag this run? {key_hint}, [{CAMPAIGN_KEY.upper()}]=Campaign encounter "
          f"(sub-68, saves to {CAMPAIGN_CAPTURE_DIR}/) -- [ENTER] for none.")

    event.wait()

    for handle in handles:
        keyboard.remove_hotkey(handle)

    return result["exception"], result["capture_dir"]


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


def on_capture(crop_plan, session_exception: str, capture_dir: str = CAPTURE_DIR):
    """Captures, crops, OCRs, and logs one encounter. `session_exception`
    and `capture_dir` are the two mutually-exclusive outcomes of the
    single choice made once at script start (see
    _prompt_for_session_options) and applied to every row this run --
    no per-capture prompt or deferral needed anymore, so this all runs
    synchronously on the keyboard hook thread (see _safe_on_capture).
    `capture_dir` is CAPTURE_DIR unless campaign mode was chosen, in
    which case it's CAMPAIGN_CAPTURE_DIR and `session_exception` is ""
    -- everything else about this function is identical either way;
    only where the files land (and whether a tag is logged) differs."""
    full = capture_full_screen()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = os.path.join(capture_dir, ts)
    os.makedirs(session_dir, exist_ok=True)

    crops = {}
    for region_name, box in crop_plan.items():
        crop = full.crop(box)
        crops[region_name] = crop
        crop.save(os.path.join(session_dir, f"{region_name}.png"))

    record = process_capture(crops)
    row = build_log_row(record, exceptions=session_exception, timestamp=ts)

    warrant_generated_path = os.path.join(session_dir, "warrant_generated.txt")
    with open(warrant_generated_path, "w", newline="\n") as f:
        f.write(build_warrant_extracted_text(record))

    try:
        append_log(row)
        copy_row_to_clipboard(row)
        print(f"[{record['timestamp']}] captured -> {session_dir}  " +
              "  ".join(f"{k}={v!r}" for k, v in row.items()))
    except Exception:
        import traceback
        traceback.print_exc()
        print("Logging this capture failed (see traceback above).")


def _safe_on_capture(crop_plan, session_exception: str, capture_dir: str = CAPTURE_DIR):
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
        on_capture(crop_plan, session_exception, capture_dir)
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

    session_exception, capture_dir = _prompt_for_session_options()

    mode_note = f" (campaign mode -- saving to {capture_dir}/)" if capture_dir == CAMPAIGN_CAPTURE_DIR else ""
    print(f"Ready{mode_note}. Press [{HOTKEY.upper()}] over a paused mercenary encounter to capture. "
          f"Press Ctrl+C to quit.")

    keyboard.add_hotkey(HOTKEY, lambda: _safe_on_capture(crop_plan, session_exception, capture_dir))

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
