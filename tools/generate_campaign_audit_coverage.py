"""
Tracks, for every currently-covered Tier I ("_i") (icon, tier) key in
assets/harvested_supports/manifest.json, whether ANY real occurrence of
it has ever been independently confirmed by a human through the
campaign shadow-ground-truth UI (tools/harvest_campaign_review.py's
review.html, recorded in assets/campaign_review/manual_truth.json, or
promoted into assets/harvested_supports/campaign_promotions.json) --
as opposed to a key whose only support is the classifier agreeing with
itself against structural candidates (source "embedding_confirmed") or
a real warrant.txt (source "ground_truth").

Why Tier I specifically: it's the tier a real warrant can least often
supply (see AI_RAMBLINGS.md's level-68 writeup) and the one the
production catalog leans hardest on inference/embedding self-
confirmation for -- exactly the blind spot the campaign audit pipeline
exists to probe. Checked directly once already (see AI_RAMBLINGS.md's
follow-up to the Mitigation Ignore investigation): of 95 Tier I
manifest entries traced to one historical archive alone
(__captures_campaign_20260917), only 26 had ever been independently
re-confirmed this way -- all in agreement, a genuinely reassuring
result, but worth tracking as an ongoing, regenerable number instead of
a one-off check.

A hash landing in manual_truth.json or campaign_promotions.json means a
human read the real name/tier off the in-game tooltip and confirmed it
independently of match_support_icon() -- the strongest available
signal short of a real warrant.txt. Agreement across everything audited
so far is a genuine, positive result for the harvest pipeline; a
disagreement here would be a serious, headline finding, not a footnote
-- printed as its own loud section, not buried in a table.

Two kinds of confirmation, both counted, but never conflated: an EXACT
hash match (a campaign crop is byte-identical to the catalog's own
reference -- the strongest possible proof, same screenshot) versus a
NAME match (a different real capture, independently confirmed as the
same exact support name, but not pixel-identical to the catalog's
reference -- still real, independent evidence, just one step weaker).
The second kind matters a lot in practice: real, reproduced capture
jitter (documented in AI_RAMBLINGS.md, up to ~37% of pixels differing
between two genuine captures of the identical real support -- almost
certainly animated shimmer/glow VFX, not a different icon) means the
exact-hash check alone silently misses real corroboration whenever a
freshly-captured crop of an already-covered identity doesn't happen to
hash-match the specific crop sitting in the catalog.

Usage:
    python generate_campaign_audit_coverage.py

Reads: assets/harvested_supports/manifest.json,
    assets/campaign_review/manual_truth.json,
    assets/harvested_supports/campaign_promotions.json
Writes: assets/campaign_audit_coverage.md
"""
import json
import os

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TOOLS_DIR)
MANIFEST_PATH = os.path.join(PROJECT_ROOT, "assets", "harvested_supports", "manifest.json")
TRUTH_PATH = os.path.join(PROJECT_ROOT, "assets", "campaign_review", "manual_truth.json")
PROMOTIONS_PATH = os.path.join(PROJECT_ROOT, "assets", "harvested_supports", "campaign_promotions.json")
OUTPUT_PATH = os.path.join(PROJECT_ROOT, "assets", "campaign_audit_coverage.md")


