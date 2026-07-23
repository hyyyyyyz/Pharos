"""Research-direction definitions for the daily arXiv sweep.

The category list and keyword map below are ported verbatim from the user's
``arxiv_ws`` project (``scripts/config.py``). They are hand-tuned coverage
accumulated over months of daily runs: a keyword that looks redundant is
usually there because a specific paper slipped through without it, and one that
looks over-broad is usually load-bearing for a niche subtopic. Treat this file
as data, not as code to be refactored — do not reorder, prune, or "improve" the
lists without the user asking, because order decides tie-breaks and every entry
changes what shows up in tomorrow's digest.
"""

from __future__ import annotations

import re

__all__ = [
    "ARXIV_CATEGORIES",
    "DIRECTIONS",
    "MAX_PAPERS_PER_DAY",
    "match_directions",
]

# Arxiv categories we sweep each day. Order doesn't matter; arxiv ORs them.
# - cs.RO: Robotics (VLA, embodied policies)
# - cs.CV: Computer Vision (VGGT, 3D reconstruction, foundation models)
# - cs.LG: Machine Learning (world models, latent dynamics)
# - cs.AI: AI generic (multi-agent, planning, foundation models for embodied)
# - cs.CL: Computation & Language (VLM backbones used by VLA)
# - cs.MM: Multimedia (multimodal benchmarks)
ARXIV_CATEGORIES: list[str] = ["cs.RO", "cs.CV", "cs.LG", "cs.AI", "cs.CL", "cs.MM"]

# Each direction is matched by a set of lowercase substring keywords. A paper
# is kept if ANY keyword for a direction appears in title OR abstract; the
# direction that matched (with the most distinct hits) becomes matched_domain.
#
# Tweak these lists when a recurring noisy paper sneaks in or when a niche
# subtopic needs coverage. Order = display preference on ties.
DIRECTIONS: dict[str, list[str]] = {
    "VLA": [
        "vision-language-action",
        "vision language action",
        "vla model",
        "vla policy",
        "robot policy",
        "embodied policy",
        "manipulation policy",
        "robotic manipulation",
        "openvla",
        "rt-2",
        "rt2",
        "pi-0",
        "pi0",
        "language-conditioned policy",
        "instruction-following manipulation",
    ],
    "World Model": [
        "world model",
        "world models",
        "neural simulator",
        "latent dynamics",
        "dynamics model",
        "video prediction",
        "video generation for robotics",
        "video world model",
        "genie",
        "navworld",
        "dreamerv3",
        "policy world model",
    ],
    "WAM": [
        "world action model",
        '"wam"',
        "action world model",
        "joint action prediction",
        "unified action model",
    ],
    "VGGT": [
        "vggt",
        "vggsfm",
        "dust3r",
        "mast3r",
        "feed-forward 3d",
        "feedforward 3d",
        "3d foundation model",
        "monocular 3d reconstruction",
        "novel view synthesis",
        "neural radiance",
        "gaussian splatting",
        "3d scene reconstruction",
        "geometry grounded",
        "visual geometry",
    ],
    "Agent": [
        "llm agent",
        "llm-based agent",
        "llm-powered agent",
        "embodied agent",
        "multi-agent",
        "multi agent",
        "agentic",
        "agent framework",
        "react agent",
        "reasoning and acting",
        "tool-use agent",
        "tool use agent",
        "gui agent",
        "web agent",
        "planning agent",
        "language agent",
        "foundation model agent",
        "agentic workflow",
        "autonomous agent",
        "agent benchmark",
    ],
    "Diffusion": [
        "diffusion policy",
        "diffusion model",
        "diffusion transformer",
        '"dit"',
        "denoising diffusion",
        "flow matching",
        "latent diffusion",
        "consistency model",
        "score-based",
        "score based generative",
        "rectified flow",
        "video diffusion",
        "stable diffusion",
        "diffusion-based",
        "diffusion based policy",
        "image diffusion",
        "guided diffusion",
        "classifier-free guidance",
    ],
    "Multi-modal": [
        "multimodal large language model",
        "multi-modal large language model",
        "mllm",
        "vision-language model",
        "vision language model",
        "vlm",
        "video-llm",
        "video llm",
        "audio-visual",
        "embodied chain-of-thought",
        "spatial reasoning",
        "embodied reasoning",
        "long-horizon planning",
    ],
}

