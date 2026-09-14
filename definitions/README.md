# Summary
Definitions are user-friendly renderings of the "relational database" within Path of Exile. 

Some definitions were created by hand while others were extracted and transformed from game files using scripts.General organization and tools are listed immediately below, progressing to specific extraction steps far below.

See `../THIRD_PARTY_NOTICES.md` -- these data are Grinding Gear Games' data.

## File Structure
Definitions are organized by domain.

 - 'mercenary_types.json' - Each mercenary type
 - |-'skills_by_mercenary.json' - Each mercenary type's primary, secondary, and utility skills
 - |--'supports_by_skills.json' - Each skill's supports
 - |---'supports.json' - Each support's own icon and its support family
 - 'gems_by_mercenary.json' - Each mercenary type's associated Trarthus gem (hand-authored, not part of the extract_mercenary_skills.py pipeline)
 - 'mercenary_regions.json' - The fixed pixel positions of each mercenary UI element.

 Definitions are name-keyed (skill names, gem names, etc) rather than mirroring the game's ID-based relational structure. These files are small(ish) and hand-inspected so name-keys stay.

## Extracting Definitions
Many sources for mercenary definitions exist (PoE wiki, poedb.tw, pathofexile.com) but the ultimate source is the game itself. Definitions, where possible, are extracted directly from the game and adapted for use.

See `../tools/README.md` for data extraction steps and tools. 