def _load_json(path, default):
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def generate():
    manifest = _load_json(MANIFEST_PATH, {"clusters": []})["clusters"]
    truth = _load_json(TRUTH_PATH, {})
    promotions = _load_json(PROMOTIONS_PATH, {})

    # Both are independent-of-the-classifier human confirmations -- a
    # promoted hash was removed from manual_truth.json (see
    # tools/promote_campaign_truth.py), so checking both is required to
    # not lose coverage of it here.
    audited_names = {}
    audited_names.update(truth)
    audited_names.update(promotions)

    # Reverse index for the NAME-match fallback (see module docstring):
    # every independently-confirmed hash, grouped by the name a human
    # gave it, regardless of which specific crop it was.
    audited_by_name: dict = {}
    for h, n in audited_names.items():
        audited_by_name.setdefault(n, []).append(h)

    by_key = {}
    for c in manifest:
        vk = c.get("visual_key")
        if not vk or not vk.endswith("_i"):
            continue
        by_key.setdefault(vk, []).append(c)

    rows = []  # (label, visual_key, sources, status, hash_hits, name_hits, is_ambiguous)
    for vk, entries in sorted(by_key.items()):
        sources = sorted({e["source"] for e in entries})
        # harvest_support_icons.py's own by_key merge pass already
        # guarantees every non-null label sharing one visual_key agrees
        # (a disagreement there gets reclassified as visual_key_ambiguous
        # before this script ever sees it) -- so an empty label set here
        # means genuinely ambiguous (no single name to check), not a gap
        # in this script's own logic.
        labels = {e["label"] for e in entries if e["label"] is not None}
        is_ambiguous = len(labels) == 0

        hashes = set(e["hash"] for e in entries)
        hash_hits = [(h, audited_names[h]) for h in hashes if h in audited_names]

        if is_ambiguous:
            candidate_names = sorted({
                c["name"] for e in entries if e["candidates"] for c in e["candidates"]
            })
            label = " / ".join(candidate_names) if candidate_names else vk
            name_hits = [
                (h, n) for n in candidate_names for h in audited_by_name.get(n, [])
                if h not in hashes
            ]
            all_hits = hash_hits + name_hits
            if all_hits:
                status = "audited" if any(n in candidate_names for _, n in all_hits) else "audited_disagree"
            else:
                status = "not_audited"
        else:
            label = next(iter(labels))
            name_hits = [(h, label) for h in audited_by_name.get(label, []) if h not in hashes]
            all_hits = hash_hits + name_hits
            if all_hits:
                status = "audited_disagree" if any(n != label for _, n in all_hits) else "audited"
            else:
                status = "not_audited"

        rows.append((label, vk, sources, status, hash_hits, name_hits, is_ambiguous))

    audited = [r for r in rows if r[3] == "audited"]
    disagree = [r for r in rows if r[3] == "audited_disagree"]
    not_audited = [r for r in rows if r[3] == "not_audited"]

    lines = []
    lines.append('# Campaign UI-audit coverage for Tier I ("Lesser") supports')
    lines.append("")
    lines.append("Regenerated by `tools/generate_campaign_audit_coverage.py`. Tracks, for")
    lines.append("every currently-covered Tier I (icon, tier) key, whether a human has ever")
    lines.append("independently confirmed it through the campaign shadow-ground-truth UI")
    lines.append("(`tools/harvest_campaign_review.py`'s `review.html`, recorded in")
    lines.append("`assets/campaign_review/manual_truth.json` or promoted into")
    lines.append("`assets/harvested_supports/campaign_promotions.json`) -- as opposed to a")
    lines.append("key whose only support is the classifier agreeing with itself")
    lines.append("(`embedding_confirmed`) or a real `warrant.txt` (`ground_truth`). See")
    lines.append("`tools/audit_campaign_truth.py` for the crop-level version of this same")
    lines.append("check, and `AI_RAMBLINGS.md` for how this gap was first found and sized.")
    lines.append("")
    lines.append("```")
    lines.append(f"{len(rows)} Tier I keys currently covered")
    lines.append(f"{len(audited):>3} independently audited via the campaign UI, all agree")
    lines.append(f"{len(disagree):>3} independently audited via the campaign UI, DISAGREE (see below)")
    lines.append(f"{len(not_audited):>3} not yet audited -- ground_truth/embedding_confirmed only")
    lines.append("```")
    lines.append("")

    if disagree:
        lines.append("## DISAGREEMENTS -- investigate before trusting either source")
        lines.append("")
        for label, vk, sources, _status, hash_hits, name_hits, _amb in disagree:
            lines.append(f"- **{label}** (`{vk}`, catalog source(s): {', '.join(sources)}):")
            for h, n in hash_hits + name_hits:
                lines.append(f"    - campaign UI says {n!r} for hash `{h[:10]}`")
        lines.append("")

    lines.append("## Independently audited, all agree")
    lines.append("")
    lines.append("A human read these off the real in-game tooltip during a campaign")
    lines.append("encounter and confirmed the same identity the catalog already had.")
    lines.append("\"Confirmations\" splits pixel-identical proof (same exact crop as the")
    lines.append("catalog's own reference) from same-identity-different-capture proof (a")
    lines.append("fresh roll of the same real support that simply didn't hash-match, most")
    lines.append("often real capture-jitter/VFX shimmer -- see this script's own module")
    lines.append("docstring) -- both are real independent evidence, just not the same")
    lines.append("strength.")
    lines.append("")
    lines.append("| Support | Visual key | Catalog source(s) | Confirmations |")
    lines.append("|---|---|---|---|")
    for label, vk, sources, _status, hash_hits, name_hits, amb in sorted(audited, key=lambda r: r[1]):
        tag = " (ambiguous icon+tier)" if amb else ""
        parts = []
        if hash_hits:
            parts.append(f"{len(hash_hits)} pixel-identical")
        if name_hits:
            parts.append(f"{len(name_hits)} different capture, same name")
        lines.append(f"| {label}{tag} | `{vk}` | {', '.join(sources)} | {'; '.join(parts)} |")
    lines.append("")

    lines.append("## Not yet audited")
    lines.append("")
    lines.append("Currently trusted only via a real `warrant.txt` (`ground_truth`) or the")
    lines.append("classifier agreeing with itself against structural candidates")
    lines.append("(`embedding_confirmed`) -- no human has independently confirmed these")
    lines.append("through the campaign UI yet, by exact crop OR by name. Not a known")
    lines.append("problem (embedding_confirmed's own validated accuracy is 100% across")
    lines.append("2094 crops), just unaudited -- closes naturally as more campaign")
    lines.append("encounters happen to re-roll these icons.")
    lines.append("")
    lines.append("| Support | Visual key | Catalog source(s) |")
    lines.append("|---|---|---|")
    for label, vk, sources, _status, _hash_hits, _name_hits, amb in sorted(not_audited, key=lambda r: r[1]):
        tag = " (ambiguous icon+tier)" if amb else ""
        lines.append(f"| {label}{tag} | `{vk}` | {', '.join(sources)} |")
    lines.append("")

    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print(f"Wrote {OUTPUT_PATH}")
    print(f"{len(rows)} Tier I keys: {len(audited)} audited+agree, {len(disagree)} audited+DISAGREE, "
          f"{len(not_audited)} not yet audited")


if __name__ == "__main__":
    generate()
