# Test data

These data are used by the test fixtures (`tests.py`, run from the project root).

Primarily, the tests (and their data) fall into two use categories:
 - Basic validation of function - "I need to test this feature is working..."
 - Edge cases - "This feature isn't (or wasn't) working when using this data..."

As more edge cases are found, more variations can be added to the test data. This breadth improves test coverage at the cost of seemingly redundant copies of the same images. See [Variance and variants folders](#variance-and-variants-folders) below.

Initial development used CTRL+PRTSC screen captures. However, this method does not create a 1:1 comparison vs using the same internal capture system (`mss`). All CTRL+PRTSC images have been purged and every file here is a real `mss`-based screen capture, copied from a `captures/` folder. See `AI_RAMBLINGS.md`'s CTRL+PRTSC-vs-`mss` finding for why mixing capture methods would quietly reintroduce a real brightness bias into this data.

## Structure

```
test_data/
  gems/                        match_icon() identity + is_gem_present() positive examples
    blade_ambusher/            the two canonical Blade Ambusher gems (see disambiguate_blade_ambusher_gem)
    quadrants/                 one real crop per rucksack quadrant position (see below)
    variance/<gem_name>/       extra same-gem captures, gem_name subfolders (see below)
  currencies/                  is_gem_present() real negative examples
    variance/                  extra same-currency captures (see below)
  scarabs/                     is_gem_present() real negative examples
    variants/                  extra same-scarab captures (see below)
  rucksacks/<session>/         whole-rucksack crops (4 quadrants each), is_gem_present_in_rucksack() cases
  mercenary_names/             extract_text()/name-parsing cases
  mercenary_types/             match_mercenary_type() cases
  mercenary_levels/            extract_level() cases
  skills/                      extract_skill_names() cases
  supports/                    extract_support_names() cases
```

Currency and scarab crops exist only as `is_gem_present()` negative examples -- neither currency nor scarab identity is active (see `assets/README.md`).

## Quadrant coverage

`test_data/gems/quadrants/` holds one real crop per rucksack quadrant (`top_left`, `top_right`, `bottom_left`, `bottom_right`) -- a gem's on-screen position changes the icon's surrounding border/lighting rendering, a real axis of variation independent of "which gem is it." See `tests.py`'s `run_quadrant_tests`.

## Variance and variants folders

Extra captures of the same real item, added as duplicates and near-duplicates turn up, to characterize real same-item score variance rather than trusting a single sample.

Three folders hold this kind of data, and they are **not** organized or wired into `tests.py` the same way:

- **`gems/variance/<gem_name>/<capture_timestamp>.png`** -- one subfolder per gem, filename is the source capture's timestamp for traceability back to its original `captures/` folder or archive. Only `spectral_helix_of_trarthus/`, `spectral_throw_of_trarthus/`, and `bladefall_of_trarthus/` are actually read by a test today (Blade Ambusher disambiguation and the Bladefall/Spectral Throw collision watch, see `tests.py`). The other gem subfolders hold real captures but aren't wired into anything yet -- useful groundwork for a future embedding-model approach (see `AI_RAMBLINGS.md`'s "Local embedding model" section), not currently exercised by any suite.
- **`currencies/variance/`** -- flat files, `<name>_<N>.png`. Not scanned by any test (`run_gem_presence_tests` only lists `test_data/currencies/`'s top level). Exists purely as an archive of the extra captures found while deduplicating the main folder.
- **`scarabs/variants/`** -- flat files, named after the underlying GGG asset (e.g. `tier4scarableague3.png`) rather than this project's usual display-name convention. Same non-scanned status as `currencies/variance/`.

Add more here as real captures turn up rather than letting them depend on a capture archive folder sticking around. If a variance folder is intended to strengthen a test, rather than sit as an archive, it needs to be wired into `tests.py` explicitly (see `_blade_ambusher_disambiguation_cases` for the pattern) -- dropping a file into one of these folders alone doesn't do that on its own.
