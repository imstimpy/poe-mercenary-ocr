"""
Generates a local, offline HTML review page for manually identifying
support crops that harvest_support_icons.py couldn't resolve on its
own -- the "ambiguous, inferred candidate list" bucket (real crops from
captures with no warrant.txt, narrowed to a short candidate list via
each contributing skill's PossibleSupports pool, but not down to one).

Deliberately NOT published as a Claude Artifact: these crops are real
GGG game assets (see THIRD_PARTY_NOTICES.md), so this stays a local
file referencing the images by relative path, opened directly in a
browser -- nothing uploaded anywhere.

Deliberately NOT a rename-the-files workflow either: harvest_support_icons.py
hard-deletes and rebuilds every top-level PNG on every run (see its own
"Clear PNGs" comment), so a manual identification only survives if it's
recorded in manual_labels.json, which the harvester reads back in and
never overwrites itself. Renaming files directly looks like it works
but is silently wiped by the next harvest run -- this page's whole
point is to produce manual_labels.json entries, not renamed files.

The whole DOM is built client-side from one embedded JSON blob (see
CLUSTERS below) rather than Python string-formatting individual
<button onclick="..."> tags -- an earlier version did the latter and
broke silently: json.dumps()'s double-quoted string collided with the
onclick="..." attribute's own double quotes, truncating the attribute
and leaving every button non-functional. Embedding one JSON blob inside
a <script> tag and building elements via textContent/addEventListener
in JS sidesteps that whole class of escaping bug.

Candidate buttons show the EXACT tier-specific literal supports.json
name (e.g. "Lesser Increased Area of Effect"), not the bare family
display name manifest.json's own `candidates` field carries (e.g.
"Increased Area of Effect") -- those two differ whenever a family's
tiers use Lesser/Greater prefixes, and manual_labels.json needs the
exact literal key. A tier selector lets the user set or correct the
tier per crop (pre-filled from tier_notes.json's reading when one
exists, but overridable) since candidate buttons are computed fresh
per selected tier, not fixed at generation time.

Usage:
    python generate_manual_labeling_page.py

Reads: assets/harvested_supports/manifest.json, definitions/supports.json
Writes: assets/harvested_supports/manual_review.html (open it directly
    in a browser -- image paths are relative to that folder, so it
    won't render correctly if moved elsewhere)
"""
import json
import os
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TOOLS_DIR)
sys.path.insert(0, TOOLS_DIR)
import harvest_support_icons as harvester

HARVESTED_DIR = os.path.join(PROJECT_ROOT, "assets", "harvested_supports")
MANIFEST_PATH = os.path.join(HARVESTED_DIR, "manifest.json")
OUTPUT_PATH = os.path.join(HARVESTED_DIR, "manual_review.html")

TIERS = ("I", "II", "III")


def build_options(cluster, skills, supports, identity, identity_members):
    """{tier_roman: [{"identity": ..., "name": <exact literal name>}, ...]},
    restricted to members whose OWN icon is actually structurally possible
    for this crop -- not every member of a candidate identity regardless
    of icon.

    Real bug an earlier version had: it walked every member of each
    candidate identity and grouped by tier alone, with no icon check at
    all. Some identities span more than one, completely unrelated icon
    (e.g. "Duration" covers both `increasedduration` ["More Duration"]
    and `reduceduration` ["Less Duration"] -- opposite effects that
    happen to share a family label), so that offered candidates whose
    icon couldn't possibly match the crop on screen. Fixed by
    recomputing `common_icons` the same way harvest_support_icons.py's
    own inference does (icon_identity_map_for_skill, intersected across
    every contributing skill) and only including a member if its icon is
    in that set.
    """
    icon_maps = [
        harvester.icon_identity_map_for_skill(skill, skills, supports, identity)
        for skill in cluster["contributing_skills"]
    ]
    icon_maps = [m for m in icon_maps if m is not None]
    common_icons = set.intersection(*[set(m.keys()) for m in icon_maps]) if icon_maps else set()

    candidate_identities = {cand["identity"] for cand in cluster["candidates"]}
    options = {t: [] for t in TIERS}
    for t in TIERS:
        tier_identities = set()
        for m in icon_maps:
            tier_identities |= harvester.identities_for_icons(m, common_icons, tier=t)
        tier_identities &= candidate_identities
        for ident in tier_identities:
            for member_name in identity_members.get(ident, []):
                entry = supports.get(member_name)
                if entry is None or entry["tier_roman"] != t or entry["icon"] not in common_icons:
                    continue
                options[t].append({"identity": ident, "name": member_name})
    return options


