"""只读验收分析脚本：哈希/表头/行数/JSONL结构/关联键匹配/字段取值分布。

仅读取原始交付物，不修改任何学生文件；输出写入本报告目录。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path

ROOT = Path(r"G:\Projects\intern_project\CahngeDirect_eassy")
REPORT = Path(r"G:\Projects\intern_project\CahngeDirect_eassy\stage9_验收报告_20260824")
GIT = Path(r"G:\Projects\github_clone\eassy_electric")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text_auto(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "replace"


def csv_info(path: Path) -> dict:
    text, enc = read_text_auto(path)
    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []
    rows = 0
    for _ in reader:
        rows += 1
    return {"encoding": enc, "n_rows": rows, "n_cols": len(header), "header": header}


def jsonl_info(path: Path, n: int = 2) -> dict:
    text, enc = read_text_auto(path)
    records = [json.loads(line) for line in text.splitlines() if line.strip()]
    sample = records[:n]
    return {
        "encoding": enc,
        "n_records": len(records),
        "sample": sample,
        "keys_first": list(sample[0].keys()) if sample else [],
    }


HASH_FILES = [
    # 学生1
    ROOT / r"origin_data\from_student1\student1\数据_分析数据\profiles_train_val.csv",
    ROOT / r"origin_data\from_student1\student1\数据_分析数据\feature_dictionary.csv",
    ROOT / r"origin_data\from_student1\student1\数据_分析数据\inspection_clean.csv",
    ROOT / r"origin_data\from_student1\student1\数据_分析数据\inspection_episode.csv",
    ROOT / r"origin_data\from_student1\student1\数据_分析数据\violation_clean.csv",
    ROOT / r"origin_data\from_student1\student1\数据_分析数据\historical_inspection_outcomes.csv",
    ROOT / r"origin_data\from_student1\profile_supplement_8fields.csv",
    ROOT / r"origin_data\from_student1\agent_profile_whitelist.json",
    ROOT / r"origin_data\from_student1\学生4_确认包_20260824\学生4_确认包_20260824\profile_supplement_8fields.csv",
    ROOT / r"origin_data\from_student1\学生4_确认包_20260824\学生4_确认包_20260824\agent_profile_whitelist.json",
    ROOT / r"origin_data\from_student1\学生4_确认包_20260824\学生4_确认包_20260824\standard_to_r1r9_mapping.csv",
    ROOT / r"origin_data\from_student1\学生4_确认包_20260824\学生4_确认包_20260824\risk_score_verification.csv",
    ROOT / r"origin_data\from_student1\学生4_确认包_20260824\学生4_确认包_20260824\final_verify.py",
    ROOT / r"origin_data\from_student1\student1\结果_验证\frozen_model.joblib",
    ROOT / r"origin_data\from_student1\student1\结果_验证\frozen_manifest.json",
    ROOT / r"origin_data\from_student1\student1\结果_验证\frozen_config.json",
    ROOT / r"origin_data\from_student1\student1\结果_验证\model_freeze_record.json",
    ROOT / r"origin_data\from_student1\student1\结果_验证\model_selection.json",
    ROOT / r"origin_data\from_student1\student1\结果_验证\test_prediction_commitment.json",
    ROOT / r"origin_data\from_student1\student1\结果_画像\防泄漏审计.json",
    ROOT / r"origin_data\from_student1\student1\结果_画像\画像汇总.csv",
    ROOT / r"origin_data\from_student1\student1\结果_数据审计\时间切分审计.csv",
    ROOT / r"origin_data\from_student1\student1\结果_数据审计\连接与去重审计.json",
    ROOT / r"origin_data\from_student1\student1\结果_数据审计\字段与规则快照.json",
    ROOT / r"origin_data\from_student1\student1\记录表\测试开封记录.csv",
    ROOT / r"origin_data\from_student1\student1\记录表\数据快照记录.csv",
    ROOT / r"origin_data\from_student1\student1\记录表\字段检查.csv",
    # 学生2
    ROOT / r"origin_data\from_student2\student2\风险分类.csv",
    ROOT / r"origin_data\from_student2\student2\regulation_chunks.jsonl",
    ROOT / r"origin_data\from_student2\student2\standard_document_mapping.csv",
    ROOT / r"origin_data\from_student2\student2\document_inventory.csv",
    ROOT / r"origin_data\from_student2\student2\knowledge_manifest.json",
    ROOT / r"origin_data\from_student2\student2\retrieval_gold.csv",
    ROOT / r"origin_data\from_student2\student2\交付_学生4\交付_学生4\knowledge\chunks\regulation_chunks.jsonl",
    ROOT / r"stage9_验收报告_20260824\extracted_student2_20260824\交付_学生4\knowledge\knowledge_manifest.json",
    ROOT / r"stage9_验收报告_20260824\extracted_student2_20260824\交付_学生4\knowledge\retrieval_validation_metrics.csv",
    ROOT / r"stage9_验收报告_20260824\extracted_student2_20260824\交付_学生4\knowledge\vector_db\db_meta.json",
    ROOT / r"stage9_验收报告_20260824\extracted_student2_20260824\交付_学生4\knowledge\document_inventory.csv",
    ROOT / r"stage9_验收报告_20260824\extracted_student2_20260824\交付_学生4\knowledge\retrieval_gold.csv",
    ROOT / r"stage9_验收报告_20260824\extracted_student2_20260824\交付_学生4\knowledge\standard_document_mapping.csv",
    ROOT / r"stage9_验收报告_20260824\extracted_student2_20260824\交付_学生4\knowledge\chunks\regulation_chunks.jsonl",
    ROOT / r"origin_data\from_student2\benchmark_cases.jsonl",
    ROOT / r"origin_data\from_student2\red_team_cases.jsonl",
    ROOT / r"origin_data\from_student2\交付_学生4.zip",
    ROOT / r"origin_data\from_student2\student2_1609.zip",
    ROOT / r"origin_data\from_student2\交付学生4.zip",
    # 学生3
    ROOT / r"origin_data\from_student3\benchmark_cases.jsonl",
    ROOT / r"origin_data\from_student3\red_team_cases.jsonl",
    ROOT / r"origin_data\from_student3\benchmark_gold_restricted.jsonl",
    ROOT / r"origin_data\from_student3\benchmark_manifest.json",
    ROOT / r"origin_data\from_student3\阶段4学生3交付给学生4\profiles_train_val.csv",
    ROOT / r"origin_data\from_student3\阶段4学生3交付给学生4\feature_dictionary.csv",
    ROOT / r"origin_data\from_student3\阶段4学生3交付给学生4\agent_profile_whitelist.json",
    ROOT / r"origin_data\from_student3\阶段4学生3交付给学生4\profile_definition_manifest.json",
    ROOT / r"origin_data\from_student3\阶段4学生3交付给学生4\profile_recalculation_audit.csv",
    ROOT / r"origin_data\from_student3\阶段4学生3交付给学生4\temporal_leakage_audit.json",
    # stage9_edit
    ROOT / r"operate\stage9_edit\data\02_train_validation\profiles_train_val.csv",
    ROOT / r"operate\stage9_edit\data\02_train_validation\profile_supplement_8fields.csv",
    ROOT / r"operate\stage9_edit\knowledge\风险分类.csv",
    ROOT / r"operate\stage9_edit\knowledge\chunks\regulation_chunks.jsonl",
    ROOT / r"operate\stage9_edit\knowledge\chunks\standard_document_mapping.csv",
    ROOT / r"operate\stage9_edit\knowledge\chunks\retrieval_gold.csv",
    ROOT / r"operate\stage9_edit\knowledge\document_inventory.csv",
    ROOT / r"operate\stage9_edit\knowledge\knowledge_manifest.json",
    ROOT / r"operate\stage9_edit\knowledge\standard_to_r1r9_mapping.csv",
    ROOT / r"operate\stage9_edit\knowledge\vector_db\faiss_index.bin",
    ROOT / r"operate\stage9_edit\knowledge\vector_db\embeddings.npy",
    ROOT / r"operate\stage9_edit\knowledge\vector_db\db_meta.json",
    ROOT / r"operate\stage9_edit\knowledge\vector_db\chunk_ids.json",
    ROOT / r"operate\stage9_edit\configs\agent_profile_whitelist.json",
    ROOT / r"operate\stage9_edit\configs\agent_profile_whitelist_student1_confirmed.json",
    ROOT / r"operate\stage9_edit\benchmark\automatic\benchmark_cases.jsonl",
    ROOT / r"operate\stage9_edit\benchmark\red_team\red_team_cases.jsonl",
    # 交接区
    GIT / r"学生3交付物（学生4）\学生3交付物（学生4）\profiles_train_val.csv",
    GIT / r"学生3交付物（学生4）\学生3交付物（学生4）\feature_dictionary.csv",
    GIT / r"学生3交付物（学生4）\学生3交付物（学生4）\画像复算.csv",
    GIT / r"学生3交付物（学生4）\学生3交付物（学生4）\画像汇总.csv",
    GIT / r"学生3交付物（学生4）\学生3交付物（学生4）\防泄漏审计.json",
    GIT / r"文件交接.csv",
]


def main() -> None:
    out: dict = {}

    hashes: dict[str, dict] = {}
    for p in HASH_FILES:
        if not p.exists():
            hashes[str(p)] = {"exists": False}
            continue
        st = p.stat()
        hashes[str(p)] = {
            "exists": True,
            "size": st.st_size,
            "mtime": st.st_mtime,
            "sha256": sha256(p),
        }
    out["hashes"] = hashes

    csv_paths = [Path(k) for k, v in hashes.items() if v.get("exists") and k.lower().endswith(".csv")]
    out["csv_info"] = {str(p): csv_info(p) for p in csv_paths}

    jsonl_paths = [Path(k) for k, v in hashes.items() if v.get("exists") and k.lower().endswith(".jsonl")]
    out["jsonl_info"] = {str(p): jsonl_info(p, n=2) for p in jsonl_paths}

    json_paths = [
        Path(k)
        for k, v in hashes.items()
        if v.get("exists")
        and k.lower().endswith(".json")
        and hashes[k]["size"] < 200_000
    ]
    json_meta: dict[str, dict] = {}
    for p in json_paths:
        text, enc = read_text_auto(p)
        try:
            data = json.loads(text)
            keys = list(data.keys()) if isinstance(data, dict) else f"list[{len(data)}]"
            json_meta[str(p)] = {"encoding": enc, "top_keys": keys}
            if isinstance(data, dict) and "知识库版本" in data:
                json_meta[str(p)]["知识库版本"] = data["知识库版本"]
        except Exception as exc:  # noqa: BLE001
            json_meta[str(p)] = {"parse_error": str(exc)[:200]}
    out["json_meta"] = json_meta

    prof = ROOT / r"operate\stage9_edit\data\02_train_validation\profiles_train_val.csv"
    supp = ROOT / r"operate\stage9_edit\data\02_train_validation\profile_supplement_8fields.csv"
    if prof.exists() and supp.exists():
        ptext, _ = read_text_auto(prof)
        stext, _ = read_text_auto(supp)
        prows = list(csv.DictReader(io.StringIO(ptext)))
        srows = list(csv.DictReader(io.StringIO(stext)))
        pkeys = Counter((r["sample_id"], r["quarter"]) for r in prows)
        skeys = Counter((r["sample_id"], r["quarter"]) for r in srows)
        dup_p = {k: v for k, v in pkeys.items() if v > 1}
        dup_s = {k: v for k, v in skeys.items() if v > 1}
        sset = set(skeys)
        matched = sum(1 for k in pkeys if k in sset)
        supp_extra = sum(1 for k in skeys if k not in pkeys)
        split = Counter(r["split"] for r in prows)

        percentiles: list[float] = []
        scores: list[float] = []
        model_hashes = set()
        score_ev: list[str] = []
        rc_keys: Counter = Counter()
        hr_cats: Counter = Counter()
        missing_required: Counter = Counter()
        for r in srows:
            try:
                percentiles.append(float(r["risk_percentile"]))
            except ValueError:
                missing_required["risk_percentile_nonnum"] += 1
            try:
                scores.append(float(r["risk_score"]))
            except ValueError:
                missing_required["risk_score_nonnum"] += 1
            model_hashes.add(r.get("model_hash", ""))
            sev = r.get("score_evidence", "")
            score_ev.append(sev)
            if not sev:
                missing_required["score_evidence_empty"] += 1
            if not r.get("model_hash", ""):
                missing_required["model_hash_empty"] += 1
            try:
                d = json.loads(r.get("risk_category_counts", "{}"))
                rc_keys.update(d.keys())
            except Exception:
                missing_required["risk_category_counts_badjson"] += 1
            try:
                lst = json.loads(r.get("historical_risk_categories", "[]"))
                hr_cats.update(lst)
            except Exception:
                missing_required["historical_risk_categories_badjson"] += 1

        out["profile_supplement_join"] = {
            "profile_rows": len(prows),
            "supp_rows": len(srows),
            "profile_dup_keys": dup_p,
            "supp_dup_keys": dup_s,
            "matched_keys": matched,
            "profile_unmatched": len(pkeys) - matched,
            "supp_extra_keys": supp_extra,
            "split_counts": dict(split),
            "risk_percentile_min": min(percentiles) if percentiles else None,
            "risk_percentile_max": max(percentiles) if percentiles else None,
            "risk_score_min": min(scores) if scores else None,
            "risk_score_max": max(scores) if scores else None,
            "model_hash_distinct": sorted(model_hashes),
            "risk_category_counts_keys": dict(rc_keys),
            "historical_risk_categories_values": dict(hr_cats),
            "missing_required": dict(missing_required),
            "score_evidence_sample": score_ev[:2],
        }

    bench_s3 = ROOT / r"origin_data\from_student3\benchmark_cases.jsonl"
    bench_s2 = ROOT / r"origin_data\from_student2\benchmark_cases.jsonl"
    gold = ROOT / r"origin_data\from_student3\benchmark_gold_restricted.jsonl"
    bench_stage9 = ROOT / r"operate\stage9_edit\benchmark\automatic\benchmark_cases.jsonl"

    def load_jsonl_lines(p: Path) -> list[dict]:
        text, _ = read_text_auto(p)
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    bench_meta: dict = {}
    for label, p in (("s3", bench_s3), ("s2", bench_s2), ("stage9", bench_stage9)):
        if not p.exists():
            bench_meta[label] = {"exists": False}
            continue
        recs = load_jsonl_lines(p)
        keys = list(recs[0].keys()) if recs else []
        case_ids = [str(r.get("case_id")) for r in recs]
        bench_meta[label] = {
            "n": len(recs),
            "keys": keys,
            "case_id_dup": len(case_ids) - len(set(case_ids)),
            "case_id_sample": case_ids[:5],
            "knowledge_version": dict(Counter(str(r.get("knowledge_version")) for r in recs)),
            "first_record": recs[0] if recs else None,
        }
    out["benchmark_meta"] = bench_meta

    if gold.exists():
        grecs = load_jsonl_lines(gold)
        gkeys = list(grecs[0].keys()) if grecs else []
        gids = [str(r.get("case_id")) for r in grecs]
        out["gold_meta"] = {
            "n": len(grecs),
            "keys": gkeys,
            "case_id_dup": len(gids) - len(set(gids)),
            "case_id_sample": gids[:5],
            "first_record": grecs[0] if grecs else None,
        }
        for label, p in (("s3", bench_s3), ("s2", bench_s2), ("stage9", bench_stage9)):
            if p.exists():
                bids = {str(r.get("case_id")) for r in load_jsonl_lines(p)}
                out["gold_meta"][f"overlap_{label}"] = len(set(gids) & bids)

    chunks = ROOT / r"operate\stage9_edit\knowledge\chunks\regulation_chunks.jsonl"
    if chunks.exists():
        crecs = load_jsonl_lines(chunks)
        ckeys = list(crecs[0].keys()) if crecs else []
        src_type = Counter(str(r.get("source_type")) for r in crecs)
        doc_ids = Counter(str(r.get("document_id")) for r in crecs)
        std_examples = [str(r.get("standard_number")) for r in crecs[:5]]
        risk_cats: Counter = Counter()
        for r in crecs:
            rc = r.get("risk_categories")
            if isinstance(rc, list):
                risk_cats.update(str(x) for x in rc)
            elif rc is not None:
                risk_cats[str(rc)] += 1
        out["chunks_meta"] = {
            "n": len(crecs),
            "keys": ckeys,
            "source_type": dict(src_type),
            "n_documents": len(doc_ids),
            "doc_ids_sample": list(doc_ids)[:5],
            "standard_examples": std_examples,
            "risk_categories_values": dict(risk_cats),
            "first_record": crecs[0] if crecs else None,
        }

    rg = ROOT / r"operate\stage9_edit\knowledge\chunks\retrieval_gold.csv"
    if rg.exists():
        text, enc = read_text_auto(rg)
        rows = list(csv.DictReader(io.StringIO(text)))
        out["retrieval_gold"] = {
            "encoding": enc,
            "n": len(rows),
            "header": list(rows[0].keys()) if rows else [],
            "first_rows": rows[:3],
        }

    rc_csv = ROOT / r"operate\stage9_edit\knowledge\风险分类.csv"
    if rc_csv.exists():
        text, enc = read_text_auto(rc_csv)
        rows = list(csv.DictReader(io.StringIO(text)))
        header = list(rows[0].keys()) if rows else []
        final_cols = [c for c in header if c and ("最终分类" in c or c == "r_category")]
        dist: Counter = Counter()
        for r in rows:
            for c in final_cols:
                v = (r.get(c) or "").strip()
                if v:
                    dist[(c, v)] += 1
        out["risk_classification_csv"] = {
            "encoding": enc,
            "n_rows": len(rows),
            "header": header,
            "category_distribution": {f"{c}={v}": n for (c, v), n in sorted(dist.items())},
        }

    mp = ROOT / r"operate\stage9_edit\knowledge\standard_to_r1r9_mapping.csv"
    if mp.exists():
        text, enc = read_text_auto(mp)
        rows = list(csv.DictReader(io.StringIO(text)))
        cat_dist = Counter((r.get("r_category") or "").strip() for r in rows)
        empty_std = sum(1 for r in rows if not (r.get("standard_code") or "").strip())
        out["r1r9_mapping"] = {
            "encoding": enc,
            "n_rows": len(rows),
            "header": list(rows[0].keys()) if rows else [],
            "r_category_dist": dict(cat_dist),
            "empty_standard_code": empty_std,
            "sample_rows": rows[:5],
        }

    mani = ROOT / r"origin_data\from_student3\benchmark_manifest.json"
    if mani.exists():
        text, enc = read_text_auto(mani)
        out["benchmark_manifest"] = {"encoding": enc, "content": json.loads(text)}

    report_path = REPORT / "audit_findings.json"
    report_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK -> {report_path}")


if __name__ == "__main__":
    main()
