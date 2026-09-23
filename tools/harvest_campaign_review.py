"""
Isolated, icon-recognition-free review pipeline for `captures_campaign/`
-- deliberately separate from harvest_support_icons.py and everything
under assets/harvested_supports/. Purpose: build an independent "shadow
ground truth" for sub-68 mercenaries (a real warrant is structurally
impossible below level 68, see AI_RAMBLINGS.md) by reading each
support's real name/tier straight off the in-game tooltip, with ZERO
input from match_support_icon() or the reference catalog. The whole
point is auditing what the icon classifier believes against a source
that's independent of it -- so this must never let that same classifier
feed its own audit, and must never write anything back into
assets/harvested_supports/ (tainting the very thing being checked).

Candidate buttons per crop are anchored ONLY to the skill(s) that
produced it -- the exact literal `PossibleSupports` strings from
definitions/supports_by_skills.json, tier already baked in (e.g.
"Lesser Chain I") -- never icon similarity. Keeps the option list small
and skill-relevant without the image-matching pipeline this data exists
to check ever being consulted.

Usage (run repeatedly during a capture session -- this IS the fast
loop, see .claude/skills/campaign-review/SKILL.md):
    python harvest_campaign_review.py

Each run first checks the clipboard for a completed review page's
copied JSON and merges any valid entries into manual_truth.json, then
rescans captures_campaign/ fresh and regenerates review.html for
whatever's still unresolved (including any brand new captures since the
last run). Nothing here is required to run in any particular directory
relationship to capture_pipeline.py's own campaign-mode captures beyond
both agreeing on CAMPAIGN_CAPTURE_DIR's location.

"Still unresolved" also checks campaign_promotions.json, not just
manual_truth.json -- a hash promoted into the production catalog (see
tools/promote_campaign_truth.py) is deliberately removed from
manual_truth.json, but its real capture is still sitting in
captures_campaign/ unchanged, and would otherwise resurface as if
nobody had ever answered it (a real, reproduced bug: an old capture
reappeared as "needs review" the run right after its hash got
promoted). A promoted hash is exactly as resolved as an unpromoted one,
just recorded in a different file -- read-only here, never written.

Reads: captures_campaign/*/supports.png + warrant_generated.txt,
    definitions/supports_by_skills.json, definitions/supports.json,
    assets/harvested_supports/campaign_promotions.json (read-only)
Writes: assets/campaign_review/manual_truth.json (full_hash -> exact
    literal supports.json name, e.g. "Lesser Chain I")
    assets/campaign_review/review.html (open directly in a browser)
    assets/campaign_review/crops/*.png (crop images review.html shows)

Never writes to assets/harvested_supports/, manual_labels.json,
tier_notes.json, or support_reference_embeddings.json.
"""
import hashlib
import json
import os
import re
import sys

import numpy as np
from PIL import Image

try:
    import pyperclip
    _CLIPBOARD_AVAILABLE = True
except ImportError:
    _CLIPBOARD_AVAILABLE = False

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TOOLS_DIR)
sys.path.insert(0, TOOLS_DIR)
import harvest_support_icons as harvester

CAMPAIGN_CAPTURE_DIR = os.path.join(PROJECT_ROOT, "captures_campaign")
SUPPORTS_BY_SKILLS_PATH = os.path.join(PROJECT_ROOT, "definitions", "supports_by_skills.json")
SUPPORTS_PATH = os.path.join(PROJECT_ROOT, "definitions", "supports.json")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "assets", "campaign_review")
TRUTH_PATH = os.path.join(OUTPUT_DIR, "manual_truth.json")
REVIEW_PATH = os.path.join(OUTPUT_DIR, "review.html")
CROPS_DIR = os.path.join(OUTPUT_DIR, "crops")
# A promoted hash (see tools/promote_campaign_truth.py) is deliberately
# REMOVED from manual_truth.json once it's also a production reference
# image -- but the real capture that originally produced it is still
# sitting in captures_campaign/ untouched, and any OTHER real encounter
# that happens to roll the exact same (icon, tier) hashes identically
# too. Without also checking this file, this script has no way to know
# that hash is already fully resolved, and would keep resurfacing it as
# "needs review" forever -- a real, reproduced bug: an old capture from
# hours earlier reappeared as unanswered the run right after its hash
# got promoted, even though nothing about that capture had changed.
PROMOTIONS_PATH = os.path.join(PROJECT_ROOT, "assets", "harvested_supports", "campaign_promotions.json")

