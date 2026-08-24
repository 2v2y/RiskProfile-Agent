"""第五轮复核：学生2 交付_学生4（08-24 晚更新版）逐项验证。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(r"G:\Projects\intern_project\CahngeDirect_eassy")
D = ROOT / r"origin_data\from_student2\交付_学生4\交付_学生4"
OLD = ROOT / r"origin_data\from_student2\student2"
OUT: dict = {}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def read_auto(p: Path) -> str:
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in read_auto(p).splitlines() if l.strip()]


# 1) 新包文件哈希
files = [
    "交付清单.md",
    "学生2交付说明.md",
    "学生4检索程序接入指南.md",
    "knowledge/chunks/regulation_chunks.jsonl",
    "knowledge/document_inventory.csv",
    "knowledge/knowledge_manifest.json",
    "knowledge/retrieval_gold.csv",
    "knowledge/retrieval_validation_metrics.csv",
    "knowledge/standard_document_mapping.csv",
    "knowledge/standard_to_r1r9_mapping.csv",
    "knowledge/vector_db/chunk_ids.json",
    "knowledge/vector_db/db_meta.json",
    "knowledge/vector_db/embeddings.npy",
    "knowledge/vector_db/faiss_index.bin",
    "src/retrieval/rag_retriever.py",
]
OUT["hashes"] = {f: sha256(D / f) for f in files}

# 2) 新旧 chunks 对比
new_chunks = load_jsonl(D / "knowledge/chunks/regulation_chunks.jsonl")
old_chunks = load_jsonl(OLD / "regulation_chunks.jsonl")
OUT["chunks"] = {
    "new_n": len(new_chunks),
    "old_n": len(old_chunks),
    "chunk_id_order_same": [c["chunk_id"] for c in new_chunks] == [c["chunk_id"] for c in old_chunks],
    "chunk_id_set_same": {c["chunk_id"] for c in new_chunks} == {c["chunk_id"] for c in old_chunks},
}
diff_fields: Counter = Counter()
n_changed = 0
for a, b in zip(old_chunks, new_chunks):
    if a != b:
        n_changed += 1
        for k in set(a) | set(b):
            if a.get(k) != b.get(k):
                diff_fields[k] += 1
OUT["chunks"]["n_changed"] = n_changed
OUT["chunks"]["changed_fields"] = dict(diff_fields)
interp_new = [c for c in new_chunks if str(c.get("standard", "")).startswith("OSHA-INTERP")]
interp_old_style = [c for c in new_chunks if str(c.get("standard", "")).startswith("interpretation_")]
OUT["chunks"]["interp_new_style"] = len(interp_new)
OUT["chunks"]["interp_old_style_left"] = len(interp_old_style)

# 3) 新旧 mapping 对比
new_map = list(csv.DictReader(io.StringIO(read_auto(D / "knowledge/standard_document_mapping.csv"))))
old_map = list(csv.DictReader(io.StringIO(read_auto(OLD / "standard_document_mapping.csv"))))
OUT["mapping"] = {
    "new_rows": len(new_map),
    "old_rows": len(old_map),
    "header": list(new_map[0].keys()),
    "new_chunk_ids": len({r.get("片段编号") for r in new_map}),
    "chunk_id_set_same": {r.get("片段编号") for r in new_map} == {r.get("片段编号") for r in old_map},
    "n_changed": sum(1 for a, b in zip(old_map, new_map) if a != b),
}

# 4) 解释资料：chunk standard / mapping / inventory 三方是否对得上
inv = list(csv.DictReader(io.StringIO(read_auto(D / "knowledge/document_inventory.csv"))))
interp_inv = [r for r in inv if (r.get("来源类型") or "").strip() == "解释资料"]
OUT["inventory_interp"] = [
    {k: r.get(k) for k in ("文档编号", "OSHA标准编号", "标题", "来源类型")} for r in interp_inv
]
map_by_chunk = {r.get("片段编号"): r for r in new_map}
OUT["interp_mapping_rows"] = [
    {
        "chunk_id": c["chunk_id"],
        "chunk_standard": c.get("standard"),
        "mapping_standard": map_by_chunk.get(c["chunk_id"], {}).get("OSHA标准编号", ""),
        "mapping_section": map_by_chunk.get(c["chunk_id"], {}).get("条款编号", ""),
    }
    for c in interp_new[:4]
]

# 5) 模拟 stage9 适配器 document_id 解析
inv_std_keys: set[str] = set()
for r in inv:
    for part in str(r.get("OSHA标准编号") or "").split(","):
        p = part.strip()
        if p:
            inv_std_keys.add(p)
    inv_std_keys.add((r.get("文档编号") or "").strip())
unknown = 0
samples = []
for c in new_chunks:
    cid = c["chunk_id"]
    std = (c.get("standard") or "").strip()
    mstd = (map_by_chunk.get(cid, {}).get("OSHA标准编号") or "").strip() or std
    if mstd not in inv_std_keys and cid in map_by_chunk:
        unknown += 1
        if len(samples) < 8:
            samples.append((cid, std, mstd))
OUT["adapter_document_id_check"] = {"unknown_chunks": unknown, "samples": samples}

# 6) gold / retrieval_gold 的 chunk 引用在新 chunks 中是否都存在
gold = load_jsonl(ROOT / r"origin_data\from_student3\student3\学生4需要的文件\benchmark_gold_restricted.jsonl")
new_ids = {c["chunk_id"] for c in new_chunks}
missing = 0
total = 0
for r in gold:
    for ref in r.get("gold_regulation_document_ids") or []:
        cid = ref.get("chunk_id")
        if cid:
            total += 1
            if cid not in new_ids:
                missing += 1
OUT["gold_refs_in_new_chunks"] = {"refs_with_chunk": total, "missing": missing}
rg = list(csv.DictReader(io.StringIO(read_auto(D / "knowledge/retrieval_gold.csv"))))
rg_missing = [r for r in rg if r.get("正确片段编号") not in new_ids]
OUT["retrieval_gold_missing_chunks"] = len(rg_missing)

# 7) chunk_ids.json 与 chunks 顺序一致性
chunk_ids = json.loads(read_auto(D / "knowledge/vector_db/chunk_ids.json"))
if isinstance(chunk_ids, list):
    OUT["chunk_ids_order_matches_chunks"] = chunk_ids == [c["chunk_id"] for c in new_chunks]
    OUT["chunk_ids_n"] = len(chunk_ids)
else:
    OUT["chunk_ids_type"] = type(chunk_ids).__name__
    OUT["chunk_ids_sample"] = list(chunk_ids)[:5]

# 8) student2 包内映射 vs 学生1 最终映射
mp2 = list(csv.DictReader(io.StringIO(read_auto(D / "knowledge/standard_to_r1r9_mapping.csv"))))
mp1 = list(csv.DictReader(io.StringIO(read_auto(ROOT / r"origin_data\from_student1\回复学生4_20260824\回复学生4_20260824\standard_to_r1r9_mapping.csv"))))
OUT["r1r9_map_compare"] = {
    "s2_rows": len(mp2),
    "s1_rows": len(mp1),
    "s2_header": list(mp2[0].keys()),
    "s1_header": list(mp1[0].keys()),
    "s2_cat_dist": dict(Counter((r.get("r_category") or "").strip() for r in mp2)),
    "s1_cat_dist": dict(Counter((r.get("r_category") or "").strip() for r in mp1)),
    "s2_r6": sum(1 for r in mp2 if (r.get("r_category") or "").strip() == "R6"),
    "s1_r6": sum(1 for r in mp1 if (r.get("r_category") or "").strip() == "R6"),
    "s2_sample_names": [(r.get("r_category"), r.get("r_category_name")) for r in mp2[:3]],
    "s1_sample_names": [(r.get("r_category"), r.get("r_category_name")) for r in mp1[:3]],
}

# 9) 新 rag_retriever.py 关键逻辑
rag = read_auto(D / "src/retrieval/rag_retriever.py")
OUT["rag_retriever"] = {
    "len": len(rag),
    "has_section_match": "section" in rag and "startswith" in rag,
    "has_float_compare": "float(" in rag,
    "has_osha_interp": "OSHA-INTERP" in rag or "interpretation_" in rag,
    "head": rag[:1200],
}

# 10) metrics 内部一致性
metrics = list(csv.DictReader(io.StringIO(read_auto(D / "knowledge/retrieval_validation_metrics.csv"))))
OUT["metrics"] = {
    "n_rows": len(metrics),
    "header": list(metrics[0].keys()),
    "ndcg_na": sum(1 for r in metrics if (r.get("ndcg_at_10") or "").strip().upper() == "N/A"),
    "rows": metrics,
}

REP = ROOT / "stage9_验收报告_20260824"
(REP / "audit_findings5.json").write_text(json.dumps(OUT, ensure_ascii=False, indent=2), encoding="utf-8")
print("OK")
