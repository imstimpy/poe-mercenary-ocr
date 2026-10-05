"""
Groups captures into distinct mercenaries so a rematch (the same mercenary
fought again via its warrant) is counted once, not as a new independent roll.

What identifies a mercenary (measured on this project's own corpus, see
AI_RAMBLINGS.md "Rematch identity"): the ROLL -- the full skill set plus the
supports on every skill -- with the name as a loose confirming check. Name
alone over-merges (58% of same-name pairs had different skills: different
mercenaries sharing a generated name); name+skills still collided once
(`Orvan, the Keitan Convert`); the supports on each skill carry nearly all
the identifying entropy. Name still has to match, loosely, because
mercenary type tells nothing and an identical roll can occur by chance
across mercenaries when supports weren't extracted (older batches).

Name matching is fuzzy on purpose: OCR noise produces different strings for
the same mercenary ("Death- dealer" / "Death-dealer", "Ivi, the Summoner" /
"... Aol"). Supports are compared compatibly: an unresolved "X or Y" icon
collision is compatible with X, Y, or another string containing either;
"Unknown" is compatible with anything.

Equipment-grid pixel similarity was tested as an extra signal and does not
separate rematches from different mercenaries (ranges overlap) -- not used.

Usage (from the project root):
    python tools/gather_unique_mercenaries.py                  # cluster + write logs/mercenary_identities.json (local only)
    python tools/gather_unique_mercenaries.py --stats Sniper   # deduplicated skill distribution for a type
"""
import argparse
import difflib
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Local, machine-specific bookkeeping derived from this machine's captures --
# not reproducible from the repo, so it lives in the gitignored logs/ folder,
# not assets/ (which holds committed reference data).
OUTPUT_PATH = os.path.join(PROJECT_ROOT, "logs", "mercenary_identities.json")
DEFINITIONS_PATH = os.path.join(PROJECT_ROOT, "definitions", "skills_by_mercenary.json")
CAPTURE_PATTERNS = ("captures/*", "__captures_*/*", "captures_campaign/*")

NAME_SIMILARITY_MIN = 0.85


def _blocks_from_lines(lines):
    seps = [i for i, l in enumerate(lines) if l.strip() == "--------"]
    segments, prev = [], 0
    for i in seps:
        segments.append(lines[prev:i])
        prev = i + 1
    segments.append(lines[prev:])
    blocks = []
    for seg in segments[3:-1]:
        seg = [l.strip() for l in seg if l.strip()]
        if seg:
            blocks.append((seg[0], list(seg[1:])))
    return blocks


def parse_warrant(text):
    """-> dict(name, build, level, blocks[(skill, [support strings])]) or None."""
    lines = text.splitlines()
    if len(lines) < 9 or "Mercenary Warrant" not in lines:
        return None
    start = lines.index("Mercenary Warrant")
    name = lines[start + 2].strip() if len(lines) > start + 2 else ""
    build = next((l[len("Build:"):].strip() for l in lines if l.startswith("Build:")), "")
    level = next((l.split(":", 1)[1].strip() for l in lines if l.startswith("Mercenary Level")), "")
    return {"name": name, "build": build, "level": level, "blocks": _blocks_from_lines(lines)}


def load_capture(capture_dir):
    """Prefers the real warrant.txt (ground truth) over warrant_generated.txt."""
    for fname, source in (("warrant.txt", "real"), ("warrant_generated.txt", "generated")):
        path = os.path.join(capture_dir, fname)
        if os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                parsed = parse_warrant(f.read())
            if parsed:
                parsed["source"] = source
                parsed["dir"] = capture_dir.replace("\\", "/")
                return parsed
    return None