# PossibleSupports strings always bake in a trailing tier ("Lesser Chain
# I"), but definitions/supports.json's own keys never do ("Lesser
# Chain") -- confirmed directly, same convention
# generate_support_coverage_report.py already relies on. Candidate
# buttons show the PossibleSupports form (so a real tier is always
# visible), so answers need this stripped before validating against
# supports.json or storing in manual_truth.json.
_TIER_SUFFIX_RE = re.compile(r"\s+(I|II|III)$")


def _load_json(path, default):
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def scan_clusters():
    """Walks every capture in captures_campaign/ (only -- never
    captures/ or any __captures_*/ archive) and clusters occupied cells
    by exact pixel hash, same primitive harvest_support_icons.py uses
    for its own clustering (imported directly, not reimplemented -- see
    AI_RAMBLINGS.md's note on match_icon logic moving into shared calls
    rather than staying a tweaked copy). Each cluster's candidate set is
    the union of exact PossibleSupports strings across every contributing
    skill -- no icon information anywhere in this function.
    """
    skills = _load_json(SUPPORTS_BY_SKILLS_PATH, {}).get("skills", {})
    columns, rows = harvester.load_grid()

    clusters = {}
    skipped_no_data = skipped_dropped = 0

    if not os.path.isdir(CAMPAIGN_CAPTURE_DIR):
        return clusters, 0, skipped_no_data, skipped_dropped

    capture_dirs = sorted(
        os.path.join(CAMPAIGN_CAPTURE_DIR, d) for d in os.listdir(CAMPAIGN_CAPTURE_DIR)
        if os.path.isdir(os.path.join(CAMPAIGN_CAPTURE_DIR, d))
    )

    for capture_dir in capture_dirs:
        supports_png = os.path.join(capture_dir, "supports.png")
        warrant_generated = os.path.join(capture_dir, "warrant_generated.txt")
        if not os.path.isfile(supports_png) or not os.path.isfile(warrant_generated):
            skipped_no_data += 1
            continue
        with open(warrant_generated, encoding="utf-8") as f:
            skill_list = harvester.parse_warrant_skills(f.read())
        if skill_list is None:
            skipped_dropped += 1
            continue

        im = Image.open(supports_png).convert("RGB")
        arr = np.array(im).astype(float)
        brightness = arr.mean(axis=2)

        for row_idx, skill_name in enumerate(skill_list):
            if row_idx >= len(rows):
                break
            ry0, ry1 = rows[row_idx]
            possible = skills.get(skill_name, {}).get("PossibleSupports", [])
            for col_idx, (cx0, cx1) in enumerate(columns):
                if brightness[ry0:ry1, cx0:cx1].std() < harvester.OCCUPIED_STD_THRESHOLD:
                    continue
                cell = im.crop((cx0, ry0, cx1, ry1))
                full_hash = hashlib.md5(cell.tobytes()).hexdigest()
                if full_hash not in clusters:
                    clusters[full_hash] = {"crop": cell, "candidates": set(), "occurrences": []}
                clusters[full_hash]["candidates"].update(possible)
                clusters[full_hash]["occurrences"].append(
                    (os.path.relpath(capture_dir, PROJECT_ROOT).replace("\\", "/"),
                     skill_name, row_idx, col_idx))

    return clusters, len(capture_dirs), skipped_no_data, skipped_dropped