# Default daily cap. If arxiv returns more matching papers than this, we keep
# the highest-recency cluster — the caller can override per sweep.
MAX_PAPERS_PER_DAY: int = 60

#: Declaration order of :data:`DIRECTIONS`, precomputed because it is consulted
#: once per candidate paper and doubles as the tie-break rank.
_DIRECTION_ORDER: dict[str, int] = {name: i for i, name in enumerate(DIRECTIONS)}


#: A keyword written as ``"wam"`` matches only as a whole word.
_QUOTED = re.compile(r'^"(.+)"$')


def keyword_matches(keyword: str, text: str) -> bool:
    """Does ``keyword`` occur in the already-lower-cased ``text``?

    Plain keywords match as substrings, which is what the original tuned config
    did and what most terms want: "diffusion policy" should hit inside a longer
    phrase.

    Short acronyms cannot use substring matching — "dit" would hit *credit*,
    *audit*, *edit*, *condition*. The original config solved that by padding the
    term with spaces (``" dit "``, ``"wam "``), which worked but has two
    problems. It is unreliable: ``"wam "`` matches "he **swam** across" while
    missing "WAM: world action model" and "the wam." because those are followed
    by punctuation rather than a space. And it is invisible — the padding cannot
    survive a round-trip through a text box, so the moment a user edited the
    direction their feed silently filled with false matches.

    So the intent is now written explicitly: quote it. ``"wam"`` matches the
    word *wam* regardless of the punctuation around it, and never *swam*. It is
    legible in the UI, survives editing, and is strictly more accurate than the
    padding it replaces. Legacy padded keywords still work as plain substrings,
    so nothing already stored breaks.
    """
    quoted = _QUOTED.match(keyword)
    if quoted is None:
        return keyword in text
    inner = quoted.group(1)
    # \b is wrong for terms with leading/trailing non-word characters (a keyword
    # like "c++" would never match), so the boundary is asserted by lookaround
    # on word characters only.
    pattern = rf"(?<!\w){re.escape(inner)}(?!\w)"
    return re.search(pattern, text) is not None


def direction_rank(direction: str | None) -> int:
    """Return the declaration index of ``direction``, or a sentinel past the end.

    Exposed so callers can present papers in the same priority order the user
    declared their interests in, instead of alphabetically.
    """
    if direction is None:
        return len(DIRECTIONS)
    return _DIRECTION_ORDER.get(direction, len(DIRECTIONS))


def match_directions(text: str) -> tuple[str | None, tuple[str, ...]]:
    """Classify ``text`` (title + abstract) into one research direction.

    A direction matches when ANY of its keywords occurs as a substring of the
    lowercased text. When several directions match, the one with the MOST
    DISTINCT keyword hits wins — a paper mentioning "diffusion policy" once
    while being about world models throughout should land under World Model.
    Ties break by declaration order in :data:`DIRECTIONS`, which is why that
    ordering is part of the configuration rather than incidental.

    Returns ``(None, ())`` when nothing matches, which is the caller's signal to
    drop the paper: the sweep is opt-in by topic, not a firehose.
    """
    text_l = text.lower()
    scored: list[tuple[str, list[str]]] = []
    for direction, keywords in DIRECTIONS.items():
        hits = [kw for kw in keywords if keyword_matches(kw, text_l)]
        if hits:
            scored.append((direction, hits))
    if not scored:
        return None, ()
    # Prefer the direction with the most hits; tie-break by DIRECTIONS order.
    scored.sort(key=lambda item: (-len(item[1]), _DIRECTION_ORDER[item[0]]))
    best_direction, best_hits = scored[0]
    return best_direction, tuple(best_hits)
