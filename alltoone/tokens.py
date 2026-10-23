"""Rough token estimation.

We do not ship a tokenizer (that would mean a dependency and a big vocabulary
file). A character-based heuristic is accurate enough to decide whether a
context file fits into a model window, which is all this tool needs.
"""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """Estimate how many LLM tokens ``text`` will consume.

    Latin text averages ~4 characters per token, scripts such as Cyrillic or CJK
    pack fewer characters per token, so they are weighted separately.
    """
    if not text:
        return 0
    ascii_chars = 0
    for char in text:
        if char < "\x80":
            ascii_chars += 1
    other_chars = len(text) - ascii_chars
    estimate = ascii_chars / 4.0 + other_chars / 1.8
    return max(1, int(estimate + 0.5))


def human_tokens(count: int) -> str:
    """Format a token count the way people read it: 8.4k, 1.2M."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 10_000:
        return f"{count / 1000:.0f}k"
    if count >= 1_000:
        return f"{count / 1000:.1f}k"
    return str(count)


def human_bytes(count: int) -> str:
    size = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
