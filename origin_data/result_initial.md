### 对比实验完成：681 样本 x 6 方法
结果目录：/root/RiskProfile-Agent/operate/stage9_full/results/comparison/20260825_200630_comparison_154135

#### 汇总（mean）：
  B0: numeric_accuracy=1.0, citation_validity=1.0, citation_correctness=0.0, evidence_support=0.099853, unsupported_claim=0.900147, traceability=1.0, safe_refusal=0.754772
  B1: numeric_accuracy=1.0, citation_validity=1.0, citation_correctness=0.0, evidence_support=1.0, unsupported_claim=0.0, traceability=1.0, safe_refusal=0.286344
  B2: numeric_accuracy=1.0, citation_validity=1.0, citation_correctness=0.0, evidence_support=0.25257, unsupported_claim=0.74743, traceability=1.0, safe_refusal=0.61674
  B3: numeric_accuracy=1.0, citation_validity=1.0, citation_correctness=0.0, evidence_support=1.0, unsupported_claim=0.0, traceability=1.0, safe_refusal=0.286344
  B4: numeric_accuracy=1.0, citation_validity=1.0, citation_correctness=0.0, evidence_support=0.25257, unsupported_claim=0.74743, traceability=1.0, safe_refusal=0.61674
  B5: numeric_accuracy=1.0, citation_validity=1.0, citation_correctness=0.0, evidence_support=0.099853, unsupported_claim=0.900147, traceability=1.0, safe_refusal=0.754772

  B0:  evidence_support=0.099853, unsupported_claim=0.900147,  safe_refusal=0.754772
  B1:  evidence_support=1.0,      unsupported_claim=0.0,       safe_refusal=0.286344
  B2:  evidence_support=0.25257,  unsupported_claim=0.74743,   safe_refusal=0.61674
  B3:  evidence_support=1.0,      unsupported_claim=0.0,       safe_refusal=0.286344
  B4:  evidence_support=0.25257,  unsupported_claim=0.74743,   safe_refusal=0.61674
  B5:  evidence_support=0.099853, unsupported_claim=0.900147,  safe_refusal=0.754772

### 消融实验结果：
comparison,metric,full_mean,variant_mean,diff,ci_lower,ci_upper,n_pairs
B5 vs full_minus_semantic_audit,numeric_accuracy,1.0,1.0,0.0,0.0,0.0,681
B5 vs full_minus_semantic_audit,citation_correctness,0.0,0.0,0.0,0.0,0.0,681
B5 vs full_minus_semantic_audit,unsupported_claim,0.900147,0.74743,0.152717,0.126285,0.180617,681
B5 vs full_minus_semantic_audit,traceability,1.0,1.0,0.0,0.0,0.0,681
B5 vs full_minus_semantic_audit,safe_refusal,0.754772,0.61674,0.138032,0.110132,0.165932,681
B5 vs full_minus_retrieval,numeric_accuracy,1.0,1.0,0.0,0.0,0.0,681
B5 vs full_minus_retrieval,citation_correctness,0.0,0.0,0.0,0.0,0.0,681
B5 vs full_minus_retrieval,unsupported_claim,0.900147,0.0,0.900147,0.876652,0.922173,681
B5 vs full_minus_retrieval,traceability,1.0,1.0,0.0,0.0,0.0,681
B5 vs full_minus_retrieval,safe_refusal,0.754772,0.286344,0.468428,0.409692,0.530103,681
B5 vs full_minus_audit,numeric_accuracy,1.0,1.0,0.0,0.0,0.0,681
B5 vs full_minus_audit,citation_correctness,0.0,0.0,0.0,0.0,0.0,681
B5 vs full_minus_audit,unsupported_claim,0.900147,0.74743,0.152717,0.126285,0.180617,681
B5 vs full_minus_audit,traceability,1.0,1.0,0.0,0.0,0.0,681
B5 vs full_minus_audit,safe_refusal,0.754772,0.61674,0.138032,0.110132,0.165932,681



### ERROR ANALYSIS
错误分析完成：4086 行，结果目录 /root/RiskProfile-Agent/operate/stage9_full/results/error_analysis/20260825_204517_error_analysis_f13c54
  B0 错误率： {'NUMERIC_ERROR': 0.0, 'CITATION_ERROR': 0.466119, 'EVIDENCE_UNSUPPORTED': 0.0, 'UNSUPPORTED_CLAIM': 0.419576, 'OUT_OF_SCOPE': 0.0, 'REFUSAL_ERROR': 0.114305, 'OTHER': 0.0}
  B1 错误率： {'NUMERIC_ERROR': 0.0, 'CITATION_ERROR': 0.583548, 'EVIDENCE_UNSUPPORTED': 0.0, 'UNSUPPORTED_CLAIM': 0.0, 'OUT_OF_SCOPE': 0.0, 'REFUSAL_ERROR': 0.416452, 'OTHER': 0.0}
  B2 错误率： {'NUMERIC_ERROR': 0.0, 'CITATION_ERROR': 0.469331, 'EVIDENCE_UNSUPPORTED': 0.0, 'UNSUPPORTED_CLAIM': 0.350793, 'OUT_OF_SCOPE': 0.0, 'REFUSAL_ERROR': 0.179876, 'OTHER': 0.0}
  B3 错误率： {'NUMERIC_ERROR': 0.0, 'CITATION_ERROR': 0.583548, 'EVIDENCE_UNSUPPORTED': 0.0, 'UNSUPPORTED_CLAIM': 0.0, 'OUT_OF_SCOPE': 0.0, 'REFUSAL_ERROR': 0.416452, 'OTHER': 0.0}
  B4 错误率： {'NUMERIC_ERROR': 0.0, 'CITATION_ERROR': 0.469331, 'EVIDENCE_UNSUPPORTED': 0.0, 'UNSUPPORTED_CLAIM': 0.350793, 'OUT_OF_SCOPE': 0.0, 'REFUSAL_ERROR': 0.179876, 'OTHER': 0.0}
  B5 错误率： {'NUMERIC_ERROR': 0.0, 'CITATION_ERROR': 0.466119, 'EVIDENCE_UNSUPPORTED': 0.0, 'UNSUPPORTED_CLAIM': 0.419576, 'OUT_OF_SCOPE': 0.0, 'REFUSAL_ERROR': 0.114305, 'OTHER': 0.0}