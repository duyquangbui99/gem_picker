"""Shared 0-100 normalization helpers used by both stock and crypto scoring."""


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def min_max_score(value: float | None, lo: float, hi: float) -> float | None:
    """Linear 0-100 scale; None passes through so callers can distinguish
    "missing data" (neutral, excluded from the weighted average) from a
    genuine low score of 0."""
    if value is None:
        return None
    if hi == lo:
        return 50.0
    return clamp(100 * (value - lo) / (hi - lo))


def momentum_score(pct_change: float | None, sweet_spot: tuple[float, float] = (5, 40), max_abs: float = 150) -> float | None:
    """Peaks in a 'sweet spot' of positive-but-not-crazy momentum and
    penalizes both stagnation/decline and blow-off-top overextension —
    avoids the screener chasing tops."""
    if pct_change is None:
        return None
    lo, hi = sweet_spot
    if pct_change < 0:
        return clamp(50 + pct_change)  # negative momentum drags below neutral
    if pct_change <= lo:
        return clamp(50 + (pct_change / lo) * 30)  # ramps 50 -> 80
    if pct_change <= hi:
        return clamp(80 + ((pct_change - lo) / (hi - lo)) * 20)  # 80 -> 100
    # beyond the sweet spot: decay back down as it gets more overextended
    overextension = min(pct_change - hi, max_abs - hi)
    return clamp(100 - (overextension / (max_abs - hi)) * 60)


def weighted_composite(scores: dict[str, float | None], weights: dict[str, float]) -> tuple[float, dict[str, float]]:
    """Weighted average over only the signals that have data, with weights
    renormalized across the available subset (missing data shouldn't silently
    drag a candidate's score down just because a free API had a gap)."""
    available = {k: v for k, v in scores.items() if v is not None}
    if not available:
        return 0.0, {}

    total_weight = sum(weights[k] for k in available)
    composite = sum(available[k] * weights[k] for k in available) / total_weight
    return round(composite, 2), {k: round(v, 2) for k, v in available.items()}
