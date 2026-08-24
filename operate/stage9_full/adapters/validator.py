"""用阶段八 Pydantic ProfileCard 校验适配后的画像卡。"""

from __future__ import annotations

from typing import Any

from adapters import paths  # noqa: F401
from src.common.pydantic_schemas import ProfileCard  # noqa: E402


def validate_cards(cards: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    for key, card in cards.items():
        try:
            ProfileCard.model_validate(card)
        except Exception as exc:  # noqa: BLE001
            failures.append({"sample_id": key[0], "quarter": key[1], "error": str(exc)[:300]})
    return {
        "total": len(cards),
        "passed": len(cards) - len(failures),
        "failed": len(failures),
        "failures": failures[:20],
    }
