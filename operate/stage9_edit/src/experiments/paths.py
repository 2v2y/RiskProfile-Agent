"""统一路径与 stage1 复用上下文。

stage9_edit 与 stage1 位于同一个 Git 仓库的 operate/ 下，
因此这里把 stage1 加入 sys.path，复用四个 Agent 与 Orchestrator。
"""

from __future__ import annotations

import sys
from pathlib import Path

STAGE9_ROOT = Path(__file__).resolve().parents[2]
OPERATE_ROOT = STAGE9_ROOT.parent
STAGE1_ROOT = OPERATE_ROOT / "stage1"


def add_stage1_path() -> Path:
    if str(STAGE1_ROOT) not in sys.path:
        sys.path.insert(0, str(STAGE1_ROOT))
    return STAGE1_ROOT
