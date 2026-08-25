"""Canonical Standard 层：Stage 9 全链路唯一的标准编号口径。

决议依据（见 docs/standard_consistency_analysis.md）：
- Canonical Standard = OSHA 官方引用格式，如 ``1926.651``、``1910.132``、``1926.1050``；
- 规则：part + "." + str(int(节号))，节号去前导零、保留全部有效位；
- 学生1映射表（standard_to_r1r9_mapping.csv）的 ``standard_normalized``（如 ``1926.0651``）
  是**映射查找键**，规则为 part + "." + (4位零填充节号).rstrip("0")，已在全部 3032 行
  1910/1926 数字码上验证 0 例外；该键有损，只用于查找，不作为 Canonical Standard。
- 非 1910/1926 编号（州法规等）不转换，原样保留并标记为非联邦标准。

本模块只做纯函数转换与映射加载/查找，不读写任何冻结数据。
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

FEDERAL_PARTS = ("1910", "1926")

# 1910/1926 编号：点号形式（1910.132 / 1926.0651 / 1926.651）或 DOL 原值形式（19100132 / 19260651 A）
_DOTTED_RE = re.compile(r"^(1910|1926)\.(\d+)$")
_DOL_RE = re.compile(r"^(1910|1926)(\d{4,})$")


def is_federal_standard(canonical: str) -> bool:
    """Canonical 是否属于 1910/1926 联邦标准体系。"""
    return bool(canonical and canonical.startswith(FEDERAL_PARTS) and _DOTTED_RE.match(canonical))


def canonicalize(raw: str | None) -> str | None:
    """把任意输入形式统一为 Canonical Standard（官方引用格式）。

    支持：
    - DOL 原值：``19260651 A``、``19260651`` → ``1926.651``
    - 映射键：``1926.0651``、``1910.0132`` → ``1926.651``、``1910.132``
    - 官方格式：``1926.651``、``1926.651(c)`` → ``1926.651``（保留后缀括号由调用方处理）
    - 非联邦编号：``16VAC25-60-130`` → 原样保留
    """
    raw = str(raw or "").strip()
    if not raw:
        return None
    token = raw.split()[0]
    m = _DOTTED_RE.match(token)
    if m:
        return f"{m.group(1)}.{int(m.group(2))}"
    m = _DOL_RE.match(token)
    if m:
        return f"{m.group(1)}.{int(m.group(2))}"
    return token


def mapping_key(canonical: str) -> str | None:
    """Canonical → 学生1映射表 standard_normalized 查找键。

    仅对联邦标准生效；非联邦标准直接返回原值（映射表对州法规键原样保存）。
    """
    canonical = str(canonical or "").strip()
    if not canonical:
        return None
    m = _DOTTED_RE.match(canonical)
    if not m:
        return canonical
    part, section = m.group(1), int(m.group(2))
    padded = str(section).zfill(4).rstrip("0")
    return f"{part}.{padded}"


def mapping_keys(canonical: str) -> list[str]:
    """返回候选映射键（含未 rstrip 的补零形式），供容错查找。"""
    primary = mapping_key(canonical)
    if not primary:
        return []
    m = _DOTTED_RE.match(canonical)
    if not m:
        return [primary]
    padded = str(int(m.group(2))).zfill(4)
    secondary = f"{m.group(1)}.{padded}"
    keys = [primary]
    if secondary not in keys:
        keys.append(secondary)
    return keys


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    """读取冻结 CSV（UTF-8/GB18030 兼容）。"""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return list(csv.DictReader(io.StringIO(raw.decode(enc))))
        except UnicodeDecodeError:
            continue
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))))


class R1R9Mapping:
    """加载并查询学生1 standard_to_r1r9_mapping.csv（只读）。

    R1–R9 权威版本按 Stage 9 决议取学生1最终版（SHA 76c0311f…，含 R6=179），
    与画像 risk_category_counts 的生成口径一致。
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.rows: list[dict[str, str]] = read_csv_rows(self.path)
        self.by_normalized: dict[str, list[dict[str, str]]] = {}
        self.by_code_token: dict[str, list[dict[str, str]]] = {}
        for row in self.rows:
            norm = (row.get("standard_normalized") or "").strip()
            self.by_normalized.setdefault(norm, []).append(row)
            code = (row.get("standard_code") or "").strip()
            token = code.split()[0] if code else ""
            if token:
                self.by_code_token.setdefault(token, []).append(row)

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    def lookup(self, canonical: str) -> dict[str, Any]:
        """按 Canonical 查询映射；返回 rows / status / keys_tried。

        status：
        - FOUND     唯一 Canonical 匹配（可能多行，均属同一标准不同子条款）
        - AMBIGUOUS 多个不同映射键对应同一 Canonical，且 R 类别冲突
        - MISSING   映射表中无该标准
        """
        canonical = str(canonical or "").strip()
        if not canonical:
            return {"canonical": canonical, "status": "MISSING", "rows": [], "keys_tried": []}
        keys = mapping_keys(canonical)
        rows: list[dict[str, str]] = []
        keys_tried: list[str] = []
        for key in keys:
            keys_tried.append(key)
            rows.extend(self.by_normalized.get(key, []))
        if not rows:
            # 兜底：按 standard_code 首 token 的 Canonical 化结果匹配
            for token, token_rows in self.by_code_token.items():
                if canonicalize(token) == canonical:
                    rows.extend(token_rows)
                    keys_tried.append(f"code:{token}")
        if not rows:
            return {"canonical": canonical, "status": "MISSING", "rows": [], "keys_tried": keys_tried}
        cats = sorted({str(r.get("r_category") or "").strip() for r in rows})
        if len(cats) > 1:
            return {
                "canonical": canonical,
                "status": "AMBIGUOUS",
                "rows": rows,
                "keys_tried": keys_tried,
                "r_categories": cats,
            }
        return {
            "canonical": canonical,
            "status": "FOUND",
            "rows": rows,
            "keys_tried": keys_tried,
            "r_category": cats[0] if cats else None,
            "r_category_name": str(rows[0].get("r_category_name") or "").strip(),
            "basis": str(rows[0].get("basis") or "").strip(),
        }

    def canonical_standard_set(self) -> set[str]:
        """映射表内全部标准 Canonical 化后的集合。

        优先用 standard_code 首 token（无损，如 19260960 → 1926.960）反推 Canonical；
        不使用 standard_normalized 反推（有损键，如 1926.096 实为 1926.960，会被误读为 1926.96）。
        """
        out: set[str] = set()
        for token in self.by_code_token:
            c = canonicalize(token)
            if c:
                out.add(c)
        return out
