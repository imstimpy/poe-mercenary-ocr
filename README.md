# Introduction
A utility born out of a need to log Path of Exile mercenary encounters as quickly, and with as few interactions, as possible.

## Requirements
Briefly, this is a Python project and follows standard Python setup. Additionally, the Tesseract OCR engine must be installed separately (not a pip package).

See `DEPENDENCIES.md` for install steps.

## Use
To capture mercenary data:
 1. Launch the utility using a terminal
 2. When viewing the mercenary inventory, press F9 
   - Images and text data will be captured, going to the clipboard, the image archives, and the text log

`python ./capture_pipeline.py`

### Outputs
An encounter is captured in multiple places:
 1. In a `captures` folder, made unique by a timestamp. Assets include extracted images from mercenary UI (name, equipment, skills, etc) and its generated warrant. Captures are grouped by purpose:
   - `captures/endgame/<timestamp>/` -- lvl68+ map captures (default location for captures).
   - `captures/campaign/<timestamp>/` -- sub-lvl 68 campaign captures (saved here when campaign mode is chosen); scanned by `tools/harvest_campaign_review.py` and kept out of the support harvest.
   - `captures/campaign_archive/<timestamp>/` -- post-reviewed batch of campaign captures that the support harvest does read.
   - `captures/legacy/<timestamp>/` -- the earliest batch, from before warrants/levels were captured.
 2. In a `logs` folder, stored into a tsv. Each encounter stores the timestamp and details of the encounter (name, infamy, gems, map) which can be imported into a spreadsheet.
 3. In the global clipboard, as a means to paste into a spreadsheet.

#### Warrants
A mercenary warrant is generated from the mercenary UI. This output is intended to exactly match* a CTRL+C of a warrant accessed by a "Rematch".

'* In limited cases it is possible to have an icon+tier collision (two or more different supports share the same icon). Instead of one skill a warrant will have both ("X or Y"). See [assets/README.md](assets/README.md#ambiguous-support-icons).

## Tests
Development and functionality can be validated through unit and functional tests. Coverage is so-so overall and tends to be better around difficult or failing areas.

`python ./tests.py`

Image recognition and Image recognition and OCR tests use mss-based captures, stored in `test_data`, as references.

To resolve pending tests, locate the images in the image archives and copy them into the test data. Using any sources other than mss-based captures (e.g., WIN+SHIFT+S, Greenshot, etc) had observed [imbalanced] deviations in RBG composition relating to gamma/brightness.

## Related tools
[Mercenary Trade Search](https://pob.codes/tools/mercenaries/)
[Mercenary Dataset Collector](https://github.com/AdamZ-8113/mercenary-dataset-collector)
[Perandus Ledger - Build a Mercenary](https://xddbsns.com/mercenary-builder.html)
[Awakened PoE Trade](https://snosme.github.io/awakened-poe-trade/)

## Disclosures
Independent fan-made tool, not affiliated with, authorized, maintained, sponsored, or endorsed by Grinding Gear Games. Contains assets derived from Path of Exile game data owned by Grinding Gear Games.

See `THIRD_PARTY_NOTICES.md` for full details.

AI is in use for this project with human oversight.