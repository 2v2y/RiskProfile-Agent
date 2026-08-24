import csv
import json
import sys
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(r"G:\Projects\intern_project\CahngeDirect_eassy")
REP = ROOT / "stage9_验收报告_20260824"

findings = json.loads((REP / "audit_findings.json").read_text(encoding="utf-8"))
hashes = findings["hashes"]
csv_info = findings["csv_info"]
jsonl_info = findings["jsonl_info"]

rows = []
for p_str, v in hashes.items():
    p = Path(p_str)
    rel = str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
    name = p.name
    ext = p.suffix.lower()
    owner = "交接区"
    if "from_student1" in p_str:
        owner = "学生1"
    elif "from_student2" in p_str:
        owner = "学生2"
    elif "from_student3" in p_str:
        owner = "学生3"
    elif "stage9_edit" in p_str:
        owner = "stage9_edit(学生4集成)"
    elif "github_clone" in p_str or "eassy_electric" in p_str:
        owner = "交接区"
    n_rows = None
    n_cols = None
    header = ""
    if p_str in csv_info:
        ci = csv_info[p_str]
        n_rows = ci["n_rows"]
        n_cols = ci["n_cols"]
        header = "; ".join(ci["header"])
    if p_str in jsonl_info:
        ji = jsonl_info[p_str]
        n_rows = ji["n_records"]
    rows.append(
        {
            "学生": owner,
            "文件路径": rel,
            "文件名": name,
            "类型": ext,
            "大小字节": v.get("size", ""),
            "修改时间": v.get("mtime", ""),
            "SHA256": v.get("sha256", ""),
            "行数/记录数": n_rows,
            "字段数": n_cols,
            "字段名": header,
        }
    )

rows.sort(key=lambda r: (r["学生"], r["文件路径"]))
out = REP / "01_交付物总清单.csv"
with open(out, "w", encoding="utf-8-sig", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
print(f"OK rows={len(rows)} -> {out}")
