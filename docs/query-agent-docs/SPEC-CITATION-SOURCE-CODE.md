# Spec: citation 携带 DM 源码(source_code / source_doc_id)—— 四级回查可解析到达梦

> SDD 阶段:**Phase 1 / SPECIFY(随分支 `feat/citation-source-code`)**。
> 触发:边界 SSE(`routes_boundary.py:122-126`)只吐 `clause_id`=`chunk_id`(audit-ai 的 sha1 派生哈希),
> 契约声称"四级回查归 Java 收口",但 **Java 查的是达梦(DM),DM 没有 chunk_id** → Java 实际回查不了。
> 根因:audit-ai 早已在库里存了 DM 原生主键(`chunks.source_code`=`LAW_CONTENT.CODE`、
> `doc_versions.source_doc_id`=`LAW_BASIC.CODE`,preseg `adapter.py:92`/`export.py:242,311` 透传),
> 但**没投影到 Milvus、也没进 citation 契约**。本切片补上,使 Java 能按 DM CODE 回查四级引用。

## 0. 切片边界(做 / 不做)

**做**:①Milvus schema 增 2 标量 `source_code`/`source_doc_id`(add-only,§8.2 增列);②摄取端从
`chunks.source_code`/`doc_versions.source_doc_id` 投影入 Milvus(数据已在 PG,零重算);③检索 hit →
`Candidate` → citation 携带这两码;④边界 SSE citation 帧**从候选(collector)吐**这两码(PG-free);
⑤`fetch_anchors`(前端路)也补选这两码;⑥**biz `boundary.v1.yaml` citation schema 增量草案**(跨仓,待 Java 对齐)。

**不做**:①**去 PG**(PG 仍是 chunk_id↔DM CODE 映射的建立处 + 冷备/对账/版本权威,不动);②让整条
`ask` 热路径**完全**不回查 PG(R1 内部 `fetch_anchors` 仍建全字段 Citation——那是"边界热路径不依赖 PG"的
**独立后续切片 Level 2**,本切片只保证**吐出去的 ID 可回查 DM**,并把 source_code 备到 Milvus hit 上作 Level 2 前置);
③非 DM 源语料(P-QA 自建)补 source_code(它本就没有 DM 码,见 §4 边角3);④改 chunk_id 公式 / clause_path 语义。

## 1. Objective

边界 SSE 的 citation 除 `clause_id`(audit-ai 检索身份)外,**并列携带 DM 可解析码**
`source_code`(`LAW_CONTENT.CODE`,条/块级)+ `source_doc_id`(`LAW_BASIC.CODE`,文档级),使 Java 能按 CODE
回查 DM 的 `LAW_CONTENT`⨝`LAW_BASIC` 拼四级引用(标题/文号/版本/条款)。完成 = 边界 citation 帧含非空
source_code(DM 源语料)、来源为 Milvus hit(不新增 PG 回查)、既有前端 `/api/query/v1/*` 契约不回归。

## 2. 契约 delta(跨仓,须与 Java 对齐)

> **已决(2026-07-24,与用户确认)**:①Java citation 回查**按两个逻辑 CODE 取数** =
> `source_code`(`LAW_CONTENT.CODE`,条/块级)+ `source_doc_id`(`LAW_BASIC.CODE`,文档级)——即本切片所带,
> 与案例桥接 96.7% 命中同键;②**无需 audit-ai 额外带字段**,四级引用(标题/文号/版本/条款)Java 从
> `LAW_BASIC`+`LAW_CONTENT` 拼得出;③**达梦无页码**(D2)→ DM 源语料引用天然**三级**(文档/条款,无页)。

- **biz `boundary.v1.yaml`**(单一源,先改 biz 后本仓照做):citation object 增
  `source_code: string|null`、`source_doc_id: string|null`。语义:DM 回查主键;**可空**(超 256 弃锚 / 非 DM 语料)。
- 本仓 `Citation.to_dict()` + 边界 SSE `citation` 帧增这两键(add-only,`clause_id`/`chunk_id`/`score` 不变,
  Java 未接入前可忽略新键)。
- **治理**:本切片先 commit 草案 + 开 Issue 引本 SPEC 与 Java 对齐 → biz 改 yaml 一个批处理小 PR → 本仓更新 pointer。

