"""
Composites a raw gem sprite sheet extracted from Path of Exile's own game
files (via poe-dat-viewer -> baseitemtypes -> itemvisualidentity -> DDS ->
PNG, see project notes for the exact lookup steps) into a clean reference
icon. Output is NOT a validated production reference -- see assets/README.md
and AI_RAMBLINGS.md for why these composites (glow/sparkle-free, cross-domain
scoring issues) haven't replaced real screen-captured gem references -- so
it's written to assets/pending_gems/, the same holding area every other
not-yet-validated extracted asset lives in.

Usage:
    python compose_gem_reference.py <raw_sprite.png> <gem_slug>

Example:
    python compose_gem_reference.py crossbowtotemgem.png siege_ballista_of_trarthus
    -> writes assets/pending_gems/siege_ballista_of_trarthus.png

What this assumes about the raw sprite sheet (now checked against 3 real
extracted gems -- Siege Ballista/CrossBowTotemGem, Dark Bargain/
skeletalchains, Spectral Throw/ghostlythrow):

- The sheet contains exactly two non-transparent "islands" separated by
  fully transparent gaps: a gold symbol/overlay piece and a colored gem
  base, different shapes from the source art, not two views of the same
  thing. Confirmed across all 3: the LEFTMOST island is the overlay
  (goes on top); the RIGHTMOST is the base.
- They combine by centering the overlay on the base -- no other
  alignment offset was needed to closely match the real in-game
  rendering (visually compared against a real capture; see project
  history) for the one gem (Siege Ballista) this alignment choice was
  originally validated against.
- **Corrected assumption**: the base is NOT always the larger piece.
  Originally assumed (true for Siege Ballista, the only gem tested at
  the time) and silently wrong for both Dark Bargain and Spectral
  Throw -- their overlay is taller (and, for Spectral Throw, also
  wider) than their base. Sizing the output canvas to the base alone
  made the centering offset go negative in that dimension, which
  `Image.alpha_composite` doesn't error on -- it silently clips
  whatever falls outside the canvas. Real detail (parts of the gold
  filigree/symbol) was being lost off the edges with no warning,
  confirmed by re-compositing both gems and visually comparing before
  and after. Fixed: the canvas is now sized to whichever piece is
  larger in each dimension, with both pieces centered independently
  within it, so neither can be clipped regardless of which one is
  bigger.

**Not yet re-verified**: the other gem composites already sitting in
assets/pending_gems/ (Bladefall, Blast Rain, Chain Hook, Heavy Strike,
Spectral Helix, Spectral Shield Throw, Storm Call, Sunder, Wave of
Conviction) were built with the OLD, buggy version of this script and
their raw sprite sheets are no longer on hand to re-run -- they may have
the same clipping problem and there's currently no way to check without
re-extracting each one's raw sprite sheet first.

The output keeps the base's alpha channel (i.e. is still transparent
outside the gem's own silhouette), which is what makes it usable with
match_icon()'s alpha-masked comparison against a screenshot-cropped
candidate that has a completely different background (a dark UI border
and drop-shadow glow neither the raw sprite nor the mask need to match).
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image


def _find_islands(alpha: np.ndarray, min_width: int = 5, min_peak_alpha: int = 200):
    """Returns a list of (x0, x1) column ranges for each contiguous
    non-transparent region in the alpha channel.

    Filters out spurious slivers -- a real extracted sprite can contain
    a stray 1-2px-wide, barely-non-transparent column (compression
    artifact or antialiasing remnant at a sprite boundary) that isn't
    part of either real piece, but would otherwise be detected as its
    own "island" and could get selected as the overlay/base instead of
    the real one (observed directly: one real sprite had exactly this,
    a 1px sliver with alpha maxing at 16, sorted into the same list as
    the two real ~50px-wide, fully-opaque pieces)."""
    has_content = alpha.max(axis=0) > 10
    raw_islands = []
    in_island = False
    for i, v in enumerate(has_content):
        if v and not in_island:
            start = i
            in_island = True
        elif not v and in_island:
            raw_islands.append((start, i - 1))
            in_island = False
    if in_island:
        raw_islands.append((start, len(has_content) - 1))

    islands = []
    for x0, x1 in raw_islands:
        width = x1 - x0 + 1
        peak_alpha = alpha[:, x0:x1 + 1].max()
        if width >= min_width and peak_alpha >= min_peak_alpha:
            islands.append((x0, x1))
    return islands


def _tight_crop(img: Image.Image, x0: int, x1: int) -> Image.Image:
    """Crops to the given column range, then further tightens to the
    actual non-transparent bounding box within it (so the piece has no
    empty margin on any side, making later centering meaningful)."""
    alpha = np.array(img)[:, x0:x1 + 1, 3]
    rows = np.where(alpha.max(axis=1) > 10)[0]
    cols = np.where(alpha.max(axis=0) > 10)[0]
    return img.crop((x0 + cols.min(), rows.min(), x0 + cols.max() + 1, rows.max() + 1))


def compose_gem_reference(sprite_path: str, gem_slug: str, output_dir: str = "assets/pending_gems") -> str:
    sprite = Image.open(sprite_path).convert("RGBA")
    alpha = np.array(sprite)[:, :, 3]

    islands = _find_islands(alpha)
    if len(islands) != 2:
        raise ValueError(
            f"Expected exactly 2 content islands in {sprite_path}, found {len(islands)}: {islands}. "
            f"This sprite doesn't match the assumed two-piece (overlay + base) layout -- "
            f"inspect it manually rather than trusting this script's output."
        )

    overlay_range, base_range = sorted(islands, key=lambda r: r[0])
    overlay = _tight_crop(sprite, *overlay_range)
    base = _tight_crop(sprite, *base_range)

    # Canvas sized to whichever piece is larger in each dimension, not just
    # the base -- the base was assumed to always be the bigger piece (true
    # for the one gem this script was originally built against, Siege
    # Ballista), but confirmed FALSE for at least two real gems (Dark
    # Bargain/skeletalchains, Spectral Throw/ghostlythrow): both have an
    # overlay taller (and, for Spectral Throw, also wider) than their base.
    # Sizing the canvas to `base.copy()` alone made the centering offset go
    # negative in that dimension, which `Image.alpha_composite` doesn't
    # error on -- it silently clips whatever falls outside the canvas, so
    # real overlay pixels (the gold filigree/symbol) were being lost off
    # the top/bottom or left/right edges with no warning. Both pieces are
    # now centered independently within a canvas that fits the larger of
    # the two in each dimension, so neither can be clipped.
    width = max(base.width, overlay.width)
    height = max(base.height, overlay.height)
    composite = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    composite.alpha_composite(base, ((width - base.width) // 2, (height - base.height) // 2))
    composite.alpha_composite(overlay, ((width - overlay.width) // 2, (height - overlay.height) // 2))

    out_path = Path(output_dir) / f"{gem_slug}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    composite.save(out_path)
    return str(out_path)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    result = compose_gem_reference(sys.argv[1], sys.argv[2])
    print(f"Wrote {result}")
