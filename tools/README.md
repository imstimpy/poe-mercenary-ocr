# Tools
Bespoke tools live here. Community tools live in the community.

The data extracted from game data comes in two forms:
 - Definitions
 - Artwork/icons

PoE stores a lot of helpful information in its files. Some data may be stored with self-describing names like "smite". Data may also be stored as an historical or unintuitive name like "regretorb" when referring to "Orb of Unmaking". Most (all?) data can be decoded by navigating the relational database by tracing the foreign key ("_rid", for example) from one file to the next until it full resolves to a string or file name.

3rd party tools are required to open/view/export game content. See [Existing community tools](#existing-community-tools) below.

## Bespoke
 - `extract_mercenary_skills.py` - Extracts mercenary names, skills, and supports from game files
 - `compose_gem_reference.py` - Assembles the layers of gem extractions into a single gem asset

 These bespoke tools extract assets from the game data with and transform these assets into a usable form.

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

Exports:
 - `data/mercenarysupports.datc64`
 - `data/mercenarysupportfamilies.datc64`

# Existing community tools 
 - [poe-dat-export](https://github.com/moepmoep12/poe-dat-export)
 - [RePoE](https://repoe-fork.github.io/)
 - [poe-dat-viewer](https://snosme.github.io/poe-dat-viewer/)
 - [VisualGGPK2](https://github.com/aianlinb/VisualGGPK2) and Content.ppk
 - [PyPoE](https://github.com/omegak2/pypoe)