def normalize_name(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def name_similarity(a, b):
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _options(support):
    """"A (Tier: 2) or B (Tier: 2)" -> {"A (Tier: 2)", "B (Tier: 2)"}"""
    return {p.strip() for p in support.split(" or ")}


def supports_compatible(a, b):
    if a == "Unknown" or b == "Unknown":
        return True
    return bool(_options(a) & _options(b))


def support_lists_compatible(a, b):
    """Order-insensitive: the live pipeline may reorder supports within a skill
    (position-agnostic collision resolution)."""
    if len(a) != len(b):
        return False
    unused = list(b)
    for s in a:
        for j, t in enumerate(unused):
            if supports_compatible(s, t):
                del unused[j]
                break
        else:
            return False
    return True


def has_supports(rec):
    return any(sups for _, sups in rec["blocks"])


def compare(a, b):
    """-> None if not the same mercenary, else 'high' or 'low' confidence.

    high: both captures have supports extracted, the same skills, and every
    skill's supports are compatible. low: supports missing on a side (older
    batches), or one capture is missing a single skill the other has -- a
    mercenary always has a fixed number of skills, so a one-skill subset is an
    OCR-dropped row, not a different mercenary (a swap -- X on one side, Y on
    the other -- IS a different mercenary and never merges). Skills are
    compared as sets: a duplicated OCR row doesn't count."""
    if a["build"] != b["build"]:
        return None
    if a["level"] and b["level"] and a["level"] != b["level"]:
        return None
    if name_similarity(a["name"], b["name"]) < NAME_SIMILARITY_MIN:
        return None
    sa = {s for s, _ in a["blocks"]}
    sb = {s for s, _ in b["blocks"]}
    dropped = False
    if sa != sb:
        if (sa < sb or sb < sa) and abs(len(sa) - len(sb)) == 1:
            dropped = True
        else:
            return None
    if not has_supports(a) or not has_supports(b):
        return "low"
    da = {s: sups for s, sups in a["blocks"]}
    db = {s: sups for s, sups in b["blocks"]}
    for skill in sa & sb:
        if not support_lists_compatible(da[skill], db[skill]):
            return None
    return "low" if dropped else "high"


def near_miss(a, b):
    """Same build and similar name, but not merged -- surfaced for review."""
    if a["build"] != b["build"] or name_similarity(a["name"], b["name"]) < NAME_SIMILARITY_MIN:
        return False
    sa = {s for s, _ in a["blocks"]}
    sb = {s for s, _ in b["blocks"]}
    return 0 < len(sa ^ sb) <= 2


class _UnionFind:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def load_all(root=PROJECT_ROOT):
    dirs = sorted({d for pat in CAPTURE_PATTERNS for d in glob.glob(os.path.join(root, pat)) if os.path.isdir(d)})
    records = []
    for d in dirs:
        rec = load_capture(d)
        if rec:
            rec["dir"] = os.path.relpath(d, root).replace("\\", "/")
            records.append(rec)
    return records


def cluster(records):
    """-> list of clusters (each a list of record indices), plus near-miss pairs."""
    uf = _UnionFind(len(records))
    low_pairs = set()
    by_build = defaultdict(list)
    for i, r in enumerate(records):
        by_build[r["build"]].append(i)
    misses = []
    for idxs in by_build.values():
        for x in range(len(idxs)):
            for y in range(x + 1, len(idxs)):
                i, j = idxs[x], idxs[y]
                result = compare(records[i], records[j])
                if result:
                    uf.union(i, j)
                    if result == "low":
                        low_pairs.add((i, j))
                elif near_miss(records[i], records[j]):
                    misses.append((i, j))
    groups = defaultdict(list)
    for i in range(len(records)):
        groups[uf.find(i)].append(i)
    return list(groups.values()), low_pairs, misses


def representative(records, members):
    """Real warrant.txt first, then earliest capture directory name."""
    return sorted(members, key=lambda i: (records[i]["source"] != "real", os.path.basename(records[i]["dir"])))[0]


def write_identities(records, groups, low_pairs, path=OUTPUT_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = {}
    for n, members in enumerate(sorted(groups, key=lambda m: os.path.basename(records[min(m)]["dir"])), 1):
        rep = representative(records, members)
        rec = records[rep]
        low = any((a, b) in low_pairs or (b, a) in low_pairs for a in members for b in members if a != b)
        out[f"m{n:04d}"] = {
            "name": rec["name"],
            "build": rec["build"],
            "representative": rec["dir"],
            "captures": sorted(records[i]["dir"] for i in members),
            "rematch_count": len(members) - 1,
            "low_confidence": low,
        }
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=2)
    return out


def skill_stats(records, groups, build_type):
    """Deduplicated secondary/utility distribution for one mercenary type."""
    with open(DEFINITIONS_PATH, encoding="utf-8") as f:
        defs = json.load(f)["mercenaries"]
    info = defs.get(build_type)
    if info is None:
        raise SystemExit(f"{build_type!r} is not a known mercenary type")
    wanted = {build_type, f"Infamous {build_type}"}
    reps = []
    total_captures = 0
    for members in groups:
        rep = representative(records, members)
        if records[rep]["build"] in wanted:
            reps.append(records[rep])
            total_captures += len(members)
    print(f"{build_type}: {total_captures} captures -> {len(reps)} distinct mercenaries "
          f"({total_captures - len(reps)} rematches ignored)")
    for bucket, count_key in (("Secondary", "SecondaryCount"), ("Utility", "UtilityCount")):
        pool = info[bucket]
        counts = Counter()
        for r in reps:
            for skill, _ in r["blocks"]:
                if skill in pool:
                    counts[skill] += 1
        fills = sum(counts.values())
        print(f"\n{bucket} ({info[count_key]} slot(s), {fills} fills across {len(reps)} mercenaries):")
        for skill in sorted(pool, key=lambda s: (-counts.get(s, 0), s)):
            share = counts.get(skill, 0) / fills * 100 if fills else 0.0
            print(f"  {skill:34s} {counts.get(skill, 0):3d}  {share:5.1f}%")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stats", metavar="TYPE", help="print deduplicated skill distribution for a mercenary type")
    args = ap.parse_args()

    records = load_all()
    groups, low_pairs, misses = cluster(records)
    if args.stats:
        skill_stats(records, groups, args.stats)
        return
    identities = write_identities(records, groups, low_pairs)
    rematches = sum(v["rematch_count"] for v in identities.values())
    print(f"{len(records)} captures -> {len(identities)} distinct mercenaries ({rematches} rematch captures merged)")
    print(f"low-confidence merges (supports missing on a side): {sum(1 for v in identities.values() if v['low_confidence'])}")
    print(f"near-misses (same build + similar name, skills differ by <=2, NOT merged): {len(misses)}")
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    sys.exit(main())
