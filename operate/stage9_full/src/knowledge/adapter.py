"""学生2 知识库 -> 统一 evidence 格式的适配器。

处理内容：
1. 中文表头 -> 英文 schema 字段（document_id / standard_number / source_type 等）
2. 法规正文片段补齐 document_id、来源、获取日期、是否归档等元数据（从 inventory join）
3. 来源类型中文 -> 英文枚举；是否为历史资料 -> 布尔
4. 从风险分类表把 standard -> R1—R9 拼回片段（供按风险类别检索）
5. 提供 DOL 格式 standard -> 知识库格式的转换函数
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any


SOURCE_TYPE_MAP = {
    "法规正文": "regulation",
    "解释资料": "interpretation",
    "历史资料": "archive",
    "数据定义": "data_definition",
    "现场工作手册": "field_manual",
}

RISK_CATEGORY_NAMES = {
    "R1": "电气危险与带电防护",
    "R2": "个人防护装备",
    "R3": "能量隔离与上锁挂牌",
    "R4": "高处作业与坠落",
    "R5": "培训、许可和程序执行",
    "R6": "机械设备和工器具",
    "R7": "受限空间、消防和危险环境",
    "R8": "现场通道、物体打击和综合防护",
    "R9": "其他或尚未分类",
}


def convert_standard(raw: str) -> str | None:
    """把 DOL API 格式转换成知识库格式（学生2 提供）。

    '19100120 Q01' -> '1910.120'
    '19260020 B02' -> '1926.20'
    """
    if not raw:
        return None
    raw = raw.strip()
    parts = raw.split()
    if not parts:
        return None
    code = parts[0]
    if not code.isdigit() or len(code) < 7:
        return None
    part = code[:4]
    section_num = int(code[4:])
    if part not in ("1910", "1926"):
        return None
    return f"{part}.{section_num}"


def normalize_dotted(code: str) -> str:
    """把 '1910.0269' 规范化为 '1910.269'（小节去前导零）。"""
    code = code.strip()
    m = re.fullmatch(r"(\d{4})\.(\d+)", code)
    if m:
        return f"{m.group(1)}.{int(m.group(2))}"
    return code


def _bool_cn(value: Any) -> bool:
    return str(value).strip().lower() in ("是", "true", "1", "y", "yes")


def _read_text(path: Path | str) -> str:
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", raw, 0, 1, f"无法识别编码：{path}")


def _csv_rows(path: Path | str):
    return csv.DictReader(io.StringIO(_read_text(path)))


def load_inventory(path: Path | str) -> dict[str, dict[str, Any]]:
    """读 document_inventory.csv，返回 standard -> 文档元数据（组合标准按每个部分建键）。"""
    out: dict[str, dict[str, Any]] = {}
    for row in _csv_rows(path):
        standard_raw = (row.get("OSHA标准编号") or "").strip()
        doc = {
                "document_id": (row.get("文档编号") or "").strip(),
                "standard_number": standard_raw,
                "title": (row.get("标题") or "").strip(),
                "source_type": SOURCE_TYPE_MAP.get(row.get("来源类型", ""), row.get("来源类型", "")),
                "source_url": (row.get("来源网址") or "").strip(),
                "ecfr_url": (row.get("eCFR链接") or "").strip(),
                "effective_date": (row.get("生效日期") or "").strip() or None,
                "retrieved_at": (row.get("获取日期") or "").strip(),
                "is_archived": _bool_cn(row.get("是否为历史资料", "")),
                "sha256": (row.get("SHA-256") or "").strip(),
                "file_name": (row.get("文件名") or "").strip(),
        }
        keys = [p.strip() for p in standard_raw.split(",") if p.strip()]
        for key in keys or [standard_raw]:
            out.setdefault(key, doc)
        # 现场手册/解释文件/数据定义等文档，chunk 的 standard 字段存的是文档编号
        out.setdefault(doc["document_id"], doc)
    return out


def load_risk_map(path: Path | str) -> dict[str, list[str]]:
    """读风险分类.csv，返回 standard规范值 -> [主类, 副类...]。"""
    out: dict[str, list[str]] = {}
    for row in _csv_rows(path):
        standard = normalize_dotted((row.get("standard规范值") or "").strip())
        if not standard:
            continue
        cats: list[str] = []
        for col in ("最终主类", "最终副类1", "最终副类2"):
            val = (row.get(col) or "").strip()
            if val and val != "NA" and val.startswith("R"):
                cats.append(val)
        out.setdefault(standard, cats)
    return out


def enrich_chunks(
    chunks_path: Path | str,
    inventory: dict[str, dict[str, Any]],
    risk_map: dict[str, list[str]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []

    def lookup_doc(standard: str) -> dict[str, Any] | None:
        if standard in inventory:
            return inventory[standard]
        if standard.startswith("interpretation_"):
            return inventory.get("OSHA-INTERP-" + standard[len("interpretation_"):])
        return None

    with open(chunks_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            chunk = json.loads(line)
            standard = normalize_dotted((chunk.get("standard") or "").strip())
            doc = lookup_doc(standard)
            section = str(chunk.get("section") or "")
            document_id = doc["document_id"] if doc else f"UNKNOWN-{standard}"
            enriched.append(
                {
                    "chunk_id": chunk.get("chunk_id", ""),
                    "evidence_id": f"regulation:{document_id}#{section}",
                    "document_id": document_id,
                    "standard_number": standard,
                    "section": section,
                    "title": doc["title"] if doc else "",
                    "text": chunk.get("text", ""),
                    "source_type": doc["source_type"] if doc else "regulation",
                    "source_url": doc["source_url"] if doc else "",
                    "effective_date": doc["effective_date"] if doc else None,
                    "retrieved_at": doc["retrieved_at"] if doc else "",
                    "is_archived": doc["is_archived"] if doc else False,
                    "risk_categories": risk_map.get(standard, []),
                }
            )
    return enriched


def build_mapping(enriched: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "standard_number": c["standard_number"],
            "document_id": c["document_id"],
            "section": c["section"],
            "chunk_id": c["chunk_id"],
        }
        for c in enriched
    ]


def build_gold(
    gold_path: Path | str,
    inventory: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, row in enumerate(_csv_rows(gold_path), start=1):
        standard = (row.get("正确标准编号") or "").strip()
        doc = inventory.get(standard, {})
        out.append(
                {
                    "query_id": f"gold_{index:04d}",
                    "query_text": (row.get("查询") or "").strip(),
                    "standard_numbers": [standard] if standard else [],
                    "gold_sections": [(row.get("正确条款") or "").strip()],
                    "gold_chunk_ids": [(row.get("正确片段编号") or "").strip()],
                    "gold_document_ids": [doc.get("document_id", "")] if doc else [],
                    "verified_by": (row.get("核对人") or "").strip(),
                }
        )
    return out


def write_jsonl(items: list[dict[str, Any]], path: Path | str) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    return out


def write_mapping_csv(rows: list[dict[str, str]], path: Path | str) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        writer = csv.writer(fh)
        writer.writerow(["standard_number", "document_id", "section", "chunk_id"])
        for r in rows:
            writer.writerow([r["standard_number"], r["document_id"], r["section"], r["chunk_id"]])
    return out


def run(
    src_dir: Path | str,
    out_dir: Path | str,
) -> dict[str, Any]:
    src = Path(src_dir)
    out = Path(out_dir)
    inventory = load_inventory(src / "document_inventory.csv")
    risk_map = load_risk_map(src / "风险分类.csv")
    enriched = enrich_chunks(src / "regulation_chunks.jsonl", inventory, risk_map)
    mapping = build_mapping(enriched)
    gold = build_gold(src / "retrieval_gold.csv", inventory)

    chunks_path = write_jsonl(enriched, out / "chunks" / "regulation_chunks.jsonl")
    mapping_path = write_mapping_csv(mapping, out / "chunks" / "standard_document_mapping.csv")
    gold_path = write_jsonl(gold, out / "chunks" / "retrieval_gold.jsonl")

    missing_meta = [c for c in enriched if not c["document_id"] or c["document_id"].startswith("UNKNOWN")]
    manifest = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "source": str(src),
        "n_documents": len(inventory),
        "n_chunks": len(enriched),
        "n_mapping": len(mapping),
        "n_gold": len(gold),
        "n_missing_metadata": len(missing_meta),
        "schema": "evidence_schema.json",
        "files": {
            "regulation_chunks": str(chunks_path),
            "standard_mapping": str(mapping_path),
            "retrieval_gold": str(gold_path),
        },
    }
    manifest_path = out / "knowledge_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
