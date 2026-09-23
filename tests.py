"""
Regression tests for capture_pipeline.py.

Run: python tests.py

Six independent suites:

1. Mercenary name extraction (run_mercenary_name_tests) -- each case is
   a real captured name crop (test_data/mercenary_names/<slug>.png,
   named from the expected name via slugify_reference_name) checked
   against the ground truth a human confirmed from the original
   screenshot. Checked for containment, not equality: extraction is
   deliberately biased toward recall over precision (see
   _isolate_text_row's docstring) -- it should never clip a real
   character, even at the cost of occasionally including an extra word
   from background noise that a human would cull by hand.

2. Mercenary type+infamy extraction (run_mercenary_type_tests) -- test
   cases are NOT a separately-maintained list; they're derived directly
   from definitions/mercenary_types.json, which is the authority on
   which type+infamy COMBINATIONS actually occur. This matters because
   it isn't simply "every type x both infamy states" -- some types are
   Infamous-only (Warpriest of the Ruckus) and some can never be
   Infamous (Flamehand, Frost Ambusher, and others -- confirmed
   directly, not assumed). A combination with no test crop yet
   (test_data/mercenary_types/<slug>.png) is PENDING, not failing --
   naturally true for most of the 65 listed combinations until each is
   actually observed and captured.

3. Gem identity matching (run_gem_tests) -- tests match_icon()'s
   fine-grained identity matching directly, covering all 12 known
   Trarthus gems (definitions/gems_by_mercenary.json). NOTE: this is no longer
   what determines the live pipeline's actual Gem 1-4 output (see suite
   5) -- real independent captures of the same gem were found to score
   in the same range as different-gem comparisons (0.68-0.75 vs
   0.73-0.90), so identity resolution moved to mercenary-type lookup
   instead. This suite is kept as a regression check on match_icon's
   underlying matching capability and a target for future work (e.g.
   ORB-based matching, which showed real promise -- see project
   history), not as a check on production behavior. A gem without a
   reference icon yet (assets/gems/<slug>.png) is reported as PENDING,
   not failed.

4. Quadrant coverage (run_quadrant_tests) -- same caveat as #3: tests
   match_icon() identity matching per rucksack quadrant, not current
   production behavior. One real capture per quadrant
   (test_data/gems/quadrants/<quadrant>.png). Added because a
   quadrant's on-screen position affects the icon's surrounding
   border/lighting rendering, a real axis of variation independent of
   "which gem is it." Deliberately not "known_issue" softened even
   though some currently fail: the whole point of this suite is to
   surface exactly that gap, not hide it. A quadrant with no real
   capture yet (currently top_right) is PENDING, same convention as
   gems without a reference yet.

5. Gem presence + resolution (run_gem_presence_tests) -- tests what
   actually determines the live pipeline's Gem 1-4 output now:
   is_gem_present() (a coarse "is this some gem" check, validated
   separable on real data even though fine-grained identity isn't --
   see GEM_PRESENCE_THRESHOLD's docstring) against real positive
   examples (test_data/gems/*.png, every real gem crop already
   collected) and real negative examples
   (test_data/currencies/*.png and test_data/scarabs/*.png -- real
   currency/scarab/card crops incidentally captured while hunting for
   gems in other screenshots, split into two folders since scarabs
   needed their own shape/domain taxonomy -- see AI_RAMBLINGS.md),
   plus resolve_gem_presence() (the mercenary-type-based identity
   lookup) against representative scenarios: an unambiguous type, the
   ambiguous Blade Ambusher case, and an unrecognized type.

6. Blade Ambusher disambiguation (run_blade_ambusher_disambiguation_test)
   -- a real pass/fail suite now, checking disambiguate_blade_ambusher_gem()
   against the two canonical Blade-Ambusher-specific crops
   (test_data/gems/blade_ambusher/) plus every real Spectral Helix
   variance sample (test_data/gems/variance/, see its README). This is
   the ONE mercenary type resolve_gem_presence() resolves via real image
   comparison instead of the 1:1 type->gem binding every other type
   uses -- confirmed necessary because skills/warrant data gives zero
   disambiguating information for Blade Ambusher specifically (both "of
   Trarthus" skills are always equipped, see
   disambiguate_blade_ambusher_gem's docstring). Unlike suites 3/4, a
   failure here IS a real production regression and counts toward the
   blocking total. No extra Spectral Throw variance sample exists yet
   (n=1 on that side) -- add one to
   test_data/gems/variance/spectral_throw_of_trarthus/ and wire it into
   _blade_ambusher_disambiguation_cases() the moment a second real
   capture turns up.

   A related but separate check, run_bladefall_spectral_throw_collision_test,
   is NOT one of the six suites above and never contributes to the
   blocking count -- it watches the real near-collision between Spectral
   Throw of Trarthus and Bladefall of Trarthus (0.9387, reproduced
   against two independent real Bladefall captures) against a stricter
   safety margin than match_icon() itself requires, always PENDING until
   a second real Spectral Throw sample exists to confirm the margin
   either way. disambiguate_blade_ambusher_gem never considers Bladefall
   a candidate at all, so this collision doesn't affect production --
   it's tracked purely because the margin is thinner than this project
   has trusted anywhere else.

Cases marked known_issue=True (or, for suite 2, listed in
MERCENARY_TYPE_KNOWN_ISSUES) are cases we know currently produce wrong
output (tracked here deliberately, not hidden) -- these are reported
separately from genuine regressions so the summary distinguishes
"a case we haven't solved yet" from "something that used to work and
now doesn't." If a known_issue case starts passing, that's good news:
the script will tell you to remove the marker.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture_pipeline as cp

# Every suite here deliberately exercises lots of intentional "didn't
# confidently match" cases (that's the whole point of testing fuzzy-match
# tolerance for real OCR noise) -- cp.NOTES_ENABLED off keeps those
# expected NOTEs from cluttering this suite's own pass/fail report. A
# real regression still shows up as an XX/failure line regardless; this
# only silences the underlying function's own console chatter.
cp.NOTES_ENABLED = False

from PIL import Image

TEST_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_data")
MERCENARY_NAME_TEST_DATA_DIR = os.path.join(TEST_DATA_DIR, "mercenary_names")
MERCENARY_TYPE_TEST_DATA_DIR = os.path.join(TEST_DATA_DIR, "mercenary_types")
GEM_TEST_DATA_DIR = os.path.join(TEST_DATA_DIR, "gems")
QUADRANT_TEST_DATA_DIR = os.path.join(GEM_TEST_DATA_DIR, "quadrants")
NON_GEM_TEST_DATA_DIRS = (os.path.join(TEST_DATA_DIR, "currencies"), os.path.join(TEST_DATA_DIR, "scarabs"))
REFERENCES_GEMS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "gems")

# One real capture per rucksack quadrant -- which gem doesn't matter,
# only that all four quadrant positions get real coverage. top_right was
# pending for a long time (no real capture on hand), until a run of real
# Flamequiver/Ripper/Winter Deacon/Sanguimancer sightings all landed
# there -- filled from an INDEPENDENTLY captured crop (Blast Rain of
# Trarthus, captures/20260921_111400), not one already reused as
# assets/gems/<slug>.png's own reference image, so this is a genuine
# held-out check of match_icon rather than an image matching itself.
QUADRANT_CASES = [
    ("top_left", "siege_ballista_of_trarthus"),
    ("top_right", "blast_rain_of_trarthus"),
    ("bottom_left", "sunder_of_trarthus"),
    ("bottom_right", "bladefall_of_trarthus"),
]

# Mercenary type test cases are NOT a separately-maintained list here --
# they're derived directly from definitions/mercenary_types.json (see
# run_mercenary_type_tests), since that file is the authority on which
# type+infamy COMBINATIONS actually occur. A flat "34 base types, try
# both Infamous and non-Infamous for each" approach doesn't work: some
# types are Infamous-only (Warpriest of the Ruckus) and some can never
# be Infamous (Flamehand, Frost Ambusher, and others -- confirmed
# directly, not assumed). Testing a combination that was never claimed
# to exist isn't a failure, it's simply not a real case -- treated as
# PENDING (no test data expected) rather than counted against anything.
#
# known_issue overrides for specific type+infamy combinations go here,
# keyed by the exact combined string as it appears in
# mercenary_types.json (e.g. "Infamous Blade Ambusher") -- empty for
# now, since none are currently known-broken.
MERCENARY_TYPE_KNOWN_ISSUES = set()

# (case/expected_name, known_issue)
MERCENARY_NAME_CASES = [
    ("Dalshon Alo", False),
    ("Darakos, the Keitan Inquisitor", False),
    ("Elarric, the Azadin Son", False),
    ("Gek, the Azadin Howler", False),
    ("Taruki, the Keitan Chainbreaker", False),
    ("Tatania, the Cyaxan Sister", False),
    ("Vaudren, the Azadin Blademaster", False),
    ("Krala, the Unrelenting", False),
    ("Krezin, the Skinner", False),
    ("Lee, the Cuckoo", False),
    ("Ryzan, the Believer", False),
    ("Vayela, the Pampered", False),
    ("Ventis, the Taker", False),
    ("Veth, the Depraved", False),
    ("Vexa, the Killer", False),
    ("Vorla Tarthis", False),
    ("Vreka, the Killer", False),
    ("Zari Nethala", False),
    ("Baknar Trosis", False),
    # Regression case for the --psm 11 total-failure bug: two real
    # captures of the same mercenary (Eli, the Contemptible) had
    # byte-identical text-glyph pixels, differing only in incidental
    # background-art noise at the crop's edges, yet --psm 11
    # deterministically returned nothing at all on this specific crop
    # while succeeding on the other. extract_text's --psm 7 fallback
    # (see its docstring) is what recovers this one. This is the actual
    # failing crop, not the succeeding one, so it exercises that
    # fallback path specifically -- don't swap it for a "cleaner" crop
    # of the same name.
    ("Eli, the Contemptible", False),
    # Regression case for the apostrophe-splits-the-word-run bug: Tesseract
    # read the apostrophe in "Ven'zi" as � on --psm 11, and
    # _WORD_RUN_RE didn't allow apostrophe-like characters mid-run, so the
    # name fragmented into "Ven" and "zi Kaldri" and max(matches, key=len)
    # kept the longer, wrong, truncated fragment. Fixed by allowing
    # straight/curly apostrophes and � inside the run, then
    # normalizing all three to a plain apostrophe in the final string.
    ("Ven'zi Kaldri", False),
    # Regression case for a second, distinct leading-noise bug: --psm 11
    # read background-art texture at the crop's edge as its own short
    # lowercase word ("ae"), space-joined onto the real name by Tesseract
    # so it landed INSIDE the same _WORD_RUN_RE match rather than as a
    # separable fragment (raw OCR was "ae Ruktara, the Unrelenting").
    # Fixed via _STRAY_NAME_PREFIX_RE, distinct from _STRAY_ICON_PREFIX_RE
    # (that one only strips before the literal word "Infamous").
    ("Ruktara, the Unrelenting", False),
    # Regression case for the trailing mirror of the above bug: the same
    # speckled background-art noise appeared on BOTH edges of this crop,
    # read as a stray lowercase word after the name too (raw OCR was "oe
    # Pradin Prowl-linger af", true name "Pradin Prowl-linger"). Fixed via
    # _STRAY_NAME_SUFFIX_RE.
    ("Pradin Prowl-linger", False),
    # Regression case for a third kind of edge noise: not background-art
    # texture, but an actual on-screen orange UI badge ("ORB") inside the
    # wide/generous name crop, misread by --psm 11 as "PORE" and
    # space-joined onto the name (raw OCR was "Alak Prowl-linger PORE").
    # Fixed by widening _STRAY_NAME_SUFFIX_RE to also strip a short
    # ALL-UPPERCASE trailing word, kept as a separate alternative from
    # the lowercase case since a real title can legitimately end in a
    # short Title-Case word.
    ("Alak Prowl-linger", False),
    # KNOWN ISSUE, not fixed: --psm 11 inserted spurious spaces INSIDE two
    # real words ("Voltaire" -> "Voltai re", "Aristocrat" -> "Ari
    # stocrat"), unlike every other name bug fixed this session (all of
    # which were extra noise GLUED onto an intact real name at an edge).
    # No safe general fix exists for this: there's no dictionary of valid
    # mercenary given names to validate a repair against (unlike
    # match_skill_name's fuzzy pool), and a naive "merge short internal
    # word fragments back together" rule would wrongly corrupt real
    # multi-word names already in this list (e.g. "Vorla Tarthis",
    # "Dalshon Alo" -- "Alo" is only 3 letters, same ballpark as the
    # bogus "Ari"/"re" fragments here, so length alone can't tell them
    # apart). --psm 7 happens to read this specific crop correctly
    # (aside from a stray "aan " prefix _STRAY_NAME_PREFIX_RE already
    # handles), but psm 11 succeeds first and the loop never reaches it --
    # swapping the default psm priority isn't justified from this one
    # counterexample alone against extract_text()'s existing, differently-
    # motivated psm-11-first reasoning (see its docstring and the "Eli,
    # the Contemptible" case above, which depends on psm 11 fully failing
    # before psm 7 is tried). Tracked here as a known issue rather than
    # guessed at.
    ("Voltaire, the Aristocrat", True),
]

# All 12 known gems (definitions/gems_by_mercenary.json) -- not all have been seen/
# captured yet. (gem_display_name, associated_type_or_types)
GEM_CASES = [
    ("Chain Hook of Trarthus", ["Ripper"]),
    ("Heavy Strike of Trarthus", ["Winter Deacon"]),
    ("Sunder of Trarthus", ["Earthshaker"]),
    ("Bladefall of Trarthus", ["Bladecaster"]),
    ("Blast Rain of Trarthus", ["Flamequiver"]),
    ("Siege Ballista of Trarthus", ["Sniper"]),
    ("Spectral Helix of Trarthus", ["Blade Ambusher"]),
    ("Spectral Shield Throw of Trarthus", ["Bastion"]),
    ("Spectral Throw of Trarthus", ["Blade Ambusher"]),
    ("Dark Bargain of Trarthus", ["Cruel Mistress"]),
    ("Storm Call of Trarthus", ["Sanguimancer"]),
    ("Wave of Conviction of Trarthus", ["Flaming Charlatan"]),
]


def type_filename(expected_type: str, expected_infamy: str) -> str:
    """Derives the type test crop's filename from the expected type/infamy
    values themselves (e.g. "Shock Ambusher" + "Infamous" ->
    "infamous_shock_ambusher.png") -- one source of truth, rather than
    hardcoding filenames separately from the values they represent."""
    slug = cp.slugify_reference_name(expected_type)
    if expected_infamy:
        slug = f"{cp.slugify_reference_name(expected_infamy)}_{slug}"
    return f"{slug}.png"

def run_mercenary_name_tests():
    results = {"pass": [], "fail": [], "known_issue_still_failing": [], "known_issue_now_passing": []}

    for expected_name, known_issue in MERCENARY_NAME_CASES:
        name_path = cp.slugify_reference_name(expected_name)
        name_path = os.path.join(MERCENARY_NAME_TEST_DATA_DIR, f"{name_path}.png")

        name_img = Image.open(name_path)

        actual_name = cp.extract_text(name_img)

        # Name is checked for containment, not equality -- it should never clip a real
        # character, even at the cost of occasionally including an
        # extra word from background noise..
        ok = bool(actual_name) and expected_name in actual_name

        detail = (f"name={actual_name!r} (expected {expected_name!r})")

        if ok and not known_issue:
            results["pass"].append(expected_name)
        elif ok and known_issue:
            results["known_issue_now_passing"].append(detail)
        elif not ok and known_issue:
            results["known_issue_still_failing"].append(detail)
        else:
            results["fail"].append(detail)

    print(f"MERCENARY NAMES: {len(results['pass'])}/{len(MERCENARY_NAME_CASES)} passing")
    for c in results["pass"]:
        print(f"  ok  {c}")

    if results["known_issue_still_failing"]:
        print(f"\n  Known issues (still failing, as expected): {len(results['known_issue_still_failing'])}")
        for d in results["known_issue_still_failing"]:
            print(f"    ~   {d}")

    if results["known_issue_now_passing"]:
        print(f"\n  Known issues now passing -- remove known_issue=True for these: "
              f"{len(results['known_issue_now_passing'])}")
        for d in results["known_issue_now_passing"]:
            print(f"    !!  {d}")

    if results["fail"]:
        print(f"\n  Unexpected failures (real regressions): {len(results['fail'])}")
        for d in results["fail"]:
            print(f"    XX  {d}")

    return len(results["fail"])

def run_mercenary_type_tests():
    """Tests every type+infamy combination listed in
    definitions/mercenary_types.json -- that file is the authority on
    which combinations actually exist (see MERCENARY_TYPE_KNOWN_ISSUES's
    comment for why a flat "every base type x both infamy states"
    approach doesn't work). A combination with no test crop yet is
    PENDING, not failing -- test data naturally won't exist for every
    one of the 65 listed combinations right away."""
    results = {"pass": [], "fail": [], "known_issue_still_failing": [], "known_issue_now_passing": [], "pending": []}

    for full_type_string in cp._load_known_mercenary_types():
        if cp._INFAMOUS_PREFIX_RE.match(full_type_string):
            expected_infamy = "Infamous"
            expected_type = cp._INFAMOUS_PREFIX_RE.sub("", full_type_string).strip()
        else:
            expected_infamy = None
            expected_type = full_type_string
        known_issue = full_type_string in MERCENARY_TYPE_KNOWN_ISSUES

        type_path = os.path.join(MERCENARY_TYPE_TEST_DATA_DIR, type_filename(expected_type, expected_infamy))
        if not os.path.isfile(type_path):
            results["pending"].append(full_type_string)
            continue

        type_img = Image.open(type_path)
        raw_type = cp.extract_text(type_img)
        actual_infamy, actual_type = cp.parse_mercenary_type(raw_type)

        # Type/infamy are exact-match since match_mercenary_type
        # normalizes stray noise with a lookup from the known list.
        ok = (actual_type == expected_type) and (actual_infamy == expected_infamy)
        detail = (f"{full_type_string}: type={actual_type!r} (expected {expected_type!r}), "
                  f"infamy={actual_infamy!r} (expected {expected_infamy!r})")

        if ok and not known_issue:
            results["pass"].append(full_type_string)
        elif ok and known_issue:
            results["known_issue_now_passing"].append(detail)
        elif not ok and known_issue:
            results["known_issue_still_failing"].append(detail)
        else:
            results["fail"].append(detail)

    total_claimed = len(cp._load_known_mercenary_types())
    print(f"MERCENARY TYPE: {len(results['pass'])}/{total_claimed} passing, "
          f"{len(results['pending'])} pending (no test crop yet)")
    for c in results["pass"]:
        print(f"  ok  {c}")

    if results["pending"]:
        print(f"\n  Pending (add test_data/mercenary_types/<slug>.png when seen):")
        for p in results["pending"]:
            print(f"    ..  {p}")

    if results["known_issue_still_failing"]:
        print(f"\n  Known issues (still failing, as expected): {len(results['known_issue_still_failing'])}")
        for d in results["known_issue_still_failing"]:
            print(f"    ~   {d}")

    if results["known_issue_now_passing"]:
        print(f"\n  Known issues now passing -- remove from MERCENARY_TYPE_KNOWN_ISSUES for these: "
              f"{len(results['known_issue_now_passing'])}")
        for d in results["known_issue_now_passing"]:
            print(f"    !!  {d}")

    if results["fail"]:
        print(f"\n  Unexpected failures (real regressions): {len(results['fail'])}")
        for d in results["fail"]:
            print(f"    XX  {d}")

    return len(results["fail"])

def run_gem_tests():
    passed, failed, pending = [], [], []

    for gem_name, assoc_types in GEM_CASES:
        slug = cp.slugify_reference_name(gem_name)
        ref_path = os.path.join(REFERENCES_GEMS_DIR, f"{slug}.png")
        crop_path = os.path.join(GEM_TEST_DATA_DIR, f"{slug}.png")

        if not os.path.isfile(ref_path) or not os.path.isfile(crop_path):
            pending.append(gem_name)
            continue

        crop_img = Image.open(crop_path)
        matched = cp.match_icon(crop_img)
        icon_ok = matched == slug

        # Exercise the type cross-check too: for a type with exactly one
        # associated gem, resolving that gem against its own type should
        # always confirm it (no correction needed); for Blade Ambusher's
        # two, either of its two gems should also pass through unchanged.
        crosscheck_ok = True
        crosscheck_detail = ""
        for t in assoc_types:
            resolved, note = cp.resolve_gem_with_type_crosscheck(slug, t)
            if resolved != slug:
                crosscheck_ok = False
                crosscheck_detail = f"resolve_gem_with_type_crosscheck({slug!r}, {t!r}) -> {resolved!r}, expected {slug!r}"

        if icon_ok and crosscheck_ok:
            passed.append(gem_name)
        else:
            detail = f"{gem_name}: match_icon={matched!r} (expected {slug!r})"
            if crosscheck_detail:
                detail += f"; {crosscheck_detail}"
            failed.append(detail)

    print(f"\nGEMS: {len(passed)}/{len(GEM_CASES)} passing, {len(pending)} pending (no reference/crop yet)")
    for g in passed:
        print(f"  ok  {g}")
    if pending:
        print(f"\n  Pending (add assets/gems/<slug>.png + test_data/gems/<slug>.png when seen):")
        for g in pending:
            print(f"    ..  {g}")
    if failed:
        print(f"\n  Unexpected failures: {len(failed)}")
        for d in failed:
            print(f"    XX  {d}")

    return len(failed)


def run_quadrant_tests():
    passed, failed, pending = [], [], []

    for quadrant, expected_gem in QUADRANT_CASES:
        crop_path = os.path.join(QUADRANT_TEST_DATA_DIR, f"{quadrant}.png")
        if expected_gem is None or not os.path.isfile(crop_path):
            pending.append(quadrant)
            continue

        crop_img = Image.open(crop_path)
        matched = cp.match_icon(crop_img)
        if matched == expected_gem:
            passed.append(quadrant)
        else:
            failed.append(f"{quadrant}: match_icon={matched!r} (expected {expected_gem!r})")

    print(f"\nQUADRANTS: {len(passed)}/4 passing, {len(pending)} pending (no real capture yet)")
    for q in passed:
        print(f"  ok  {q}")
    if pending:
        print(f"\n  Pending (add test_data/gems/quadrants/<quadrant>.png when a gem is seen there):")
        for q in pending:
            print(f"    ..  {q}")
    if failed:
        print(f"\n  Failing: {len(failed)}")
        for d in failed:
            print(f"    XX  {d}")

    return len(failed)


def run_gem_presence_tests():
    """Tests what actually determines production Gem 1-4 output now:
    is_gem_present() (coarse presence) + resolve_gem_presence() (type-
    based identity lookup) -- see module docstring, suite 4."""
    failed = []

    # --- is_gem_present: real positive examples (every gem crop we have) ---
    positive_count = 0
    for fname in sorted(os.listdir(GEM_TEST_DATA_DIR)):
        path = os.path.join(GEM_TEST_DATA_DIR, fname)
        if not os.path.isfile(path) or not fname.lower().endswith(".png"):
            continue
        positive_count += 1
        if not cp.is_gem_present(Image.open(path)):
            failed.append(f"is_gem_present(real gem crop {fname}) = False, expected True")

    # --- is_gem_present: real negative examples (currencies + scarabs) ---
    negative_count = 0
    for non_gem_dir in NON_GEM_TEST_DATA_DIRS:
        if not os.path.isdir(non_gem_dir):
            continue
        for fname in sorted(os.listdir(non_gem_dir)):
            path = os.path.join(non_gem_dir, fname)
            if not os.path.isfile(path) or not fname.lower().endswith(".png"):
                continue
            negative_count += 1
            if cp.is_gem_present(Image.open(path)):
                failed.append(f"is_gem_present(real non-gem crop {fname}) = True, expected False")

    # --- resolve_gem_presence: representative scenarios ---
    scenarios_checked = 0
    scenarios = [
        ("Sniper", 2, ["Siege Ballista of Trarthus", "Siege Ballista of Trarthus"], False),
        ("Sniper", 0, [], False),
        ("Blade Ambusher", 1, None, True),   # ambiguous -- expect "Unknown (...)" + a note
        ("Nonexistent Type", 1, None, True),  # unrecognized -- expect "Unknown" + a note
        # Recognized type with no gem pool at all (23 of 34 types) --
        # real bug found via a genuine false positive (a mask-shaped
        # currency item scored just above the presence threshold for a
        # Shock Ambusher capture, which structurally can't have a gem).
        # Expect nothing logged, just a note -- not "Unknown".
        ("Shock Ambusher", 1, [], True),
    ]
    for mercenary_type, count, expected_names, expect_note in scenarios:
        scenarios_checked += 1
        names, note = cp.resolve_gem_presence(mercenary_type, count)
        if expected_names is not None and names != expected_names:
            failed.append(f"resolve_gem_presence({mercenary_type!r}, {count}) -> {names!r}, expected {expected_names!r}")
        if expect_note and not note:
            failed.append(f"resolve_gem_presence({mercenary_type!r}, {count}) returned no note, expected one")
        if not expect_note and note:
            failed.append(f"resolve_gem_presence({mercenary_type!r}, {count}) returned an unexpected note: {note!r}")

    print(f"\nGEM PRESENCE: {positive_count} real positive examples, {negative_count} real negative "
          f"examples, {scenarios_checked} resolve_gem_presence scenarios checked")
    if failed:
        print(f"\n  Failing: {len(failed)}")
        for d in failed:
            print(f"    XX  {d}")
    else:
        print("  ok  all positive/negative examples and scenarios correct")

    return len(failed)


RUCKSACK_TEST_DATA_DIR = os.path.join(TEST_DATA_DIR, "rucksacks")

# (session label, quadrant, expect_present) -- one asserted quadrant per
# real captured session, converted from the ad hoc validation that found
# is_gem_present_in_rucksack()'s real gem floor (0.8209) and real non-gem
# ceiling (0.7966) with zero overlap (see assets/README.md). Only the ONE
# quadrant confirmed by that investigation is asserted per session -- the
# other three aren't claimed to be verified non-gems, just not part of
# what was actually checked, so this doesn't overreach into unconfirmed
# territory the way a blanket "everything else is False" assertion would.
RUCKSACK_PRESENCE_CASES = [
    ("gem_blast_rain_1", "rucksack_bottom_left", True),
    ("gem_blast_rain_2", "rucksack_bottom_left", True),
    ("gem_spectral_throw_1", "rucksack_bottom_right", True),
    ("gem_spectral_helix_1", "rucksack_bottom_left", True),
    ("gem_spectral_helix_2", "rucksack_bottom_left", True),
    ("gem_siege_ballista_1", "rucksack_bottom_left", True),
    ("gem_bladefall_1", "rucksack_bottom_right", True),
    ("gem_sunder_1", "rucksack_bottom_left", True),
    ("nongem_currency_1", "rucksack_top_left", False),
    ("nongem_exalted_orb", "rucksack_top_left", False),
    ("nongem_masked_1", "rucksack_top_left", False),
    ("nongem_masked_2", "rucksack_bottom_right", False),
    ("nongem_ring_1", "rucksack_bottom_right", False),
    ("nongem_currency_2", "rucksack_top_left", False),
    ("nongem_currency_3", "rucksack_top_left", False),
    ("nongem_masked_3", "rucksack_top_left", False),
    ("nongem_currency_4", "rucksack_top_left", False),
    ("nongem_cruel_mistress", "rucksack_top_right", False),
]


def run_rucksack_presence_tests():
    """Real pass/fail suite for is_gem_present_in_rucksack() -- the
    whole-rucksack, multi-scale/position-search function process_capture()
    now uses instead of four independent is_gem_present() calls (see its
    docstring). Distinct from suite 5 above, which still tests the OLD
    is_gem_present() against single isolated crops -- that function and
    its tests are unchanged and still meaningful for that narrower
    question, just no longer what production actually calls.

    Each case is a real captured session (all four quadrant crops,
    reassembled the same way production does) with the ONE quadrant
    this project has real ground truth for asserted -- see
    RUCKSACK_PRESENCE_CASES.
    """
    results = {"pass": [], "fail": [], "pending": []}
    for label, quadrant, expected in RUCKSACK_PRESENCE_CASES:
        session_dir = os.path.join(RUCKSACK_TEST_DATA_DIR, label)
        quadrants = ("rucksack_top_left", "rucksack_top_right", "rucksack_bottom_left", "rucksack_bottom_right")
        paths = {q: os.path.join(session_dir, f"{q}.png") for q in quadrants}
        if not all(os.path.isfile(p) for p in paths.values()):
            results["pending"].append(label)
            continue
        crops = {q: Image.open(p) for q, p in paths.items()}
        present = cp.is_gem_present_in_rucksack(crops)
        actual = present[quadrant]
        detail = f"{label} ({quadrant}): is_gem_present_in_rucksack -> {actual!r} (expected {expected!r})"
        if actual == expected:
            results["pass"].append(detail)
        else:
            results["fail"].append(detail)

    total = len(results["pass"]) + len(results["fail"])
    print(f"\nRUCKSACK PRESENCE (multi-scale): {len(results['pass'])}/{total} passing")
    if results["pending"]:
        print(f"\n  Pending (no test crop yet): {len(results['pending'])}")
        for c in results["pending"]:
            print(f"    ..  {c}")
    if results["fail"]:
        print(f"\n  Unexpected failures (real regressions): {len(results['fail'])}")
        for d in results["fail"]:
            print(f"    XX  {d}")
    else:
        print("  ok  all known sessions correct")

    return len(results["fail"])


BLADE_AMBUSHER_TEST_DATA_DIR = os.path.join(GEM_TEST_DATA_DIR, "blade_ambusher")
GEM_VARIANCE_DIR = os.path.join(GEM_TEST_DATA_DIR, "variance")

# (expected slug, path) -- the two canonical crops plus every real
# variance sample for either gem (test_data/gems/variance/, see its
# README): validated against real same-gem spread, not one photo, is
# exactly what justified turning this from a standing TODO into a real
# disambiguate_blade_ambusher_gem() call live in production (see
# resolve_gem_presence/process_capture and assets/README.md's findings).
# Spectral Throw's variance dir was n=1 for a long time (the Bladefall
# collision watch could only ever check one real Throw sample) -- a
# second real, independently-captured Throw sample (a live Blade
# Ambusher encounter, `20260914_091453`, correctly resolved to Throw by
# production) finally turned up; see AI_RAMBLINGS.md.
def _blade_ambusher_disambiguation_cases():
    cases = [
        ("spectral_helix_of_trarthus", os.path.join(BLADE_AMBUSHER_TEST_DATA_DIR, "spectral_helix_of_trarthus.png")),
        ("spectral_throw_of_trarthus", os.path.join(BLADE_AMBUSHER_TEST_DATA_DIR, "spectral_throw_of_trarthus.png")),
    ]
    for slug in ("spectral_helix_of_trarthus", "spectral_throw_of_trarthus"):
        variance_dir = os.path.join(GEM_VARIANCE_DIR, slug)
        if os.path.isdir(variance_dir):
            for fname in sorted(os.listdir(variance_dir)):
                if fname.lower().endswith(".png"):
                    cases.append((slug, os.path.join(variance_dir, fname)))
    return cases


def run_blade_ambusher_disambiguation_test():
    """Real pass/fail suite for disambiguate_blade_ambusher_gem() -- the
    one function resolve_gem_presence() defers to for Blade Ambusher
    specifically (every other mercenary type keeps the 1:1 type->gem
    binding, see resolve_gem_presence's docstring). Unlike suites 3/4
    (match_icon's general identity matching, which no longer determines
    production output), a regression here IS a real production
    regression, so this counts toward the blocking failure total.

    A case with no test crop on disk is reported PENDING, same
    convention as every other suite, rather than failing -- relevant
    once a second real Spectral Throw sample exists.
    """
    results = {"pass": [], "fail": [], "pending": []}

    for expected_slug, path in _blade_ambusher_disambiguation_cases():
        label = os.path.relpath(path, GEM_TEST_DATA_DIR)
        if not os.path.isfile(path):
            results["pending"].append(label)
            continue
        actual = cp.disambiguate_blade_ambusher_gem(Image.open(path))
        if actual == expected_slug:
            results["pass"].append(label)
        else:
            results["fail"].append(f"{label}: disambiguate_blade_ambusher_gem -> {actual!r} (expected {expected_slug!r})")

    total = len(results["pass"]) + len(results["fail"])
    print(f"\nBLADE AMBUSHER DISAMBIGUATION: {len(results['pass'])}/{total} passing")
    for c in results["pass"]:
        print(f"  ok  {c}")
    if results["pending"]:
        print(f"\n  Pending (no test crop yet): {len(results['pending'])}")
        for c in results["pending"]:
            print(f"    ..  {c}")
    if results["fail"]:
        print(f"\n  Unexpected failures (real regressions): {len(results['fail'])}")
        for d in results["fail"]:
            print(f"    XX  {d}")

    return len(results["fail"])


SKILL_TEST_DATA_DIR = os.path.join("test_data", "skills")

# (mercenary type+infamy combination, skills crop filename, expected skill
# names in on-screen order) -- real captures, ground-truthed against that
# same capture's real warrant.txt. infamous_bloodletter_corvil caught the
# original psm 3 -> psm 6 switch (psm 3 silently read only 2 of these 6
# real skills); blade_ambusher_eli and infamous_warpriest_raskor caught
# the FOLLOW-UP fix (psm 6 alone silently dropped 1-4 real skills on 9 of
# 66 real captures swept, once psm 3's fix was checked broadly -- these
# two are the most decisive of those 9, dropping 4 and 1 respectively).
# extract_skill_names() now runs both passes and keeps whichever matches
# more of the known pool -- see its docstring and AI_RAMBLINGS.md. All
# kept as permanent regression cases, not one-off diagnostics.
SKILL_NAME_EXTRACTION_CASES = [
    ("Infamous Bloodletter", "infamous_bloodletter_corvil.png",
     ["Bloodthirst", "Blood Mortar", "Lacerate", "Perforate", "Determination", "Dash"]),
    ("Infamous Swiftblade", "infamous_swiftblade_thrassel.png",
     ["Cyclone of the Empire", "Rallying Cry", "Triggerblades", "Flicker Strike", "Pride", "Dash"]),
    ("Blade Ambusher", "blade_ambusher_eli.png",
     ["Blade Trap", "Trarthan Agility", "Spectral Helix of Trarthus", "Spectral Throw of Trarthus", "Bear Trap", "Grace"]),
    ("Infamous Warpriest", "infamous_warpriest_raskor.png",
     ["Holy Relic", "Smite", "Dominating Blow", "Beacons of Faith", "Herald of Purity", "Flame Dash"]),
    ("Sanguimancer", "sanguimancer_lorakna_wrapped_skill.png",
     ["Boiling Blood", "Storm Call of Trarthus", "Bodyswap",
      "Corrupted Blade Vortex of the Scythe", "Pride", "Proximity Shield"]),
    # Caught a real gap in extract_skill_names' noise handling: the skill
    # icons' own art bled into the OCR crop as five extra one-or-two-
    # character lines ('~', 'ri', 'mi', 'bi', '(R)') alongside all 6 real
    # skills, read identically in both psm passes. Since these can never
    # fuzzy-match anything (shorter than every real skill name in the
    # pool), extract_skill_names now drops them outright instead of
    # keeping them as fake "unresolved skill" candidates -- see its
    # docstring.
    ("Combatant", "combatant_razti_icon_bleed.png",
     ["Inspiring Cry", "Herald of Ice", "Frost Blades", "Static Strike", "Wrath", "Dash"]),
    # A second, distinct icon-bleed shape: 'ear ¢' (5 characters, longer
    # than the "too short" check alone would catch) -- caught the gap in
    # the fix above and is why the character-set check exists alongside
    # the length check. See extract_skill_names's docstring.
    ("Kineticist", "kineticist_vorlena_icon_bleed.png",
     ["Elemental Weakness", "Flame Wall", "Kinetic Rain of Impact",
      "Greater Kinetic Blast", "Flame Dash", "Inspiring Cry"]),
    # A third icon-bleed shape: 'CSaee' -- ordinary letters, plausible
    # length, no shape tell the length/character-class checks above
    # would catch. Caught the gap that motivated the quota check
    # (matched-count-already-at-max) in extract_skill_names's docstring.
    ("Fallen Reverend", "fallen_reverend_ung_icon_bleed.png",
     ["Absolution", "Raise Spectre of Transience", "Desecrate",
      "Reinforce: Fallen Bishop", "Flame Dash", "Battlemage's Cry"]),
    # Real, reproduced bug found via a full real-warrant.txt-vs-regenerated
    # audit (see AI_RAMBLINGS.md): OCR garbled "Glacial Hammer" into "GLACIAL
    # Eee" (icon-bleed noise ate "HAMMER"), and match_skill_name's fuzzy
    # match picked "Vaal Glacial Hammer" instead -- difflib's ratio() scored
    # the noisy candidate slightly HIGHER against the longer, wrong pool
    # entry (0.667) than the correct shorter one (0.643), because ratio()
    # rewards raw character overlap over length fidelity. This mercenary
    # has both skills at once, so the wrong one silently overwrote a real,
    # distinct skill instead of surfacing as unresolved.
    ("Winter Deacon", "winter_deacon_petyr_glacial_hammer_prefix.png",
     ["Frost Shield", "Vaal Glacial Hammer", "Earthquake of Winter",
      "Glacial Hammer", "Frost Bomb", "Hatred"]),
    # Same root cause, different skill pair: "Leap Slam of Groundbreaking"
    # wraps to two on-screen lines ("LEAP SLAM OF" / "GROUNDBREAKING"), and
    # this mercenary ALSO has plain "Leap Slam" in its pool. The wrap
    # prefix "LEAP SLAM OF" fuzzy-matched to the shorter "Leap Slam" instead
    # of the correct full name (same ratio()-favors-less-length-difference
    # effect), then "GROUNDBREAKING" alone still matched the long name on
    # its own -- producing an extra, duplicated row and shifting every
    # subsequent skill/support row by one. Two independent real captures
    # confirmed this isn't a one-off.
    ("Ripper", "ripper_leap_slam_wrap_1.png",
     ["Tunnelslam", "Enrage", "Chain Hook of Trarthus",
      "Leap Slam of Groundbreaking", "Determination", "Leap Slam"]),
    ("Ripper", "ripper_leap_slam_wrap_2.png",
     ["Tunnelslam", "Enrage", "Leap Slam of Groundbreaking",
      "Cleave", "Molten Shell", "Leap Slam"]),
    # Real, reproduced Tesseract block-ORDER bug (distinct from every case
    # above): every individual line OCR'd and fuzzy-matched correctly, but
    # psm 3's own internal text-block segmentation returned "Greater
    # Stormcall" LAST instead of in its true 4th-of-6 on-screen position --
    # confirmed by checking psm 3's raw line order directly against this
    # capture's real warrant.txt. extract_skill_names' pass-picking only
    # scores passes by HOW MANY names matched the known pool, never whether
    # the matched order is right, so a fully-recognized-but-reordered pass
    # sails through undetected. psm 6 for this same image actually had the
    # right order but too much garbling to win on match count.
    ("Stormhand", "stormhand_tara_block_reorder.png",
     ["Conductivity", "Sigil of Power", "Arc", "Greater Stormcall",
      "Lightning Warp", "Wrath"]),
    ("Cardinal", "cardinal_orton_block_reorder.png",
     ["Holy Hammers", "Holy Strike", "Holy Sweep", "Sanctified Strike",
      "Herald of Purity", "Purity of Lightning"]),
]


def run_skill_name_extraction_tests():
    """Real pass/fail suite for extract_skill_names(), now live in
    process_capture()/build_warrant_extracted_text(). Checks against the
    known-skill-pool-filtered result (the same filter
    build_warrant_extracted_text applies) rather than the raw OCR
    output, since a stray leading noise line (real, expected -- see
    extract_skill_names's docstring) failing to match anything is
    correct behavior, not a bug, and shouldn't fail this suite.
    """
    results = {"pass": [], "fail": [], "pending": []}
    for mercenary_type, filename, expected in SKILL_NAME_EXTRACTION_CASES:
        path = os.path.join(SKILL_TEST_DATA_DIR, filename)
        if not os.path.isfile(path):
            results["pending"].append(filename)
            continue
        known_pool = set(cp.known_skills_for_type(mercenary_type))
        raw = cp.extract_skill_names(Image.open(path), mercenary_type=mercenary_type)
        actual = [name for name in raw if name in known_pool]
        detail = f"{filename} ({mercenary_type}): {actual!r}"
        if actual == expected:
            results["pass"].append(detail)
        else:
            results["fail"].append(f"{detail}  (expected {expected!r})")

    total = len(results["pass"]) + len(results["fail"])
    print(f"\nSKILL NAME EXTRACTION: {len(results['pass'])}/{total} passing")
    for c in results["pass"]:
        print(f"  ok  {c}")
    if results["pending"]:
        print(f"\n  Pending (no test crop yet): {len(results['pending'])}")
        for c in results["pending"]:
            print(f"    ..  {c}")
    if results["fail"]:
        print(f"\n  Unexpected failures (real regressions): {len(results['fail'])}")
        for d in results["fail"]:
            print(f"    XX  {d}")

    return len(results["fail"])


# (record, expected substring that MUST appear in the output, expected
# substring that must NOT appear) -- pure unit tests against synthetic
# records, no image fixtures needed, for build_warrant_extracted_text()'s
# "silently missing -> visibly flagged" fix: a skill row that doesn't
# match the mercenary's own known pool used to just vanish from the
# output with no trace beyond an ephemeral console NOTE (see its
# docstring) -- now every dropped row is listed in a trailing comment
# line so the fact something was thrown away survives to disk.
WARRANT_EXTRACTED_TEXT_CASES = [
    (
        {
            "mercenary_name": "Test, the Example",
            "infamy": None,
            "mercenary_type": "Frosthand",
            "mercenary_level": 66,
            "skills": ["Frost Bomb", "Frostbite", "leapslam", "Ice Nova", "Flame Dash", "Discipline"],
        },
        "# unrecognized skill row(s), dropped: leapslam",
        None,
    ),
    (
        {
            "mercenary_name": "Clean, the Case",
            "infamy": None,
            "mercenary_type": "Frosthand",
            "mercenary_level": 66,
            "skills": ["Frost Bomb", "Frostbite", "Ice Nova", "Flame Dash", "Discipline"],
        },
        None,
        "# unrecognized",
    ),
    (
        # Real case (20260914_121940, Lorakna the Profuse): "Corrupted
        # Blade Vortex of the Scythe" wraps to 2 lines in-game, so OCR
        # reads it as two candidates -- the real name (matches fine) and
        # its wrapped continuation "THE SCYTHE" (doesn't match anything
        # on its own). All 6 real skills were actually correct; only the
        # flag line was wrong before this was fixed to recognize a
        # wrapped-line remnant (a substring of an already-matched skill)
        # instead of flagging it as a genuinely dropped row.
        {
            "mercenary_name": "Lorakna, the Profuse",
            "infamy": None,
            "mercenary_type": "Sanguimancer",
            "mercenary_level": 83,
            "skills": ["Boiling Blood", "Storm Call of Trarthus", "Bodyswap",
                       "Corrupted Blade Vortex of the Scythe", "THE SCYTHE",
                       "Pride", "Proximity Shield"],
        },
        None,
        "# unrecognized",
    ),
]


def run_warrant_extracted_text_tests():
    """Pass/fail suite for build_warrant_extracted_text()'s unrecognized-
    skill flagging. Each case asserts a string that must appear (the
    flag line, naming the dropped skill) and/or one that must NOT appear
    (no flag line at all when every skill matched) -- a regression here
    means a dropped skill is silently missing again, exactly the gap
    this fix closed."""
    results = {"pass": [], "fail": []}
    for record, must_contain, must_not_contain in WARRANT_EXTRACTED_TEXT_CASES:
        text = cp.build_warrant_extracted_text(record)
        label = record["mercenary_name"]
        ok = True
        if must_contain is not None and must_contain not in text:
            ok = False
            detail = f"{label}: expected to find {must_contain!r} in output, didn't"
        if must_not_contain is not None and must_not_contain in text:
            ok = False
            detail = f"{label}: expected NOT to find {must_not_contain!r} in output, but did"
        if ok:
            results["pass"].append(label)
        else:
            results["fail"].append(detail)

    total = len(results["pass"]) + len(results["fail"])
    print(f"\nWARRANT_EXTRACTED.TXT UNRECOGNIZED-SKILL FLAGGING: {len(results['pass'])}/{total} passing")
    for c in results["pass"]:
        print(f"  ok  {c}")
    if results["fail"]:
        print(f"\n  Unexpected failures (real regressions): {len(results['fail'])}")
        for d in results["fail"]:
            print(f"    XX  {d}")

    return len(results["fail"])


# Real, reproduced near-collision found during the Blade Ambusher
# disambiguation investigation (see assets/README.md/AI_RAMBLINGS.md):
# Spectral Throw of Trarthus scores 0.9387 against Bladefall of
# Trarthus, confirmed identical against two independent real Bladefall
# captures -- not a fluke of one reference photo. match_icon()'s own
# thresholds (0.94 min_score, 0.025 min_margin) still resolve both
# correctly today, but only by a 0.0613 margin -- far thinner than this
# project has trusted anywhere else (GEM_PRESENCE_THRESHOLD was revised
# twice over margins in the 0.001-0.01 range, but its "comfortable"
# gem-floor-to-non-gem-ceiling margins were still a full order of
# magnitude tighter than 0.0613 before those revisions forced a
# rethink). Tracked here against a stricter safety margin this pair
# does NOT currently clear -- a deliberate "fails a bar worth caring
# about" rather than match_icon's bare pass/fail, which would hide how
# thin this really is. This isn't live in production
# (disambiguate_blade_ambusher_gem never considers Bladefall a
# candidate at all -- see its docstring), so this is purely a
# watch-list regression check, not blocking.
BLADEFALL_THROW_SAFE_MARGIN = 0.15


def _spectral_throw_samples():
    """Every real Spectral Throw of Trarthus crop on hand: the canonical
    test crop plus any variance samples (test_data/gems/variance/
    spectral_throw_of_trarthus/, see its README) -- was n=1 (canonical
    only) for a long time; a second real, independently-captured sample
    (`20260914_091453`, a live Blade Ambusher encounter correctly
    resolved to Throw by production) finally turned up. See
    AI_RAMBLINGS.md."""
    paths = [os.path.join(GEM_TEST_DATA_DIR, "spectral_throw_of_trarthus.png")]
    variance_dir = os.path.join(GEM_VARIANCE_DIR, "spectral_throw_of_trarthus")
    if os.path.isdir(variance_dir):
        paths += [os.path.join(variance_dir, f) for f in sorted(os.listdir(variance_dir))
                  if f.lower().endswith(".png")]
    return [p for p in paths if os.path.isfile(p)]


def _bladefall_samples():
    """Every real Bladefall of Trarthus crop on hand: the canonical
    reference plus any variance samples."""
    paths = [os.path.join(REFERENCES_GEMS_DIR, "bladefall_of_trarthus.png")]
    variance_dir = os.path.join(GEM_VARIANCE_DIR, "bladefall_of_trarthus")
    if os.path.isdir(variance_dir):
        paths += [os.path.join(variance_dir, f) for f in sorted(os.listdir(variance_dir))
                  if f.lower().endswith(".png")]
    return [p for p in paths if os.path.isfile(p)]


def run_bladefall_spectral_throw_collision_test():
    """Diagnostic, not blocking: reports the real measured margin between
    every real Spectral Throw of Trarthus sample and every real Bladefall
    of Trarthus sample on hand, so it stays visible in every run rather
    than only discoverable by re-deriving it ad hoc (as it was, twice,
    during the original investigation). Marked PENDING (not a clean
    pass/fail) since BLADEFALL_THROW_SAFE_MARGIN is a judgment call about
    what "safe" means here, not an externally-validated number the way
    match_icon()'s own thresholds were (see match_icon's docstring).

    Reports the WORST (smallest) margin across every Throw-sample x
    Bladefall-sample pair -- a single weak pairing matters more here
    than the average, since production only needs one real capture to
    land on the wrong side of it.
    """
    throw_paths = _spectral_throw_samples()
    bladefall_paths = _bladefall_samples()

    print(f"\nBLADEFALL vs SPECTRAL THROW COLLISION WATCH: pending "
          f"({len(throw_paths)} Throw sample(s) x {len(bladefall_paths)} Bladefall sample(s), not a hard pass/fail)")
    if not throw_paths or not bladefall_paths:
        print("  ..  missing test crop(s) -- nothing to check yet")
        return

    worst = None
    for throw_path in throw_paths:
        throw_crop = Image.open(throw_path)
        self_score = cp._icon_similarity_score(throw_crop, throw_crop)
        for bladefall_path in bladefall_paths:
            bladefall_ref = Image.open(bladefall_path)
            cross_score = cp._icon_similarity_score(throw_crop, bladefall_ref)
            margin = self_score - cross_score
            detail = (margin, cross_score, self_score,
                      os.path.relpath(throw_path, GEM_TEST_DATA_DIR),
                      os.path.relpath(bladefall_path, os.path.dirname(REFERENCES_GEMS_DIR)))
            if worst is None or margin < worst[0]:
                worst = detail

    margin, cross_score, self_score, throw_label, bladefall_label = worst
    status = "ok" if margin >= BLADEFALL_THROW_SAFE_MARGIN else "XX"
    print(f"  {status}  worst pair: {throw_label} vs {bladefall_label}  "
          f"throw-vs-bladefall={cross_score:.4f}  throw-vs-self={self_score:.4f}  "
          f"margin={margin:.4f}  (safe margin wanted: {BLADEFALL_THROW_SAFE_MARGIN})")
    if margin < BLADEFALL_THROW_SAFE_MARGIN:
        print(f"      fails the stricter safety margin (not match_icon()'s own, which this still "
              f"clears) -- real, reproduced across {len(throw_paths)} independent Throw sample(s).")


LEVEL_TEST_DATA_DIR = os.path.join(TEST_DATA_DIR, "mercenary_levels")

# Real level values (32-83, campaign through maps). A missing file is
# PENDING, the same convention as every other suite -- this list is
# deliberately ahead of which files actually exist yet. Files here are
# real mss captures of the "level" region (build_crop_plan crops it to
# 147x40 -- see definitions/mercenary_regions.json's "level" entry --
# same as any other region's saved crop, e.g. test_data/skills/*.png),
# NOT the earlier CTRL+PRTSC full-screen calibration screenshots that
# used to live in captures/lvlNN.png (used only to find the region and
# sanity-check the psm choice, then deliberately not kept -- see
# AI_RAMBLINGS.md's "Mercenary Level extraction" section -- and now
# removed from disk since real mss data has replaced that role).
LEVEL_EXTRACTION_CASES = [
    (27, "lvl27.png"),
    (32, "lvl32.png"), (38, "lvl38.png"), (46, "lvl46.png"), (58, "lvl58.png"),
    (66, "lvl66.png"), (68, "lvl68.png"), (73, "lvl73.png"), (79, "lvl79.png"),
    (80, "lvl80.png"), (81, "lvl81.png"), (82, "lvl82.png"), (83, "lvl83.png"),
]


def run_level_extraction_tests():
    """Real pass/fail suite for extract_level() against real mss-captured
    level-region crops (test_data/mercenary_levels/<filename>.png, already
    cropped to the region by the same code path production uses -- open
    directly, do NOT re-crop with box_tuple/the region's absolute
    coordinates, those are full-screen coordinates and this is already a
    147x40 region crop). A missing level (no real capture yet) is PENDING,
    same convention as every other suite.
    """
    results = {"pass": [], "fail": [], "pending": []}
    for expected, filename in LEVEL_EXTRACTION_CASES:
        path = os.path.join(LEVEL_TEST_DATA_DIR, filename)
        if not os.path.isfile(path):
            results["pending"].append(filename)
            continue
        actual = cp.extract_level(Image.open(path))
        detail = f"{filename}: extract_level -> {actual!r} (expected {expected!r})"
        if actual == expected:
            results["pass"].append(detail)
        else:
            results["fail"].append(detail)

    total = len(results["pass"]) + len(results["fail"])
    print(f"\nLEVEL EXTRACTION: {len(results['pass'])}/{total} passing")
    for c in results["pass"]:
        print(f"  ok  {c}")
    if results["pending"]:
        print(f"\n  Pending (no real capture yet): {len(results['pending'])}")
        for c in results["pending"]:
            print(f"    ..  {c}")
    if results["fail"]:
        print(f"\n  Unexpected failures (real regressions): {len(results['fail'])}")
        for d in results["fail"]:
            print(f"    XX  {d}")

    return len(results["fail"])


SUPPORT_TEST_DATA_DIR = os.path.join(TEST_DATA_DIR, "supports")

# (filename, skill names (row-aligned, real ground truth), expected rows)
# -- real supports.png crops with a paired real warrant.txt on hand,
# ground-truthing extract_support_names()'s full production path (grid
# subdivision + match_support_icon() + resolve_support_name(), CULLED
# against each row's own skill via definitions/supports_by_skills.json
# -- see resolve_support_name's docstring), not just the underlying
# match logic already exhaustively validated in
# tools/match_support_icon.py (1,992/1,992 real crops). Picked for real
# coverage, not just convenience: fallen_reverend_baknar keeps 2 real,
# PROVEN-genuine ambiguities even after culling (Absolution and Raise
# Spectre of Transience can both actually roll either Minion Damage or
# Minion Life, confirmed directly against supports_by_skills.json) plus
# a skill with zero equipped supports (an empty row); sanguimancer_rakella
# fully resolves to single names everywhere -- real confirmation that
# culling doesn't just narrow, it can close an ambiguity completely.
SUPPORT_EXTRACTION_CASES = [
    ("fallen_reverend_baknar.png",
     ["Absolution", "Raise Spectre of Transience", "Desecrate",
      "Reinforce: Fallen Bishop", "Flame Dash", "Battlemage's Cry"],
     [
        ["Critical Chance (Tier: 2)", "Minion Damage (Tier: 2) or Minion Life (Tier: 2)",
         "Increased Area of Effect (Tier: 2)", "Lightning Penetration (Tier: 2)"],
        ["Greater Critical Chance (Tier: 3)", "Greater Faster Attacks (Tier: 3)",
         "Increased Area of Effect (Tier: 2)", "Minion Damage (Tier: 2) or Minion Life (Tier: 2)",
         "Greater Minion Damage (Tier: 3) or Greater Minion Life (Tier: 3)"],
        ["Second Wind (Tier: 3)", "Cooldown Recovery (Tier: 2) or DoT Multiplier (Tier: 2)"],
        [],
        ["More Duration (Tier: 2)", "Greater Faster Casting (Tier: 3)"],
        ["Second Wind (Tier: 3)", "Greater Cooldown Recovery (Tier: 3)"],
     ]),
    ("sanguimancer_rakella.png",
     ["Boiling Blood", "Storm Call of Trarthus", "Bodyswap",
      "Corrupted Blade Vortex of the Scythe", "Flame Dash", "Pride"],
     [
        ["Brutality (Tier: 2)", "Greater More Duration (Tier: 3)", "DoT Multiplier (Tier: 2)"],
        ["Concentrated Effect (Tier: 2)", "More Duration (Tier: 2)", "Greater DoT Multiplier (Tier: 3)",
         "Faster Casting (Tier: 2)", "Swift Affliction (Tier: 2)"],
        ["Greater Faster Casting (Tier: 3)", "Increased Area of Effect (Tier: 2)"],
        ["Brutality (Tier: 2)", "Concentrated Effect (Tier: 2)", "Physical as Extra Chaos (Tier: 2)",
         "Faster Casting (Tier: 2)"],
        ["More Duration (Tier: 2)", "Faster Casting (Tier: 2)"],
        [],
     ]),
    # First live capture with the culling fix already deployed (not
    # something hand-picked to test it) -- its warrant_generated.txt
    # came back byte-for-byte identical to the real warrant.txt (modulo
    # CRLF vs LF line endings, the game's own clipboard copy vs this
    # project's write convention). Zero ambiguity anywhere in this one,
    # unlike the two cases above -- real confirmation that the culling
    # fix doesn't just narrow known-ambiguous cases, ordinary captures
    # keep working exactly as before.
    ("toxicologist_ryx.png",
     ["Withering Step", "Chaotic Burst", "Chaotic Shot", "Caustic Arrow", "Dash", "Trarthan Agility"],
     [
        ["Increased Area of Effect (Tier: 2)", "Second Wind (Tier: 3)"],
        ["Increased Area of Effect (Tier: 2)", "Wither on Hit (Tier: 2)"],
        ["Faster Attacks (Tier: 2)", "Faster Projectiles (Tier: 2)", "Physical as Extra Chaos (Tier: 2)",
         "Chance to Poison (Tier: 2)", "Chaos Penetration (Tier: 2)"],
        ["Wither on Hit (Tier: 2)", "Greater Area of Effect (Tier: 3)", "Greater Fork (Tier: 3)",
         "Pierce (Tier: 2)", "Greater Faster Attacks (Tier: 3)"],
        [],
        ["Greater Cooldown Recovery (Tier: 3)", "Increased Area of Effect (Tier: 2)"],
     ]),
    # Real capture that proved _resolve_same_skill_collisions() correct:
    # Raise Zombie of Gigantism rolled both Minion Damage and Minion Life
    # (icon+tier collision, real ground-truth-confirmed elsewhere) at
    # once -- since a skill never rolls the same support twice (checked
    # directly against 663 real ground-truth skill rows, zero
    # duplicates), 2 slots sharing that exact 2-member collision forces
    # one of each, resolving the row fully instead of repeating the same
    # "X or Y" string twice. No __captures_campaign warrant.txt exists to
    # verify against (structurally impossible below level 68, see
    # AI_RAMBLINGS.md), so this is verified via the forced-bijection
    # logic itself, not ground truth text.
    ("reanimator_zixa.png",
     ["Raise Zombie of Gigantism", "Desecrate", "Raise Zombie of Falling",
      "Flesh Offering", "Frostblink", "Relic of Binding"],
     [
        ["Lesser Minion Damage (Tier: 1)", "Lesser Minion Life (Tier: 1)"],
        ["Lesser Faster Casting (Tier: 1)"],
        ["Lesser Increased Area of Effect (Tier: 1)", "Lesser Added Chaos (Tier: 1)"],
        ["Lesser Faster Casting (Tier: 1)"],
        ["Lesser Increased Area of Effect (Tier: 1)"],
        [],
     ]),
]


def run_support_extraction_tests():
    """Real pass/fail suite for extract_support_names() -- what actually
    determines production's Supports output now (see
    build_warrant_extracted_text). A regression here IS a real
    production regression, unlike suites 3/4 above."""
    results = {"pass": [], "fail": [], "pending": []}
    for filename, skill_names, expected in SUPPORT_EXTRACTION_CASES:
        path = os.path.join(SUPPORT_TEST_DATA_DIR, filename)
        if not os.path.isfile(path):
            results["pending"].append(filename)
            continue
        actual = cp.extract_support_names(Image.open(path), skill_names=skill_names)
        detail = f"{filename}: {actual!r}"
        if actual == expected:
            results["pass"].append(detail)
        else:
            results["fail"].append(f"{detail}  (expected {expected!r})")

    total = len(results["pass"]) + len(results["fail"])
    print(f"\nSUPPORT EXTRACTION: {len(results['pass'])}/{total} passing")
    for c in results["pass"]:
        print(f"  ok  {c}")
    if results["pending"]:
        print(f"\n  Pending (no test crop yet): {len(results['pending'])}")
        for c in results["pending"]:
            print(f"    ..  {c}")
    if results["fail"]:
        print(f"\n  Unexpected failures (real regressions): {len(results['fail'])}")
        for d in results["fail"]:
            print(f"    XX  {d}")

    return len(results["fail"])


SUPPORT_TIER_TEST_DATA_DIR = os.path.join(SUPPORT_TEST_DATA_DIR, "tiers")

# (filename, expected roman tier) -- real crops, each visually confirmed to
# show the claimed number of bars in the tier badge before being locked in
# as a fixture (see AI_RAMBLINGS.md's Tier I regression writeup). tier_i_1/
# tier_i_2 came from __captures_campaign, added specifically because
# match_support_icon() had a real, reproduced bug that silently mismatched
# Tier I onto whatever higher tier happened to already have a harvested
# reference (see the badge_key membership check in match_support_icon's
# own docstring) -- Tier I crops barely existed in the corpus before that
# archive, so nothing caught this until real low-level data did.
# tier_ii_1/tier_iii_1 are pre-existing regression anchors (from
# sanguimancer_rakella.png and __captures_campaign respectively) to
# confirm the fix didn't disturb the tiers that already worked.
# tier_i_4_lightningpenetration is a second, separate real bug: the
# badge reader found this Tier I crop's bar fine but rejected it on
# color-uniformity (core_std 12.0-13.3, a subtle highlight/shine
# gradient in this specific icon's own art) since
# _SUPPORT_BADGE_CORE_STD_MAX was tuned to 10.0 against a sample that
# didn't include this case -- caught by the campaign shadow-ground-truth
# audit (assets/campaign_review/, see AI_RAMBLINGS.md), not by this
# suite, since no real Tier I capture existed on hand to catch it before
# that independent data did. Threshold raised to 20.0.
SUPPORT_TIER_CASES = [
    ("tier_i_1.png", "I"),
    ("tier_i_2.png", "I"),
    ("tier_i_4_lightningpenetration.png", "I"),
    ("tier_ii_1.png", "II"),
    ("tier_iii_1.png", "III"),
]


def run_support_tier_tests():
    """Real pass/fail suite for the tier-badge reader and its use inside
    match_support_icon() -- both _resolve_support_tier_from_badge() (the
    roman-numeral bar count in isolation) and match_support_icon()'s own
    resolved key (which must end in the same tier) are checked per crop,
    since a regression could plausibly break either one independently.
    A regression here IS a real production regression (see
    run_support_extraction_tests, which exercises the same function one
    level up)."""
    results = {"pass": [], "fail": [], "pending": []}
    for filename, expected_roman in SUPPORT_TIER_CASES:
        path = os.path.join(SUPPORT_TIER_TEST_DATA_DIR, filename)
        if not os.path.isfile(path):
            results["pending"].append(filename)
            continue
        crop = Image.open(path)
        expected_lower = expected_roman.lower()

        badge_tier = cp._resolve_support_tier_from_badge(crop)
        key = cp.match_support_icon(crop)
        key_tier = key.rsplit("_", 1)[1] if key else None

        detail = (f"{filename}: badge={badge_tier!r} match_support_icon={key!r} "
                  f"(expected tier {expected_roman!r})")
        if badge_tier == expected_lower and key_tier == expected_lower:
            results["pass"].append(detail)
        else:
            results["fail"].append(detail)

    total = len(results["pass"]) + len(results["fail"])
    print(f"\nSUPPORT TIER DETECTION: {len(results['pass'])}/{total} passing")
    for c in results["pass"]:
        print(f"  ok  {c}")
    if results["pending"]:
        print(f"\n  Pending (no test crop yet): {len(results['pending'])}")
        for c in results["pending"]:
            print(f"    ..  {c}")
    if results["fail"]:
        print(f"\n  Unexpected failures (real regressions): {len(results['fail'])}")
        for d in results["fail"]:
            print(f"    XX  {d}")

    return len(results["fail"])


_SUPPORT_TIER_SUFFIX_RE = re.compile(r"\s+(I|II|III)$")


def run_support_coverage_tests():
    """Self-documenting coverage check, not a code-regression suite: for
    every tier of every real support (definitions/supports.json),
    bundled by the skill(s) that can roll it
    (definitions/supports_by_skills.json), reports whether the reference
    catalog (assets/models/support_reference_embeddings.json) can
    currently resolve that (icon, tier) at all.

    This is the same underlying gap tools/generate_support_coverage_report.py
    tracks (assets/support_icon_coverage.md), just re-derived here so it
    shows up on every test run, organized by skill, instead of only in a
    separately-regenerated report. A support is "missing" because no real
    capture with that roll has been seen yet, not because anything is
    broken -- so issues found here are printed per specific (support,
    tier), not swept into one aggregate count, but deliberately never
    added to run()'s blocking failure total (see its call site).

    A missing (icon, tier) is one gap in the reference catalog, not one
    gap per skill that happens to offer it -- `mercsilverintsupportgem_i`
    alone is reachable from dozens of skills, and counting it once per
    skill (an earlier version of this suite did exactly that) inflated
    "1217 issues" for what the coverage report correctly calls 52
    missing keys. An intermediate version printed a cross-reference line
    for every later skill sharing an already-flagged gap instead of a
    new ISSUE -- still way too much reporting for the same real
    handful of gaps. Fixed by reporting each visual_key exactly once,
    full stop: skills are visited in sorted order, a gap is printed only
    the first time it's seen, and every later skill that would otherwise
    only repeat an already-flagged gap is skipped entirely -- no line,
    no header, nothing -- since it has nothing new to say. A skill still
    prints if it has at least one gap not yet flagged, even if it ALSO
    shares an already-flagged one (only the new one is shown for that
    skill, not the repeat).
    """
    print("\nNOTE: missing supports are only reported once, under the first skill "
          "that offers them, to avoid cascading failures for the same underlying gap.")

    supports = cp._load_supports_definition()
    skills = cp._load_supports_by_skills()
    refs = cp._load_support_reference_embeddings()

    issues_by_skill = {}
    covered = issue_count = total = 0
    seen_names = set()
    flagged_keys = set()

    for skill_name in sorted(skills):
        skill_issues = []
        for entry in skills[skill_name].get("PossibleSupports", []):
            base_name = _SUPPORT_TIER_SUFFIX_RE.sub("", entry)
            info = supports.get(base_name)
            if info is None:
                continue  # supports.json is the source of truth; shouldn't happen
            seen_names.add(base_name)
            total += 1
            visual_key = f"{info['icon']}_{info['tier_roman'].lower()}"
            if visual_key in refs:
                covered += 1
                continue
            if visual_key in flagged_keys:
                continue  # already reported under an earlier skill -- nothing new to say
            flagged_keys.add(visual_key)
            issue_count += 1
            skill_issues.append(f"ISSUE  {entry}: {visual_key!r} not in the reference catalog yet")
        if skill_issues:
            issues_by_skill[skill_name] = skill_issues

    unassigned = sorted(set(supports) - seen_names)
    unassigned_missing = [
        name for name in unassigned
        if f"{supports[name]['icon']}_{supports[name]['tier_roman'].lower()}" not in refs
    ]

    print(f"\nSUPPORT COVERAGE BY SKILL: {covered}/{total} (support, tier) entries covered; "
          f"{issue_count} distinct (icon, tier) gap(s) affect the other {total - covered} "
          f"across {len(issues_by_skill)} skill(s)")
    for skill_name in sorted(issues_by_skill):
        print(f"\n  {skill_name}:")
        for line in issues_by_skill[skill_name]:
            print(f"    {line}")
    if unassigned:
        print(f"\n  {len(unassigned)} support(s) never offered by any skill in the extracted "
              f"data (not counted above -- see AI_RAMBLINGS.md's zero-breadth-supports note):")
        for name in unassigned:
            print(f"    ..  {name}")
        if unassigned_missing:
            print(f"    ({len(unassigned_missing)} of those are also missing a reference -- "
                  f"can't be flagged by skill since no skill reaches them; adds to "
                  f"assets/support_icon_coverage.md's total but not to issue_count above)")

    return issue_count


def run():
    mercenary_name_failures = run_mercenary_name_tests()
    mercenary_type_failures = run_mercenary_type_tests()
    gem_failures = run_gem_tests()
    quadrant_failures = run_quadrant_tests()
    presence_failures = run_gem_presence_tests()
    rucksack_presence_failures = run_rucksack_presence_tests()
    blade_ambusher_failures = run_blade_ambusher_disambiguation_test()
    skill_name_failures = run_skill_name_extraction_tests()
    warrant_extracted_text_failures = run_warrant_extracted_text_tests()
    level_extraction_failures = run_level_extraction_tests()
    support_extraction_failures = run_support_extraction_tests()
    support_tier_failures = run_support_tier_tests()
    support_coverage_issues = run_support_coverage_tests()
    run_bladefall_spectral_throw_collision_test()

    # gem_failures/quadrant_failures test match_icon's identity matching,
    # which no longer determines production output (see suites 3/4's
    # docstrings) -- counting them toward the ship/no-ship gate would
    # claim a production regression that isn't one. They're still fully
    # visible above, just not blocking. blade_ambusher_failures IS
    # blocking, unlike those two -- disambiguate_blade_ambusher_gem is
    # actually live in production for this one type (see
    # resolve_gem_presence), so a regression here is a real one.
    #
    # presence_failures is a partial exception worth knowing about, not
    # a clean case either way: process_capture() no longer calls
    # is_gem_present() at all (replaced by is_gem_present_in_rucksack(),
    # see run_rucksack_presence_tests() below) -- but run_gem_presence_tests()
    # ALSO checks resolve_gem_presence()'s scenarios, which are still
    # fully live. Left blocking for now since splitting the suite is a
    # bigger change than this comment -- if this ever fails, check
    # whether it's an is_gem_present() failure (no longer meaningful to
    # production) or a resolve_gem_presence() one (still is) before
    # treating it as urgent.
    blocking_failures = (mercenary_name_failures + mercenary_type_failures
                          + presence_failures + rucksack_presence_failures
                          + blade_ambusher_failures + skill_name_failures
                          + warrant_extracted_text_failures + level_extraction_failures
                          + support_extraction_failures + support_tier_failures)
    if gem_failures or quadrant_failures:
        print(f"\n(match_icon identity-matching suites have {gem_failures + quadrant_failures} "
              f"failure(s) -- tracked above, not blocking since match_icon no longer "
              f"determines production Gem output.)")
    if support_coverage_issues:
        print(f"\n(SUPPORT COVERAGE BY SKILL has {support_coverage_issues} issue(s) -- tracked "
              f"above, not blocking since a missing reference is a known data gap, not a code "
              f"regression. See assets/support_icon_coverage.md for the same gap by (icon, tier) "
              f"instead of by skill.)")

    print()
    if blocking_failures:
        print(f"RESULT: {blocking_failures} unexpected regression(s) -- do not ship.")
        return 1
    print("RESULT: no regressions (pending gems and known issues, if any, are unchanged).")
    return 0


if __name__ == "__main__":
    sys.exit(run())
