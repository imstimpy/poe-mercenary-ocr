# Reference Assets

Known-good icon assets are used in `match_icon` as references for the capture_pipeline.py image recognition. 

There are three possible sources for these icons:
 1. In-game screen captures
 2. Official GGG CDN assets
 3. Game asset extractions

 Comparing same-same in-game screen captures (same resolution, same source) affords the "simplest" comparison path by using direct pixel comparison. It is also the least portable: any differences in the graphics settings (resolution, AA, lighting, etc) between the client computer and the original source will affect image recognition.
 
 Official assets or game-extractions can add portability but requires resolution scaling and/or conceptual comparison. Gems, in particular, receive additional rendering effects (e.g., a gold glow of the gems) making same-different comparisons quite demanding.

## In-game screen captures

In-game captures are cropped from screenshots from the running game engine. The universality of these captures is extremely limited!

## Official GGG CND assets

Pre-composed artwork primarily for use on official GGG websites. Preliminary checks show these are worse matches than in-game captures.

## Game-extracted artwork

Extracted artwork (gems, currency, scarabs, skills, and skill supports) are the assets the game renders. Some of it looks promising while other artwork comes with some caveats. Currency, scarabs, and skills all appear to be rendered without additional effects (i.e., what you extract is what you see in-game). Gems, however, are composed from multiple layers then receive additional rendering effects when shown in-game. Because these effects are not stored as art, they cannot be recreated outside of the game engine. Skill supports are unverified. 

### Gems

Gems are using in-game screen captures through significant trial and error with different assets and various image recognition strategies.

### Scarabs

Scarabs are not yet active. Preliminary testing shows scarab outlines matter more than color, and gold hues matter more than blue hues.

### Skills

Skills are not yet active. Preliminary testing shows skills can be identified using either extracted image assets or OCR (or both).

### Skill Supports

Skill supports are not yet active. Preliminary testing shows skill supports fall into the same problem domain as gems.

## Structure

`match_icon` only scans the categories listed in `REFERENCE_CATEGORIES`
(in capture_pipeline.py). Assets within `REFERENCE_CATEGORIES` will be loaded automatically. Assets within other folders will be added.

```
assets/
  gems/                    (scanned -- listed in REFERENCE_CATEGORIES)
    spectral_helix_of_trarthus.png
  pending_currency/        (NOT scanned -- not on that list)
    ancientorb.png
  pending_gems/            (NOT scanned -- not on that list)
    dark_bargain_of_trarthus.png
  pending_skills/          (NOT scanned -- not on that list)
    smite.png
  pending_scarabs/         (NOT scanned -- not on that list)
  currency/     (create as needed, then add "currency" to REFERENCE_CATEGORIES)
  scarabs/      (create as needed, then add "scarabs" to REFERENCE_CATEGORIES)
```

## Naming convention

Names of all assets should be lowercase, underscores instead of spaces, no other punctuation.

```
spectral_helix_of_trarthus.png      correct
Spectral Helix of Trarthus.png      wrong -- spaces, capitals
spectral-helix-of-trarthus.png      wrong -- hyphens
```

The filename (without extension) is what `match_icon()` returns and
what ends up in the log, so consistency here matters for anything
downstream that compares or aggregates those names.

Use `slugify_reference_name()` (in capture_pipeline.py) to convert a
display name to this convention rather than typing it by hand:

```python
>>> slugify_reference_name("Spectral Helix of Trarthus")
'spectral_helix_of_trarthus'
```

## Known limitations

- **Machine-specific calibration** Screen-captured references are calibrated to the resolution, UI scale, and graphics settings of the machine that captured them. These regions are defined in `definitions/mercenary_regions.json`. Portable options have been discussed but not yet implemented; see `AI_RAMBLINGS.md` for the reasoning.
- **Unknown Blade Ambusher gems** Blade Ambusher is the only mercenary that can have 1 of 2 different gems; all others are 1:1. Due to limitations in the gem differentiation, Spectral Helix and Spectral Throw cannot be disambiguated. The pipeline reports this case as `"Unknown (Blade Ambusher)"` rather than guessing. See `AI_RAMBLINGS.md` for the investigation of this limitation.
 
## What's actually live

Live:
 - Gems

Not Live:
 - Currency
 - Scarabs
 - Skills
 - Supports
 - Uniques

### Gems

Gem detection circumvents the difficulties of positive, unique gem differentiation by combining a gem presence check with a mercenary-type lookup: a mercenary's rucksack can only contain its own signature gem. This resolves 10 of 12 gems with no image-matching risk at all with the Blade Ambusher limitation noted above.

### Skills and supports

Not implemented. Signs point to OCR as a definitive solution for skill identification so skill assets may never be used. Supports, on the other hand, need more investigation.

### Currency and scarabs

Positive detection of currency and scarabs may one day fit into a notification of importance model. For cataloguing mercenaries and their gems, currency and scarabs are only used as a means to detect gems.

### Uniques

Identification of uniques of importance may one day fit into a notification of importance model. For cataloguing mercenaries and their gems, unique detection is not used. A brief trial was done to detect the red/brown corner filigree associated with a unique item, but no effort was made to identify the uniques themselves. No notes exist for the trial work.

