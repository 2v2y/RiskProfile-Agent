"""用真实画像数据跑一次契约核对：适配器输出是否全部通过 Pydantic 校验。

运行：
    python -m src.profiles.contract_check <student3_profiles_csv> <whitelist_json>
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from src.common.pydantic_schemas import ProfileCard
from src.profiles.adapter import adapt_csv


def main() -> int:
    csv_path = sys.argv[1] if len(sys.argv) > 1 else None
    whitelist_path = sys.argv[2] if len(sys.argv) > 2 else None

    if csv_path is None:
        csv_path = (
            r"G:\Projects\intern_project\CahngeDirect_eassy\origin_data"
            r"\from_student3\阶段4学生3交付给学生4\profiles_train_val.csv"
        )
    if whitelist_path is None:
        whitelist_path = (
            r"G:\Projects\intern_project\CahngeDirect_eassy\operate\stage1"
            r"\configs\agent_profile_whitelist.json"
        )

    cards = adapt_csv(csv_path, whitelist_path)
    passed = 0
    failures: list[dict] = []
    for card in cards:
        try:
            ProfileCard.model_validate(card)
            passed += 1
        except ValidationError as exc:
            failures.append(
                {
                    "sample_id": card.get("sample_id"),
                    "quarter": card.get("quarter"),
                    "error": str(exc),
                }
            )

    report = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(cards),
        "passed": passed,
        "failed": len(failures),
        "pass_rate": round(passed / len(cards), 6) if cards else 0.0,
        "failures": failures[:50],
    }

    out_dir = Path(__file__).resolve().parents[2] / "docs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "contract_check_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"契约核对：总 {report['total']}，通过 {report['passed']}，失败 {report['failed']}")
    for item in failures[:20]:
        print("FAIL", item["sample_id"], item["quarter"], item["error"][:160])
    print(f"报告已写入：{out_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
