# 混合检索调参记录（P1-1.4）

日期：2026-10-16。实现：`app/modules/knowledge/service.py::search(mode="hybrid")`。

## 方法

- 关键词分：字符 bigram 集合的 **Overlap 系数** `|A∩B|/min(|A|,|B|)`。
  选 Overlap 而非 Jaccard：查询远短于 chunk，Jaccard 分母含并集会系统性压低
  完全包含查询词的长 chunk；Overlap 刻画"查询被 chunk 覆盖的程度"，逐字命中得 1.0。
- 融合：`final = alpha·clip01(余弦) + (1−alpha)·keyword`；融合后仍用
  `retrieval_min_score` 阈值过滤，阈值语义不变。
- 评测：12 文档（含 2 篇干扰文档）× 10 问（5 exact 精确词 + 5 paraphrase 同义改写），
  指标为 top-5 召回（`expected_doc_title_contains` 命中）与 MRR。
- 调参时将 `retrieval_min_score` 临时置 0.0，只比较"排序质量"（隔离阈值影响）；
  默认阈值 0.25 的语义未动。
- 注意：Mock embedding 本身也是字符 bigram 模型，与关键词分天然正相关；
  真实向量模型上线后建议用正式评估集（`eval/qa_eval_v1.json`）复核一次。

## 对比结果（smoke 集 `eval/hybrid_smoke_eval_v1.json`）

| 模式 | recall@5 | MRR | exact (5) | paraphrase (5) |
|------|----------|-----|-----------|----------------|
| vector | 0.80 | 0.700 | 5/5 | 3/5 |
| hybrid α=0.3 | 0.90 | 0.850 | 5/5 | 4/5 |
| hybrid α=0.5 | 0.90 | 0.850 | 5/5 | 4/5 |
| hybrid α=0.6 | 0.90 | 0.850 | 5/5 | 4/5 |
| hybrid α=0.7 | 0.90 | 0.850 | 5/5 | 4/5 |
| hybrid α=0.8 | 0.90 | 0.800 | 5/5 | 4/5 |
| hybrid α=0.9 | 0.90 | 0.725 | 5/5 | 4/5 |
| hybrid α=1.0 | 0.80 | 0.700 | 5/5 | 3/5（≡ vector，预期）|

- hybrid 全 alpha 均 **≥ vector 基线**，主要修复了 1 条同义改写查询的漏召回。
- α∈{0.3,0.5,0.6,0.7} 四组在小集上并列最优；α≥0.8 时关键词权重不足，MRR 退化。

## 最终取值

- `retrieval_mode = "hybrid"`（smoke 集上 recall 0.90 > 基线 0.80，达到"≥基线"要求）
- `retrieval_alpha = 0.7` —— 一句话理由：在并列最优的 α 区间里取最保守的一端，
  保持向量语义主导（生产环境向量质量高于 mock），同时留 0.3 关键词权重足以
  补救纯向量的漏召回。