def generate():
    with open(harvester.SUPPORTS_PATH, encoding="utf-8") as f:
        supports = json.load(f)["supports"]
    with open(harvester.SUPPORTS_BY_SKILLS_PATH, encoding="utf-8") as f:
        skills = json.load(f)["skills"]
    identity = harvester.build_identity_resolver(supports)
    identity_members = harvester.build_identity_members(supports, identity)

    with open(MANIFEST_PATH, encoding="utf-8") as f:
        manifest = json.load(f)

    clusters = [
        c for c in manifest["clusters"]
        if c["source"] == "inferred" and c["label"] is None and c["file"] and c["candidates"]
    ]
    clusters.sort(key=lambda c: (len(c["candidates"]), c["hash"]))

    payload = []
    for c in clusters:
        hash_key = c["file"].removeprefix("unlabeled_").removesuffix(".png")
        options = build_options(c, skills, supports, identity, identity_members)
        payload.append({
            "hash": hash_key,
            "file": c["file"],
            "current_tier": c["tier"],
            "occurrence_count": c["occurrence_count"],
            "skills": c["contributing_skills"],
            "options": options,
        })

    html = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Support manual-labeling review</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #1b1b1b; color: #eee; margin: 0; padding: 24px; }}
  h1 {{ font-size: 20px; }}
  p.instructions {{ max-width: 700px; line-height: 1.5; color: #ccc; }}
  #output-wrap {{ position: sticky; top: 0; background: #111; padding: 12px; border: 1px solid #444;
                  margin-bottom: 20px; z-index: 10; }}
  #output {{ width: 100%; height: 140px; background: #000; color: #7fd; font-family: monospace;
             font-size: 12px; box-sizing: border-box; }}
  #count {{ font-weight: bold; }}
  .grid {{ display: flex; flex-wrap: wrap; gap: 16px; }}
  .card {{ background: #262626; border: 1px solid #3a3a3a; border-radius: 6px; padding: 10px;
           width: 280px; }}
  .card.done {{ opacity: 0.35; }}
  .card img {{ width: 128px; height: 128px; image-rendering: pixelated; display: block; margin: 0 auto 8px; }}
  .hash {{ font-size: 11px; color: #999; margin-bottom: 4px; }}
  .skills {{ font-size: 11px; color: #bbb; margin-bottom: 8px; }}
  .tier-row {{ display: flex; gap: 4px; margin-bottom: 8px; }}
  .tier-row button {{ flex: 1; padding: 4px; background: #333; color: #eee; border: 1px solid #555;
                       border-radius: 4px; cursor: pointer; font-size: 12px; }}
  .tier-row button.active {{ background: #2a6; border-color: #2a6; font-weight: bold; }}
  .candidates button {{ display: block; width: 100%; margin: 2px 0; padding: 5px; font-size: 12px;
                         text-align: left; background: #333; color: #eee; border: 1px solid #555;
                         border-radius: 4px; cursor: pointer; }}
  .candidates button:hover {{ background: #457; }}
  .candidates .empty {{ font-size: 12px; color: #888; font-style: italic; }}
  .freetext input {{ width: 100%; margin-top: 6px; box-sizing: border-box; padding: 4px;
                      background: #111; color: #eee; border: 1px solid #555; }}
  .picked {{ margin-top: 6px; font-size: 12px; color: #7d7; }}
  .tier-warning {{ margin-top: 4px; font-size: 11px; color: #e94; }}
  button.copy {{ margin-top: 8px; padding: 6px 12px; }}
</style>
</head>
<body>
<h1>Support manual-labeling review</h1>
<p class="instructions">
  Pick (or correct) the tier first, then click the correct support --
  candidate buttons are the exact literal <code>definitions/supports.json</code>
  name for whichever tier is selected, since that's what
  <code>manual_labels.json</code> needs (not just the family name).
  If none of the buttons match, type the exact name instead. Nothing is
  written to disk from here: copy the JSON box below into
  <code>assets/harvested_supports/manual_labels.json</code> (merge with
  whatever's already there, don't overwrite), then re-run
  <code>harvest_support_icons.py</code>. If a pick's tier differs from
  the tier already on the card, that crop's <code>tier_notes.json</code>
  entry (if any) needs correcting too, or the manual label will be
  silently rejected as inconsistent -- flagged inline when it applies.
</p>
<div id="output-wrap">
  <div><span id="count">0</span> identified so far</div>
  <textarea id="output" readonly></textarea>
  <button class="copy" onclick="copyOutput()">Copy to clipboard</button>
</div>
<div class="grid" id="grid"></div>
<script>
  const CLUSTERS = {json.dumps(payload)};
  const results = {{}};
  const activeTier = {{}};

  function render() {{
    document.getElementById("count").textContent = Object.keys(results).length;
    document.getElementById("output").value = JSON.stringify(results, null, 2);
  }}

  function copyOutput() {{
    const el = document.getElementById("output");
    el.select();
    document.execCommand("copy");
  }}

  function pick(hash, name, chosenTier, currentTier) {{
    if (!name) return;
    results[hash] = name;
    const card = document.getElementById("card-" + hash);
    card.classList.add("done");
    const picked = card.querySelector(".picked");
    picked.textContent = "-> " + name;
    const warn = card.querySelector(".tier-warning");
    if (currentTier && chosenTier !== currentTier) {{
      warn.textContent = "Note: existing tier_notes.json reading for this hash is "
        + currentTier + ", not " + chosenTier + " -- that entry needs correcting too, "
        + "or this label will be silently rejected as inconsistent.";
    }} else {{
      warn.textContent = "";
    }}
    render();
  }}

  function renderCandidates(cluster, tier) {{
    const container = document.getElementById("cands-" + cluster.hash);
    container.innerHTML = "";
    const opts = cluster.options[tier] || [];
    if (opts.length === 0) {{
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "No known support for these candidates at tier " + tier + ".";
      container.appendChild(empty);
      return;
    }}
    for (const opt of opts) {{
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = opt.name;
      btn.addEventListener("click", () => pick(cluster.hash, opt.name, tier, cluster.current_tier));
      container.appendChild(btn);
    }}
  }}

  function selectTier(cluster, tier) {{
    activeTier[cluster.hash] = tier;
    const card = document.getElementById("card-" + cluster.hash);
    card.querySelectorAll(".tier-row button").forEach(b => {{
      b.classList.toggle("active", b.dataset.tier === tier);
    }});
    renderCandidates(cluster, tier);
  }}

  function buildCard(cluster) {{
    const card = document.createElement("div");
    card.className = "card";
    card.id = "card-" + cluster.hash;

    const img = document.createElement("img");
    img.src = cluster.file;
    img.alt = cluster.hash;
    card.appendChild(img);

    const hashLine = document.createElement("div");
    hashLine.className = "hash";
    hashLine.textContent = "unlabeled_" + cluster.hash + "  --  seen " + cluster.occurrence_count + "x"
      + (cluster.current_tier ? "  --  current tier_notes.json reading: " + cluster.current_tier : "  --  no tier resolved yet");
    card.appendChild(hashLine);

    const skillsLine = document.createElement("div");
    skillsLine.className = "skills";
    skillsLine.textContent = "Contributing skills: " + cluster.skills.join(", ");
    card.appendChild(skillsLine);

    const tierRow = document.createElement("div");
    tierRow.className = "tier-row";
    for (const t of ["I", "II", "III"]) {{
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = t;
      btn.dataset.tier = t;
      btn.addEventListener("click", () => selectTier(cluster, t));
      tierRow.appendChild(btn);
    }}
    card.appendChild(tierRow);

    const cands = document.createElement("div");
    cands.className = "candidates";
    cands.id = "cands-" + cluster.hash;
    card.appendChild(cands);

    const freetext = document.createElement("div");
    freetext.className = "freetext";
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "or type the exact supports.json name";
    input.addEventListener("keydown", (e) => {{
      if (e.key === "Enter") {{
        pick(cluster.hash, input.value, activeTier[cluster.hash] || cluster.current_tier, cluster.current_tier);
      }}
    }});
    freetext.appendChild(input);
    card.appendChild(freetext);

    const warn = document.createElement("div");
    warn.className = "tier-warning";
    card.appendChild(warn);

    const picked = document.createElement("div");
    picked.className = "picked";
    card.appendChild(picked);

    return card;
  }}

  const grid = document.getElementById("grid");
  if (CLUSTERS.length === 0) {{
    const done = document.createElement("div");
    done.style.color = "#7d7";
    done.textContent = "Nothing to review right now -- every crop from the last harvest run "
      + "resolved on its own (ground truth, manual_labels.json, or the embedding classifier).";
    grid.appendChild(done);
  }}
  for (const cluster of CLUSTERS) {{
    grid.appendChild(buildCard(cluster));
    const startTier = cluster.current_tier || "I";
    selectTier(cluster, startTier);
  }}
</script>
</body>
</html>
'''

    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print(f"Wrote {OUTPUT_PATH} ({len(payload)} crops to review)")
    print("Open it directly in a browser (image paths are relative to assets/harvested_supports/).")


if __name__ == "__main__":
    generate()
