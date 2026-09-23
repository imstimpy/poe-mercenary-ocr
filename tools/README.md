# Tools
Bespoke tools live here. Community tools live in the community.

The data extracted from game data comes in two forms:
 - Definitions
 - Artwork/icons

PoE stores a lot of helpful information in its files. Some data may be stored with self-describing names like "smite". Data may also be stored as an historical or unintuitive name like "regretorb" when referring to "Orb of Unmaking". Most (all?) data can be decoded by navigating the relational database by tracing the foreign key ("_rid", for example) from one file to the next until it full resolves to a string or file name.

3rd party tools are required to open/view/export game content. See [Existing community tools](#existing-community-tools) below.

## Bespoke

These bespoke tools extract assets from the game data and transform them into a usable form.

 - `extract_mercenary_skills.py` - Extracts mercenary names, skills, and supports from game files.
   - Run: `python extract_mercenary_skills.py` (no arguments; run from `tools/`)
   - Reads: `extracted_dats/`
   - Writes: `mercenary_jsons/` -- review and move into `../definitions/`

 - `compose_gem_reference.py` - Assembles the layers of a gem extraction into a single gem asset.
   - Run: `python compose_gem_reference.py <raw_sprite.png> <gem_slug>` (run from the project root)
   - Writes: `assets/pending_gems/<gem_slug>.png`

 - `harvest_support_icons.py` - Builds labeled support icon references from encounters.
   - Run: `python harvest_support_icons.py` (no arguments; safe to run from `tools/` or the project root)
   - Reads: `captures/`, `__captures_*/`, and the hand-maintained `tier_notes.json` / `manual_labels.json` overrides alongside its output
   - Writes: `assets/harvested_supports/`

 - `generate_support_coverage_report.py` - Regenerates the missing/weak support-icon coverage report. Run after `harvest_support_icons.py`. Splits missing keys that structurally can't or probably won't ever get a captured reference into their own groups (a data-proven zero-breadth group, and a hand-flagged "suspected non-live" group -- see `assets/support_icon_coverage_notes.json`) instead of cluttering the main hunting-targets table with them.
   - Run: `python generate_support_coverage_report.py` (no arguments; run from `tools/`)
   - Reads: `definitions/supports.json`, `definitions/skills_by_mercenary.json`, `definitions/supports_by_skills.json`, `assets/harvested_supports/manifest.json`, `assets/support_icon_coverage_notes.json` (hand-maintained, optional)
   - Writes: `assets/support_icon_coverage.md`

 - `match_support_icon.py` - Support-icon classifier accuracy report against real `warrant.txt` ground truth (100% on every real crop on hand). The classifier itself is promoted into `capture_pipeline.py` (`match_support_icon`/`extract_support_names`); this script is now just its validation/regression report.
   - Run: `python match_support_icon.py` (no arguments; run from the project root)
   - Reads: `assets/harvested_supports/`, `definitions/supports.json`, `captures/`, `__captures_*/`

 - `export_support_reference_embeddings.py` - Precomputes support-icon reference embeddings for production use. Run after `harvest_support_icons.py`.
   - Run: `python export_support_reference_embeddings.py` (no arguments; run from the project root)
   - Reads: `assets/harvested_supports/`
   - Writes: `assets/models/support_reference_embeddings.json`
   - Writes: nothing (report to stdout)

 - `generate_support_collision_report.py` - Regenerates the list of ambiguous support icons, split into real same-skill collisions vs. shared-icon-but-different-skills. Run after `definitions/supports.json` or `definitions/supports_by_skills.json` changes.
   - Run: `python generate_support_collision_report.py` (no arguments; run from `tools/`)
   - Reads: `definitions/supports.json`, `definitions/supports_by_skills.json`
   - Writes: `assets/support_icon_collisions.md`

 - `generate_manual_labeling_page.py` - Generates a local, offline HTML review page for identifying support crops `harvest_support_icons.py` couldn't resolve on its own (real crops from non-warrant captures, narrowed to a short candidate list but not to one). Click a crop's correct support and copy the resulting JSON into `manual_labels.json` -- the one mechanism that survives a re-harvest; renaming the crop files directly does not (`harvest_support_icons.py` deletes and rebuilds every top-level PNG on each run). Not published anywhere -- these are real GGG game assets (see `THIRD_PARTY_NOTICES.md`), so this stays a local file you open directly in a browser.
   - Run: `python generate_manual_labeling_page.py` (no arguments; run from `tools/`)
   - Reads: `assets/harvested_supports/manifest.json`
   - Writes: `assets/harvested_supports/manual_review.html` (gitignored -- regenerate it fresh rather than reusing a stale copy)

 - `harvest_campaign_review.py` - Isolated, icon-recognition-free reviewer for `captures_campaign/` only (never `captures/` or any `__captures_*/` archive -- structurally excluded by `harvest_support_icons.py`'s own glob patterns, not just convention). Builds an independent "shadow ground truth" for sub-68 mercenaries by having a human read each support's real name/tier off the in-game tooltip; candidates shown are anchored only to the contributing skill(s)' real `PossibleSupports`, never to icon matching. Never touches `assets/harvested_supports/`, `manual_labels.json`, `tier_notes.json`, or the reference embeddings -- the whole point is auditing that pipeline independently of it. See `.claude/skills/campaign-review/SKILL.md` for the live capture-and-review loop this is meant to run inside.
   - Run: `python harvest_campaign_review.py` (no arguments; run from `tools/` or the project root; safe to run repeatedly)
   - Reads: `captures_campaign/*/supports.png` + `warrant_generated.txt`, `definitions/supports_by_skills.json`, `definitions/supports.json`, `assets/harvested_supports/campaign_promotions.json` (read-only -- a promoted hash counts as already answered, see `promote_campaign_truth.py` below), and the system clipboard (for a completed review page's copied answers)
   - Writes: `assets/campaign_review/manual_truth.json` (the actual data -- not gitignored), `assets/campaign_review/review.html` + `crops/*.png` (both gitignored, regenerated fresh each run)

 - `audit_campaign_truth.py` - Read-only audit: checks `capture_pipeline.match_support_icon()` against `manual_truth.json`, the independent shadow ground truth `harvest_campaign_review.py` built. This is the actual point of the whole campaign-review pipeline -- `match_support_icon.py`'s own 100% figure is measured almost entirely on Tier II/III data, since a real warrant.txt is structurally impossible below level 68.
   - Run: `python audit_campaign_truth.py` (no arguments; run from `tools/` or the project root)
   - Reads: `assets/campaign_review/manual_truth.json`, `assets/campaign_review/crops/*.png` (regenerate first with `harvest_campaign_review.py` if a crop file is missing)

 - `promote_campaign_truth.py` - Deliberately promotes a *specific* campaign shadow-ground-truth identification into the production catalog, for a real (icon, tier) gap that may never show up in a warrant-backed capture at all (see `assets/support_icon_coverage.md`'s missing list, skewed toward Tier I). A campaign identification is read straight off the real tooltip with zero icon-matching involvement -- actually cleaner provenance than a `manual_labels.json` entry. Removes the promoted hash from `manual_truth.json` (its new home is `campaign_promotions.json`) so it stops counting toward `audit_campaign_truth.py`'s numbers once it's also a reference image -- auditing the classifier against its own reference would be circular. Re-run `harvest_support_icons.py` afterward to fold it into `manifest.json`.
   - Run: `python promote_campaign_truth.py <hash-or-prefix> [<hash-or-prefix> ...]` (run from `tools/` or the project root)
   - Reads: `assets/campaign_review/manual_truth.json`, `assets/campaign_review/crops/*.png`, `definitions/supports.json`
   - Writes: `assets/harvested_supports/campaign_promotions.json` + `campaign_promoted/<hash>.png` (both committed), `assets/campaign_review/manual_truth.json` (removes each promoted hash)

 - `generate_campaign_audit_coverage.py` - Tracks, per Tier I (icon, tier) key in the production catalog, whether it's ever been independently confirmed through the campaign UI (`manual_truth.json`/`campaign_promotions.json` hash match) versus only trusted via `ground_truth`/`embedding_confirmed` self-resolution. Found a real, sizeable gap this way once already (see `AI_RAMBLINGS.md`) -- this makes that an ongoing, regenerable number instead of a one-off check. Run after `harvest_support_icons.py` or `harvest_campaign_review.py` change either input.
   - Run: `python generate_campaign_audit_coverage.py` (no arguments; run from `tools/` or the project root)
   - Reads: `assets/harvested_supports/manifest.json`, `assets/campaign_review/manual_truth.json`, `assets/harvested_supports/campaign_promotions.json`
   - Writes: `assets/campaign_audit_coverage.md`

 - `export_gem_embedding_model.py` - Builds the gem-presence embedding model. Needs `requirements-dev.txt` installed (not part of normal setup -- see `DEPENDENCIES.md`).
   - Run: `python export_gem_embedding_model.py` (no arguments; run from `tools/`)
   - Reads: `assets/gems/`
   - Writes: `assets/models/`

See `../THIRD_PARTY_NOTICES.md` -- these data and icons are Grinding Gear Games' data.  

## Mercenary and skill definitions

Mercenary, mercenary skill, and mercenary skill support definitions can be extracted from game data by traversing numerous files linked by keys.

 1. Using [poe-dat-export](https://github.com/moepmoep12/poe-dat-export) or similar, export the following files to JSON and store them in a temporary folder such as `tools/extracted_dats`:
   - `data/mercenarybuilds.datc64` - infamous and non-infamous mercenary types
   - `data/mercenaryskills.datc64` - mercenary skills, support count, and supports
   - `data/mercenarysupportcounts.datc64` - support counts
   - `data/mercenarysupports.datc64` - supports
   - `data/mercenarysupportfamilies.datc64` - human-readable name for each support's family
 2. Extract the definitions
 ```
 $ cd tools
 $ python ./extract_mercenary_skills.py
 ```
 3. Review the output and move them into `definitions`

 ## Image assets

 Many image assets can be viewed or exported directly from the game data. Some existing community tools may likely exist to extract these files. The example in [Gem assets](#gem-assets) details how to extract gems, which requires a lookup of the "_rid" first; this is similar to the skill icons. Currencies and scarabs are viewed directly without relationships.
 
 ### Currency assets

 Pending currency assets are stored as-is, directly from game data extractions. The extraction was done by hand - no automated scripts.

 Found in art/2ditems/currency/<currency>.dds where `../assets/currency_raw_asset_names.json` provides the display-name -> raw
 asset-name mapping.
 
 ### Gem assets

 There are two gem assets:
  - gems - **ACTIVE** in-game captures
  - pending_gems - **INACTIVE** game data extractions

 Gem assets are stored as in-game captures.
 
 Pending gem assets are stored as composited images created from `compose_gem_reference.py`. The extraction was done by hand - no automated extraction.

 art/2ditems/gems/<gem>.dds where `../assets/gem_raw_asset_names.json` provides the display-name -> raw
 asset-name mapping.

1. Using [poe-dat-viewer](https://snosme.github.io/poe-dat-viewer/) or similar, export the following file to JSON:
 - `data/baseitemtypes.datc64`
 - `data/itemvisualidentity.datc64`
2. Search within `baseitemtypes.json` for the "id:" field matching the skill (e.g., "Metadata/Items/Gems/SkillGemSiegeBallista")
   - Note the ItemVisualIdentityKey ("ItemVisualIdentity": 9699)
3. Search within `itemvisualidentity.json` for the "_rid" matching the ItemVisualIdentity ("_rid": 9699)
   - Note the DDSFile ("DDSFile": "Art/2DItems/Gems/CrossBowTotemGem.dds")
4. Using [VisualGGPK2](https://github.com/aianlinb/VisualGGPK2) or similar
  1. Open Content.ppk
  2. Browse to DDSFile ("Art/2DItems/Gems/CrossBowTotemGem.dds")
  3. Right Click | Convert dds to png

#### Compositing an extracted gem into a reference

A `.dds`-derived gem asset from the steps above isn't immediately usable as a reference - it's a sheet containing multiple separate assets of different sizes/shapes:
 - A gold symbol/overlay
 - A colored gem base
 - A separate sparkle background.
 - Some unidentified gold glow, either applied as an unknown overlay or a rendered effect.

 `compose_gem_reference.py` finds the overlay and gem base in the sheet and composites the overlay centered on the base.

```
python compose_gem_reference.py <raw_sprite.png> <gem_slug>
```

Writes to `assets/pending_gems/<gem_slug>.png` -- not a validated reference, same holding area as every other not-yet-validated extracted asset.

A complete application of the sparkle background and gold glow (to 100% replicate what is seen in the rucksack) has not been successful. 

### Scarab assets

Pending scarab assets are stored as-is, directly from game data extractions. The extraction was done by hand - no automated scripts.

 Found in art/2ditems/currency/scarabs/<scarab>.dds where `../assets/scarab_raw_asset_names.json` provides the display-name -> raw
 asset-name mapping.

### Skill assets

Pending skill assets are stored as-is, directly from game data extractions. The extraction was done by hand - no automated scripts. Should these be necessary, some 271 skills will need to be extracted with an [unwritten] extraction script. 

Found in art/2dart/skillicons/<skill>.dds where `../assets/skill_raw_asset_names.json` provides the display-name -> raw
asset-name mapping.

Exports:
 - `data/mercenaryskills.datc64`
 - `data/grantedeffects.datc64`
 - `data/activeskills.datc64`

"Name" `mercenaryskills.json` ("GrantedEffect") -> "_rid" `grantedeffects.json` ("ActiveSkill") -> "_rid" `activeskills.json` Icon_DDSFile for to <skill>.dds.

### Skill support assets

Pending skill support assets are stored as-is, directly from game data extractions. The extraction was done by hand - no automated scripts.

The display-name -> raw asset-name lookup IS automated. See [Mercenary and skill definitions](#mercenary-and-skill-definitions)

Found in art/2ditems/gems/support/<support>.dds where `../definitions/supports.json` provides the display-name -> raw asset-name mapping.

**Possible future direction (not started, not planned yet):** a fully scripted, portable alternative to this project's current live-capture-based support catalog (`assets/harvested_supports/`) -- one game-extracted icon reference per icon (not per tier, since the tier badge is a render-time overlay the raw asset doesn't have -- see `harvest_support_icons.py`'s `visual_key_for_name()` docstring), with tier resolved at classification time by the already-built, reference-independent `_resolve_support_tier_from_badge()` in `capture_pipeline.py`. Immune to resolution/UI changes and portable to any user's install, unlike the current corpus. [jcmoyer/PoET](https://github.com/jcmoyer/PoET) and PyPoE reportedly have command-line interfaces (unlike VisualGGPK2's GUI-only export), which could make the full chain -- ppk extraction, asset-path lookup from the already-exported mercenary skill JSON, `.dds`-to-`.png` conversion -- scriptable end to end instead of done by hand. Unverified (neither tool has been tried against this specific extraction yet).

Exports:
 - `data/mercenarysupports.datc64`
 - `data/mercenarysupportfamilies.datc64`

# Existing community tools 
 - [poe-dat-export](https://github.com/moepmoep12/poe-dat-export)
 - [RePoE](https://repoe-fork.github.io/)
 - [poe-dat-viewer](https://snosme.github.io/poe-dat-viewer/)
 - [VisualGGPK2](https://github.com/aianlinb/VisualGGPK2) and Content.ppk
 - [PyPoE](https://github.com/omegak2/pypoe)