### 2.1 biz `boundary.v1.yaml` 增量草案(待 Java 定稿)

```yaml
# citation object(边界二 SSE citation 事件 payload)增量:
Citation:
  properties:
    clause_id:    { type: string, description: "audit-ai 检索身份(chunk_id);非 DM 键" }
    score:        { type: number, nullable: true }
    # ── 新增(CP-010:达梦回查键;Java 按此回查 LAW_BASIC/LAW_CONTENT 拼四级引用)──
    source_code:  { type: string, nullable: true,
                    description: "达梦 LAW_CONTENT.CODE(条/块级)。非 DM 源 / 超256弃锚 → null,Java 回落 fuzzy" }
    source_doc_id: { type: string, nullable: true,
                     description: "达梦 LAW_BASIC.CODE(文档级)" }
```

## 3. Project Structure(增量,全 add-only)

```
libs/common/common/milvus_schema.py   +FieldSchema source_code(VARCHAR256)/source_doc_id(VARCHAR64)
pipeline/pipeline/index/milvus_io.py   CorpusRow/_to_milvus_dict/_OUTPUT_FIELDS +2 字段
pipeline/pipeline/index/corpus_rows.py build_rows 从 chunks.source_code / dv.source_doc_id 填
query/query/retrieve/hybrid.py         Candidate/_to_candidate +2 字段(默认 None,保位置构造兼容)
query/query/contract.py                Citation +source_code/source_doc_id(默认 None)+ to_dict
query/query/generate/anchors.py        fetch_anchors 补选 chunks.source_code / dv.source_doc_id
query/query/api/routes_boundary.py     citation 帧从 collector 候选吐 source_code/source_doc_id
docs/query-agent-docs/BOUNDARY-*.md    biz yaml 增量草案 + 待对齐记录
```

## 4. 边角(须交代,否则漏)

1. **多对一**:DM 一块超预算切多个 chunk(seq 递增)→ 多 chunk_id 同一 source_code;Java 按 CODE 得同一条款,语义正确。
2. **超 256 弃锚**:`LAW_CONTENT.CODE` 超 256 时 preseg 弃锚回落 fuzzy(`export.py:200`)→ 该批 source_code 空;
   契约标"可空",Java 回落 clause_path/文号 fuzzy。
3. **非 DM 源语料**:P-QA 自建无 source_code → citation 该两码为空;Java 对这类回落 audit-ai 或排除出 DM-citation 域。

## 5. Testing(TDD)

- 单元:schema 含 2 字段;`build_rows` 填对(chunk.source_code / dv.source_doc_id);`_to_milvus_dict` 带 2 键;
  `_to_candidate` 从 hit 取;`Citation.to_dict` 含 2 键(默认 None 向后兼容)。
- 真栈:摄取后 Milvus hit 带 source_code(`search` output_fields);`fetch_anchors` 回查得 source_code。
- 边界:SSE citation 帧含 source_code/source_doc_id,取自候选(断言不新增 PG 回查)。
- 回归:既有前端 citation 契约(`contract.py` to_dict 增键不减键)、chunk_id 公式 pin、bge 检索全绿。

## 6. Boundaries

- **Always**:add-only(既有字段/契约不减不改);source_code 来自 Milvus hit(边界吐值不新增 PG 回查);
  非 DM 语料/弃锚 → 值为空(不报错、不误当有效码)。
- **Ask-first**:**biz `boundary.v1.yaml` 增量**须先与 Java 对齐(跨仓契约,不本仓单方定稿);
  Milvus 增列需重灌才对存量填值(greenfield 内网无碍;本地存量需 reprocess/rebuild 后才有值)。
- **Never**:去 PG / 改 chunk_id 公式 / 用 chunk_id 冒充 DM 键让 Java 回查(本切片正为纠此)。

## 7. 与 PG 之争的关系

本切片是"**在线热路径去 PG 依赖(Option B)**"的第一步、也是其数据前置:把 DM CODE 备到 Milvus hit 上。
**它证明 ID 映射问题本已解(source_code 在库),缺的只是投影 + 上契约**——不需要、也不应该删 PG。