def merge_clipboard_into_truth(clusters: dict, supports: dict, promotions: dict) -> int:
    """Reads whatever's currently on the clipboard (expected: the JSON
    review.html's own "Copy to clipboard" button produces) and merges
    valid entries into manual_truth.json. Validates each entry against
    THIS run's freshly-recomputed candidate set for that hash before
    trusting it -- catches a stale copy, a typo, or pasting something
    else entirely, same "don't silently trust an override" discipline
    resolve_manual_label() applies for the main pipeline. Returns how
    many new entries were merged.

    `promotions` (see tools/promote_campaign_truth.py) is checked so an
    already-promoted hash never gets re-added to manual_truth.json --
    it's already fully resolved and living in campaign_promotions.json
    instead; re-adding it here would just get removed again by the next
    promotion pass, or worse, silently diverge from the promoted name.
    """
    if not _CLIPBOARD_AVAILABLE:
        return 0
    try:
        raw = pyperclip.paste()
        candidate_json = json.loads(raw)
        if not isinstance(candidate_json, dict):
            return 0
    except Exception:
        return 0

    truth = _load_json(TRUTH_PATH, {})
    merged = 0
    for hash10, raw_name in candidate_json.items():
        full_hash = next((h for h in clusters if h.startswith(hash10)), None)
        if full_hash is None:
            continue  # not a hash from this batch -- stale clipboard, ignore
        if full_hash in promotions:
            continue  # already promoted -- see this function's own docstring
        name = _TIER_SUFFIX_RE.sub("", raw_name).strip()
        if name not in supports:
            print(f"  WARNING: clipboard names {raw_name!r} for hash {hash10}, but {name!r} isn't "
                  f"a real definitions/supports.json key -- check for a typo. Ignoring.")
            continue
        possible_form = f"{name} {supports[name]['tier_roman']}"
        if possible_form not in clusters[full_hash]["candidates"]:
            print(f"  WARNING: clipboard names {raw_name!r} for hash {hash10}, but no contributing "
                  f"skill for that crop can actually roll it -- ignoring rather than trusting a "
                  f"mismatched paste.")
            continue
        if full_hash in truth and truth[full_hash] != name:
            print(f"  WARNING: hash {hash10} already has {truth[full_hash]!r} recorded, clipboard "
                  f"says {name!r} -- keeping the existing entry. Edit manual_truth.json by hand if "
                  f"the original was wrong.")
            continue
        if full_hash not in truth:
            truth[full_hash] = name
            merged += 1

    if merged:
        with open(TRUTH_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(truth, f, indent=2, sort_keys=True)
    return merged


def generate_review_html(clusters: dict, known: dict):
    """`known` is manual_truth.json's entries UNION campaign_promotions.json's
    -- a promoted hash is exactly as resolved as an unpromoted one, just
    living in a different file (see this module's PROMOTIONS_PATH
    comment for why both must be checked, not just manual_truth.json)."""
    payload = []
    for full_hash, data in sorted(clusters.items(), key=lambda kv: -len(kv[1]["occurrences"])):
        if full_hash in known:
            continue
        hash10 = full_hash[:10]
        payload.append({
            "hash": hash10,
            "occurrence_count": len(data["occurrences"]),
            "skills": sorted({occ[1] for occ in data["occurrences"]}),
            "candidates": sorted(data["candidates"]),
        })

    html = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Campaign shadow ground truth review</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #1b1b1b; color: #eee; margin: 0; padding: 24px; }}
  h1 {{ font-size: 20px; }}
  p.instructions {{ max-width: 700px; line-height: 1.5; color: #ccc; }}
  #output-wrap {{ position: sticky; top: 0; background: #111; padding: 12px; border: 1px solid #444;
                  margin-bottom: 20px; z-index: 10; }}
  #output {{ width: 100%; height: 100px; background: #000; color: #7fd; font-family: monospace;
             font-size: 12px; box-sizing: border-box; }}
  .grid {{ display: flex; flex-wrap: wrap; gap: 16px; }}
  .card {{ background: #262626; border: 1px solid #3a3a3a; border-radius: 6px; padding: 10px;
           width: 280px; }}
  .card.done {{ opacity: 0.35; }}
  .card img {{ width: 128px; height: 128px; image-rendering: pixelated; display: block; margin: 0 auto 8px; }}
  .hash {{ font-size: 11px; color: #999; margin-bottom: 4px; }}
  .skills {{ font-size: 11px; color: #bbb; margin-bottom: 8px; }}
  .candidates button {{ display: block; width: 100%; margin: 2px 0; padding: 5px; font-size: 12px;
                         text-align: left; background: #333; color: #eee; border: 1px solid #555;
                         border-radius: 4px; cursor: pointer; }}
  .candidates button:hover {{ background: #457; }}
  .freetext input {{ width: 100%; margin-top: 6px; box-sizing: border-box; padding: 4px;
                      background: #111; color: #eee; border: 1px solid #555; }}
  .picked {{ margin-top: 6px; font-size: 12px; color: #7d7; }}
  button.copy {{ margin-top: 8px; padding: 6px 12px; }}
</style>
</head>
<body>
<h1>Campaign shadow ground truth review</h1>
<p class="instructions">
  Hover the real support in the game's UI to read its exact name/tier,
  then click the matching button below (or type it if the wording
  differs slightly from what's shown -- must be the exact literal name
  from <code>definitions/supports.json</code>, e.g. "Lesser Chain").
  These options come ONLY from what the contributing skill(s) can
  structurally roll -- never from icon recognition. When done, click
  "Copy to clipboard", then run
  <code>python tools/harvest_campaign_review.py</code> again -- it reads
  the clipboard, merges your answers into <code>manual_truth.json</code>,
  and regenerates this page for whatever's newly captured or still
  unresolved.
</p>
<div id="output-wrap">
  <div><span id="count">0</span> identified so far this session</div>
  <textarea id="output" readonly></textarea>
  <button class="copy" onclick="copyOutput()">Copy to clipboard</button>
</div>
<div class="grid" id="grid"></div>
<script>
  const CLUSTERS = {json.dumps(payload)};
  const results = {{}};

  function render() {{
    document.getElementById("count").textContent = Object.keys(results).length;
    document.getElementById("output").value = JSON.stringify(results, null, 2);
  }}
  function copyOutput() {{
    const el = document.getElementById("output");
    el.select();
    document.execCommand("copy");
  }}
  function pick(hash, name) {{
    if (!name) return;
    results[hash] = name;
    const card = document.getElementById("card-" + hash);
    card.classList.add("done");
    card.querySelector(".picked").textContent = "-> " + name;
    render();
  }}
  function buildCard(cluster) {{
    const card = document.createElement("div");
    card.className = "card";
    card.id = "card-" + cluster.hash;

    const img = document.createElement("img");
    img.src = "crops/" + cluster.hash + ".png";
    img.alt = cluster.hash;
    card.appendChild(img);

    const hashLine = document.createElement("div");
    hashLine.className = "hash";
    hashLine.textContent = "hash " + cluster.hash + "  --  seen " + cluster.occurrence_count + "x";
    card.appendChild(hashLine);

    const skillsLine = document.createElement("div");
    skillsLine.className = "skills";
    skillsLine.textContent = "Contributing skills: " + cluster.skills.join(", ");
    card.appendChild(skillsLine);

    const cands = document.createElement("div");
    cands.className = "candidates";
    for (const name of cluster.candidates) {{
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = name;
      btn.addEventListener("click", () => pick(cluster.hash, name));
      cands.appendChild(btn);
    }}
    card.appendChild(cands);

    const freetext = document.createElement("div");
    freetext.className = "freetext";
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "or type the exact supports.json name";
    input.addEventListener("keydown", (e) => {{
      if (e.key === "Enter") pick(cluster.hash, input.value);
    }});
    freetext.appendChild(input);
    card.appendChild(freetext);

    const picked = document.createElement("div");
    picked.className = "picked";
    card.appendChild(picked);
    return card;
  }}

  const grid = document.getElementById("grid");
  if (CLUSTERS.length === 0) {{
    const done = document.createElement("div");
    done.style.color = "#7d7";
    done.textContent = "Nothing to review -- every crop captured so far has a recorded answer.";
    grid.appendChild(done);
  }}
  for (const cluster of CLUSTERS) {{
    grid.appendChild(buildCard(cluster));
  }}
</script>
</body>
</html>
'''
    with open(REVIEW_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    return len(payload)


def main():
    with open(SUPPORTS_PATH, encoding="utf-8") as f:
        supports = json.load(f)["supports"]

    clusters, capture_count, skipped_no_data, skipped_dropped = scan_clusters()
    promotions = _load_json(PROMOTIONS_PATH, {})

    merged = merge_clipboard_into_truth(clusters, supports, promotions)
    if merged:
        print(f"Merged {merged} new answer(s) from the clipboard into {TRUTH_PATH}")

    truth = _load_json(TRUTH_PATH, {})
    known = {**promotions, **truth}

    os.makedirs(CROPS_DIR, exist_ok=True)
    for f in os.listdir(CROPS_DIR):
        os.remove(os.path.join(CROPS_DIR, f))
    for full_hash, data in clusters.items():
        data["crop"].save(os.path.join(CROPS_DIR, f"{full_hash[:10]}.png"))

    remaining = generate_review_html(clusters, known)

    print(f"Capture dirs found: {capture_count} "
          f"(skipped: {skipped_no_data} no data, {skipped_dropped} dropped/unrecognized row)")
    print(f"Unique crops: {len(clusters)} total, {len(known)} answered so far "
          f"({len(promotions)} already promoted into the production catalog), "
          f"{remaining} need review")
    print(f"Open {REVIEW_PATH} directly in a browser.")


if __name__ == "__main__":
    main()
