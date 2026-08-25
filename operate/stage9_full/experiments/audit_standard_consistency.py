"""标准一致性审计：学生1 Canonical / 学生2 映射 / 知识库 chunks / Stage 9 画像四层对比。

运行：
    python -m experiments.audit_standard_consistency [--out DIR]

输出：
    - 控制台打印 === STANDARD CONSISTENCY AUDIT === 摘要；
    - 结果写入 results/audit_standard_consistency/<时间戳>_audit.json（默认）。

本脚本只读冻结数据；对每一个 mismatch 给出原因（格式/覆盖/口径），不修改任何数据。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from adapters import canonical_standard
from adapters import data_loader
from adapters import paths  # noqa: F401
from experiments import common
from src.common.run_log import new_run_dir  # noqa: E402


def _chunk_standards(knowledge_dir: Path) -> tuple[Counter, Counter]:
    chunk_std = Counter()
    chunk_sec = Counter()
    path = knowledge_dir / "chunks" / "regulation_chunks.jsonl"
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig", "replace")
    for line in text.splitlines():
        if not line.strip():
            continue
        chunk = json.loads(line)
        std = str(chunk.get("standard", "")).strip()
        sec = str(chunk.get("section", "")).strip()
        if std:
            chunk_std[std] += 1
        if sec:
            chunk_sec[sec] += 1
    return chunk_std, chunk_sec


def _family_covered(canonical: str, kb_standards: set[str]) -> bool:
    if canonical in kb_standards:
        return True
    # 与学生2 RAG 一致的前缀匹配（截断编号，如 1926.105 → 1926.1050 家族）
    return any(s.startswith(canonical) for s in kb_standards)


def _canonical_count(codes: list[str]) -> Counter:
    out = Counter()
    for raw in codes:
        c = canonical_standard.canonicalize(raw)
        if c:
            out[c] += 1
    return out


def _mapping_rows_by_canonical(mapping: canonical_standard.R1R9Mapping) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    for row in mapping.rows:
        code = (row.get("standard_code") or "").strip()
        token = code.split()[0] if code else ""
        c = canonical_standard.canonicalize(token)
        if not c:
            c = canonical_standard.canonicalize((row.get("standard_normalized") or "").strip())
        if c:
            out.setdefault(c, []).append(row)
    return out


def audit(config: dict[str, Any], data: dict[str, Path], out_dir: Path | None = None) -> dict[str, Any]:
    # 1) 学生1 Canonical 映射（权威版）与学生2包内映射（对照版）
    s1_mapping = canonical_standard.R1R9Mapping(data["r1r9_mapping"])
    s2_mapping = canonical_standard.R1R9Mapping(data["knowledge_dir"] / "standard_to_r1r9_mapping.csv")
    s1_by_canon = _mapping_rows_by_canonical(s1_mapping)
    s2_by_canon = _mapping_rows_by_canonical(s2_mapping)

    # 2) 知识库 chunks
    chunk_std, chunk_sec = _chunk_standards(data["knowledge_dir"])
    kb_standards = set(chunk_std)

    # 3) Stage 9 画像
    profiles = data_loader.load_profiles(data)
    profile_codes: list[str] = []
    for card in profiles.values():
        profile_codes.extend(card.get("historical_standard_codes") or [])
    profile_canon = _canonical_count(profile_codes)

    s1_canon_set = set(s1_by_canon)
    s2_canon_set = set(s2_by_canon)
    prof_set = set(profile_canon)

    # 4) 交集与差异
    profile_missing_kb = sorted(prof_set - kb_standards)
    mapping_missing_kb_exact = sorted(s1_canon_set - kb_standards)
    mapping_missing_kb_family = sorted(
        c for c in mapping_missing_kb_exact if not _family_covered(c, kb_standards)
    )
    kb_not_in_profile = sorted(kb_standards - prof_set)
    unknown_in_profile = sorted(
        c for c in prof_set
        if c not in s1_canon_set and not c.startswith(("1910.", "1926."))
    )
    federal_missing_from_kb = sorted(
        c for c in profile_missing_kb
        if c.startswith(("1910.", "1926."))
    )

    # R 类别差异：学生1 vs 学生2
    r_diff: list[dict[str, Any]] = []
    for c in sorted(s1_canon_set & s2_canon_set):
        r1 = sorted({str(r.get("r_category") or "").strip() for r in s1_by_canon[c]})
        r2 = sorted({str(r.get("r_category") or "").strip() for r in s2_by_canon[c]})
        if r1 != r2:
            r_diff.append({"canonical": c, "student1_r": r1, "student2_r": r2})

    # 映射键歧义（同一 Canonical 对应多个不同 R 类别）
    ambiguous: list[dict[str, Any]] = []
    for c, rows in s1_by_canon.items():
        cats = sorted({str(r.get("r_category") or "").strip() for r in rows})
        if len(cats) > 1:
            ambiguous.append({"canonical": c, "r_categories": cats, "n_rows": len(rows)})

    report: dict[str, Any] = {
        "config": {
            "student1_mapping": str(data["r1r9_mapping"]),
            "student2_mapping": str(data["knowledge_dir"] / "standard_to_r1r9_mapping.csv"),
            "knowledge_chunks": str(data["knowledge_dir"] / "chunks" / "regulation_chunks.jsonl"),
            "profiles": str(data["profile_supplement"]),
        },
        "counts": {
            "student1_mapping_rows": s1_mapping.n_rows,
            "student1_canonical_standards": len(s1_canon_set),
            "student2_mapping_rows": s2_mapping.n_rows,
            "student2_canonical_standards": len(s2_canon_set),
            "knowledge_chunks": sum(chunk_std.values()),
            "knowledge_standards": len(kb_standards),
            "profile_standard_tokens": len(profile_codes),
            "profile_canonical_standards": len(prof_set),
            "profile_federal_standards": len([c for c in prof_set if c.startswith(("1910.", "1926."))]),
            "profile_nonfederal_standards": len([c for c in prof_set if not c.startswith(("1910.", "1926."))]),
        },
        "intersections": {
            "profile_intersect_knowledge": len(prof_set & kb_standards),
            "profile_intersect_student1_mapping": len(prof_set & s1_canon_set),
            "mapping_intersect_knowledge": len(s1_canon_set & kb_standards),
        },
        "profile_standards_missing_from_knowledge": [
            {"standard": c, "count": profile_canon[c], "reason": _missing_reason(c, s1_by_canon)}
            for c in profile_missing_kb
        ],
        "profile_federal_missing_from_knowledge_count": len(federal_missing_from_kb),
        "profile_federal_missing_from_knowledge": federal_missing_from_kb,
        "mapping_standards_missing_from_knowledge_exact_count": len(mapping_missing_kb_exact),
        "mapping_standards_missing_from_knowledge_exact": mapping_missing_kb_exact,
        "mapping_standards_missing_from_knowledge_even_prefix_count": len(mapping_missing_kb_family),
        "mapping_standards_missing_from_knowledge_even_prefix": mapping_missing_kb_family,
        "knowledge_standards_not_in_profile": [
            {"standard": c, "count": chunk_std[c]} for c in kb_not_in_profile
        ],
        "unknown_profile_standards": [
            {"standard": c, "count": profile_canon[c]} for c in unknown_in_profile
        ],
        "r_category_diff_student1_vs_student2": r_diff,
        "ambiguous_mapping_keys": ambiguous,
        "special_checks": {
            "1926.651_chunks_standard": chunk_std.get("1926.651", 0),
            "1926.651_chunks_section": chunk_sec.get("1926.651", 0),
            "1926.0651_chunks_standard": chunk_std.get("1926.0651", 0),
            "1926.651_in_student1_mapping": len(s1_by_canon.get("1926.651", [])),
            "1926.651_in_student2_mapping": len(s2_by_canon.get("1926.651", [])),
            "1926.651_profile_count": int(profile_canon.get("1926.651", 0)),
        },
    }

    if out_dir is not None:
        run_dir = new_run_dir(out_dir, "audit_standard_consistency", config)
        (run_dir / "audit.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        report["output_dir"] = str(run_dir)
    return report


def _missing_reason(canonical: str, s1_by_canon: dict[str, list[dict[str, str]]]) -> str:
    if canonical.startswith(("1910.", "1926.")):
        if canonical in s1_by_canon:
            return "学生1映射已覆盖该标准，但学生2法规正文知识库无该标准片段（知识库覆盖范围限制）"
        return "联邦标准，但学生1映射表与学生2知识库均未覆盖（数据缺口）"
    return "非1910/1926编号（州法规/其他体系），不在学生2知识库覆盖范围（按设计不检索）"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="标准一致性审计")
    parser.add_argument("--out", default=None, help="输出目录（默认 results/audit_standard_consistency）")
    args = parser.parse_args(argv)

    config, data, stage_config = common.setup()
    out_dir = (
        Path(args.out)
        if args.out
        else Path(stage_config["paths"]["runs"]) / "audit_standard_consistency"
    )
    report = audit(config, data, out_dir)

    c = report["counts"]
    i = report["intersections"]
    print("=== STANDARD CONSISTENCY AUDIT ===")
    print(f"Canonical standards (student1 mapping): {c['student1_canonical_standards']}")
    print(f"Profile standards: {c['profile_canonical_standards']} "
          f"(federal {c['profile_federal_standards']}, non-federal {c['profile_nonfederal_standards']})")
    print(f"Student2 mapping standards: {c['student2_canonical_standards']}")
    print(f"Knowledge chunk standards: {c['knowledge_standards']} (chunks {c['knowledge_chunks']})")
    print(f"Profile ∩ Knowledge: {i['profile_intersect_knowledge']}")
    print(f"Profile standards missing from knowledge: "
          f"{len(report['profile_standards_missing_from_knowledge'])} "
          f"(federal {report['profile_federal_missing_from_knowledge_count']})")
    print(f"Mapping standards missing from knowledge (exact): "
          f"{report['mapping_standards_missing_from_knowledge_exact_count']} "
          f"(even prefix: {report['mapping_standards_missing_from_knowledge_even_prefix_count']})")
    print(f"Unknown profile standards (non-federal, unmapped): "
          f"{len(report['unknown_profile_standards'])}")
    print(f"R category diff (student1 vs student2 mapping): {len(report['r_category_diff_student1_vs_student2'])}")
    print(f"Ambiguous mapping keys: {len(report['ambiguous_mapping_keys'])}")
    print("1926.651 special check:", json.dumps(report["special_checks"], ensure_ascii=False))
    print(f"\n完整报告：{report.get('output_dir')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
