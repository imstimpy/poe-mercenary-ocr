# Summary
Definitions are user-friendly renderings of the "relational database" within Path of Exile. 

Some definitions were created by hand while others were extracted and transformed from game files through scripts. See THIRDPARTYDISCLOSURES. General organization and tools are listed immediately below, progressing to specific extraction steps far below.

## File Structure
Definitions are organized by domain.

 - 'mercenary_types.json' - Each mercenary type
 - |-'skills_by_mercenary.json' - Each mercenary type's primary, secondary, and utility skills
 - |--'supports_by_skills.json' - Each skill's supports

 Definitions are name-keyed (skill names, gem names, etc) rather than mirroring the game's ID-based relational structure. These files are small(ish) and hand-inspected so name-keys stay.


## Extracting Definitions
Many sources for mercenary definitions exist (PoE wiki, poedb.tw, pathofexile.com) but the ultimate source is the game itself. Definitions, where possible, are extracted directly from the game and adapted for use.

### For Mercenaries, Skills and Supports
 1. Using [poe-dat-export](https://github.com/moepmoep12/poe-dat-export) or similar, export the following files to JSON and store them temporarily in `tools`:
   - `mercenarybuilds.datc64` - infamous and non-infamous mercenary types
   - `mercenaryskills.datc64` - mercenary skills, support count, and supports
   - `mercenarysupportcounts.datc64` - support counts
   - `mercenarysupports.datc64` - supports

 2. Extract the definitions
 ```
 $ cd tools
 $ python ./extract_mercenary_skills.py
 ```
 3. Review the output and move them into `definitions`
