"""Which Qwen to browse with.

Pure data and stdlib only, so the terminal wizard can import it before the venv
exists. `needsGb` is video memory for the weights plus a working context, with a
little headroom -- not the download size.
"""

from __future__ import annotations

TIERS = [
    {
        "model": "qwen3:1.7b",
        "download": "~1.4 GB",
        "needsGb": 0,
        "note": "runs on anything, CPU included; loosest grip on a layout",
    },
    {
        "model": "qwen3:4b",
        "download": "~2.6 GB",
        "needsGb": 4,
        "note": "about twice the speed of 8b, and still holds a page together",
    },
    {
        "model": "qwen3:8b",
        "download": "~5 GB",
        "needsGb": 7,
        "note": "better writing and better games; the usual choice",
    },
    {
        "model": "qwen3:14b",
        "download": "~9 GB",
        "needsGb": 14,
        "note": "best pages, slow enough that browsing feels like waiting",
    },
]


def fits(vram_gb: float) -> list[dict]:
    """Everything this machine can hold, smallest first. Never empty."""
    usable = [tier for tier in TIERS if vram_gb >= tier["needsGb"]]
    return usable or [TIERS[0]]


def recommend(vram_gb: float) -> dict:
    """The largest that fits.

    Speed is a real concern for a browser, but that belongs to the Effort
    setting rather than to a model two sizes below what the card can hold.
    """
    return fits(vram_gb)[-1]
