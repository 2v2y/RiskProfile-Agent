"""配对 Bootstrap 置信区间（比较两个方法在同一批样本上的指标差异）。"""

from __future__ import annotations

import random
from typing import Any


def paired_bootstrap(
    rows_a: list[dict[str, Any]],
    rows_b: list[dict[str, Any]],
    metric: str,
    n_resamples: int = 2000,
    confidence: float = 0.95,
    seed: int = 0,
) -> dict[str, Any]:
    key = lambda rows: {r["sample_id"]: float(r[metric]) for r in rows if metric in r}
    a = key(rows_a)
    b = key(rows_b)
    common = sorted(set(a) & set(b))
    if not common:
        return {"metric": metric, "n_pairs": 0, "reason": "无共同样本，无法配对比较"}
    diffs = [a[s] - b[s] for s in common]
    mean_diff = sum(diffs) / len(diffs)
    rng = random.Random(seed)
    boot: list[float] = []
    for _ in range(n_resamples):
        sample = [rng.choice(diffs) for _ in range(len(diffs))]
        boot.append(sum(sample) / len(sample))
    boot.sort()
    alpha = (1.0 - confidence) / 2.0
    lo = boot[int(alpha * len(boot))]
    hi = boot[int((1 - alpha) * len(boot)) - 1]
    return {
        "metric": metric,
        "n_pairs": len(common),
        "mean_difference": round(mean_diff, 6),
        "confidence": confidence,
        "ci_lower": round(lo, 6),
        "ci_upper": round(hi, 6),
    }
