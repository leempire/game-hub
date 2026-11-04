"""Shared per-bot skill tiers; invalid and legacy values use the normal tier."""

DIFFICULTIES = ("easy", "normal", "hard")
DEFAULT_DIFFICULTY = "normal"


def normalize_difficulty(value) -> str:
    return value if isinstance(value, str) and value in DIFFICULTIES else DEFAULT_DIFFICULTY
