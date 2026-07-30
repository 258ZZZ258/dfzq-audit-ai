# audit-biz 调用 audit-ai 查询接口(边界二)说明

> **本文件描述 `routes_boundary.py` 的当前实际行为**,与代码同步维护。
>
> ⚠ **与 biz 仓 `boundary.v1.yaml`(v1.2.0)存在已知差异,且本文为准**:该 yaml 仍把 `/v1/query`
> 定为 SSE,而 javainit ↔ audit-ai 联调已当面谈定改用一次性 JSON(2026-07 决策)。这违反
> 「契约单一源在 biz、本仓照做」的常规流程,是**待回灌**状态而非新口径——回灌清单见文末
> 「yaml 待回灌清单」,回灌后本节改回「以主本为准」。红线见 `BOUNDARY-biz-contract-pointer.md`。

## 一句话

Java 后端调用 Python `audit-ai` 的**无状态、无身份**制度查询热路径,使用一次性 JSON:

```text
POST /v1/query                    # 制度查询热路径;前端页面接口仍是 /api/query/v1/*(不受本接口影响)
POST /v1/cases/{case_id}          # 案例详情回查(读 audit-ai PG)
POST /v1/clauses/{clause_id}      # 条款详情回查(读 audit-ai PG,四级锚点 + 全文 + 节级父块)
POST /v1/dm/clauses/{source_code} # 条款全文回查(读达梦源库,非 audit-ai PG)
```

四个端点**都只给 Java 后端调用**,都要 `X-Internal-Token`。
实现:`query/query/api/routes_boundary.py`(薄壳 over `QueryAgent.ask`,不改 AI 内核)。

## 鉴权(无身份)

请求带 `X-Internal-Token: <令牌>`;Python 侧比对 env `AUDIT_AI_INTERNAL_TOKEN`
(绝不入库),用 `secrets.compare_digest` 常数时间比较。**env 未配置视为边界关闭**
(fail-closed)。本头**不承载用户身份**,只证明调用方是 audit-biz。缺失/不符/env 未配 → `401`:

```json
{ "error": { "code": "B104", "message": "内部令牌无效" } }
```

## 请求体

```json
{
  "query": "客户适当性依据",
  "request_id": "REQ-...",
  "filters": {
    "perm_tags": ["内部"],
    "corpus_types": ["internal"],
    "project_id": null,
    "owner": null
  },
  "options": { "top_k": 5, "include_superseded": false }
}
```

- `filters` 由 Java jCasbin **预计算**下传,audit-ai 直接据此构 Milvus **前置过滤**(检索**前**生效,
  红线:算在 Java、用在 Python;audit-ai 不做权限判断)。
- `perm_tags`:密级/职级标签。**空数组 = 无额外限制**(契约明文,非放行漏洞),字段必填。
- `corpus_types`:`internal|external|qa|case|audit_project`,**至少一个**(空 → 422)。映射到 Milvus
  分区 `P-INT|P-EXT|P-QA|P-CASE`。
- `owner`/`project_id`:仅 `audit_project` 语义;制度语料按契约忽略 `owner`。
- `options.include_superseded`:对**答案与引用**生效(不只影响分数),默认只检索 effective。

### 已知限制:`audit_project` → 422

当前 audit-ai v1.6 Milvus schema **无** `audit_project` 分区、无 `project_id`/`owner` 标量字段。
`corpus_types` 含 `audit_project` 时(无论是否带 project_id/owner)一律返回 `422`,**不静默零命中**
(静默零命中会把集成/配置问题伪装成「未找到依据」;检索后过滤则破前置过滤红线):

```json
{ "error": { "code": "VALIDATION_ERROR",
             "message": "audit_project 语料未接入 audit-ai 当前 Milvus schema,暂不支持" } }
```

schema 接入项目语料后一并放开(去掉 `_build_scope` 的守卫 + 加 `project_id`/`owner` 前置过滤)。

## 响应:JSON(`application/json`)

成功时在一次 HTTP 响应中返回完整结果；不使用 keep-alive、事件帧或长连接。结构如下：

```json
{
  "meta": {"request_id": "REQ-...", "route_type": "evidence", "ai_label": true,
            "review_required": false, "export_enabled": true, "elapsed_ms": 1234},
  "answer_blocks": [{"block_seq": 0, "block_type": "text", "content": "..."}],
  "citations": [{"clause_id": "...", "chunk_id": "...", "score": 0.91,
                 "source_code": "...", "source_doc_id": "..."}],
  "structured": {
    "regulations": {"total": 1, "items": []},
    "clauses": {"total": 2, "items": []},
    "regulatory_rules": {"total": 3, "items": []},
    "cases": {"total": 4, "items": []}
  },
  "completion": {"finish_reason": "stop", "confidence": 0.8, "exhausted_scope": []}
}
```

- `meta.elapsed_ms`：本次请求在 audit-ai 侧的耗时(毫秒,`time.perf_counter` 测量,含两次检索)。
- `answer_blocks`：答案块，`block_seq` 供 Java/前端保持顺序，`content` 为完整正文。
- `citations`：轻量引用。`clause_id`(=`chunk_id`)是回查主键；`source_code` 和 `source_doc_id`
  可为空，供 Java 侧授权回查时使用。拒答/闲聊等无引用的路由返回空数组。
- `completion.finish_reason`：`stop` 或 `refused`（覆盖感知拒答，不是错误）。
- `structured`：浏览器右侧四个结果 Tab 的权威结构化命中。法规、条款、监管规则命中项对达梦来源
  add-only 携带 `source_code`/`source_doc_id`，与 citation 语义相同；查询回答和结构化装配各自检索一次，
  但两次都在 Java 下传的同一 `corpus_types`/`perm_tags` 前置过滤范围内；不得为了装配 Tab
  绕过该 scope，也不得由 Java 将其替换为空数组。

- `citation.score`:per-hit 检索融合分,min-max 归一到 0–1,与前端 structured「匹配度」**同口径**
  (`structured.make_normalizer`)。**从 ask 的同一次检索候选派生**(经 `Retriever.scoped(collector=...)`
  收集),不为取分而额外检索,故与答案引用无候选集漂移。契约 `nullable`:极少数自建候选的路由取不到分
  → `null`,biz 降级不显。

### 每次请求做两次检索(`structured` 引入)

`_query_response` 先在 `scoped(collector=...)` 内跑 `agent.ask`(答案 + 引用 + 分数),**再另开一个
`scoped()` 跑 `structured_for`** 装配四 Tab —— 一次请求两次 Milvus 检索,延迟与检索成本约翻倍。

⚠ 这与 yaml v1.2.0 及本文早期措辞的「不二次检索」不符,**以本节为准**。两次检索都在 Java 下传的
**同一** `corpus_types`/`perm_tags` 前置过滤内(前置过滤红线不破);`citation.score` 仍只取第一次的
候选,故引用与分数同源。若日后要压回一次,得让 `structured` 复用第一次的 collector 候选(另议)。

### JSON 错误响应

生成失败返回 HTTP `500`：`{"error": {"code": "B105", "message": "生成失败"}}`
(不泄内部细节，堆栈进日志/trace)。

> **码已登记,载体已变**:`B105`(查询热路径内部错误,服务向)同 `B104` 属 B1xx **服务向**段,已在
> biz `boundary.v1.yaml` + `SPEC-BOUNDARY.md §3.3` 登记(audit-biz PR#6)。但 yaml 把它挂在
> **`QueryErrorEvent`(SSE 事件)** 下;改 JSON 后它走的是 HTTP 500 的统一 JSON 错误体。
> **码值不变、载体待回灌**(见文末清单)。

## 前置过滤覆盖:关掉按身份取数的 widening 桥接

前置过滤红线要求**所有**回给 Java 的内容都在授权集内。经 scope 的检索路(`retrieve`/
`retrieve_cases`/`retrieve_enumerate`)已带 corpus 门 + `perm_tag` 过滤;但内核另有两条**按 PG
身份精确取数、绕过 Milvus 前置过滤**的 widening 桥接,边界 scope 激活时**一律关闭**(前端/CLI 无
scope → 照走,byte 等价):

- **案例精确反查**(`case/bridge.cases_for_clauses`,`attach_cases` 用):全表扫 `cases.cited_regulations`
  按外规条款反查案例——scope 内关闭,否则调用方未请求/未授权 `case` 语料也会漏案例卡。
- **R5 桥接入口**(`judge/r5_judgment.resolve_cited_clauses`):把案例 `cited_regulations` 解析成外规条款
  chunk——scope 内关闭,否则漏出未受 corpus/`perm_tag` 约束的外规引用。

门控由 `retrieve.hybrid.scope_active()` 统一判定(边界请求期为真)。

## 与前端接口的关系

本接口与前端向 `/api/query/v1/*` **完全独立**:无会话落库、无导出、无 PG 引用回查装配。
前端接口的结构化载荷契约(`contract.py` 各 Hit)**不受本接口影响**、保持不变。

## 已知限制:热路径的 PG 依赖(现已扩大)

薄壳复用 `QueryAgent.ask`(不改内核),而生成正文依赖 PG **权威全文**(§7.3,非 Milvus 截断),
故边界热路径**仍读 PG**——与 boundary.v1.yaml「在线热路径不依赖 PG」措辞不符。

**范围比原先大**:`structured_for` 还会 `fetch_pg_context` 回查 chunk→doc 与案例行来装配四 Tab;
`/v1/cases/{case_id}` 与 `/v1/clauses/{clause_id}` 更是**直接**以 PG 为数据源。也就是说
「audit-ai 在线不依赖 PG」在当前实现下**整体不成立**,不再只是生成路径的残余。

红线本身仍守:**引用的四级定位由 Java 收口**——`/v1/query` 只回 `clause_id` + 轻量键,不在查询响应里
自行装配四级引用;`/v1/clauses` 是 Java **显式发起**、带 `perm_tags` 再校验的独立回查,不是 audit-ai
自作主张地 widening(区别见下节)。措辞待 biz yaml 修正,或本仓抽只读检索路(另议)。

## 本地联调

```bash
export AUDIT_AI_INTERNAL_TOKEN=dev-secret
.venv/bin/python -m uvicorn query.api.app:app --host 127.0.0.1 --port 8770
# GET /healthz 健康检查;POST http://127.0.0.1:8770/v1/query(带 X-Internal-Token)
```

端口非契约,运维/Java 可改;稳定路径是 `/v1/query`。

## 案例详情回查

查询响应中的案例卡使用 `case_id`（当前实现为案例 `doc_version_id`）作为回查主键。浏览器不能直接
调用 Python，而是经 Java 的 `GET /api/v1/regulation/cases/{case_id}`；Java 再调用：

```text
POST /v1/cases/{case_id}
X-Internal-Token: <令牌>
Content-Type: application/json

{"filters":{"perm_tags":["公开"]}}
```

成功时直接返回案例 JSON，至少含 `case_id`、`case_name`、`regulator`、`penalty_date`、
`violation_topic`、`related_regulation`、`core_issue`、`insight`、`full_text`、`source_url`。
Java 负责封装为既有浏览器 `Result<T>` 成功体。

该回查在 PostgreSQL 权威案例/文档版本/分块表中完成，`perm_tags` 必须再次校验；案例不存在和超出
授权范围均返回 `404`，不得据此泄露未授权案例的存在性。

## 达梦条款与监管规则全文回查

查询响应中的 `clause_id` 是浏览器详情路由键；同一条已授权命中携带
`source_code=LAW_CONTENT.CODE`、`source_doc_id=LAW_BASIC.CODE`。Java 缓存这对键后，浏览器仍经
`GET /api/v1/regulation/clauses/{clause_id}` 回查；Java 再以同一内部令牌调用：

```text
POST /v1/dm/clauses/{source_code}
X-Internal-Token: <令牌>
Content-Type: application/json

{"source_doc_id":"LAW_BASIC.CODE"}
```

audit-ai 按两个源 CODE 直读 `ZNFG_IAM_LAW_CONTENT JOIN ZNFG_IAM_LAW_BASIC`，连接串只从
`PRESEG_SOURCE_DSN` 获取（本地为 PG 仿真、内网为达梦）。成功时返回完整 `full_text`（并保留兼容字段
`text`）和源文档元数据，不能以结构化检索中的 140 字 `snippet` 或 `core_requirement` 代替原文。Java
不得接受浏览器传来的源 CODE，缓存缺失/过期必须提示重新查询，不能回退到 audit-ai PG 条款端点。

**主表空正文时回退详情表**:`dm_clause_detail` 与 `preseg.export` 同口径——`LAW_CONTENT.CONTENT`
为空则读 `LAW_CONTENT_DETAIL` 的 `CONTENT_TYPE=0` 段、按 `CONTENT_ORDER`/`ID` 拼接。真库在册主表正文
几乎全空(2026-07-29 内网复核),没有这条回退 `full_text` 恒空。详见 `docs/preseg-docs/preseg_devlog.md`。

## audit-ai PG 条款详情回查

```text
POST /v1/clauses/{clause_id}
X-Internal-Token: <令牌>
Content-Type: application/json

{"filters":{"perm_tags":["公开"]}}
```

`clause_id` = `chunk_id`。返回四级锚点(`clause_detail` 走 `generate/anchors`)+ `full_text`
(`chunks.text` 权威全文,并保留兼容字段 `text`)+ `parent_text`(节级父块)。

与上一节的**达梦**回查是两条不同数据源:本端点读 **audit-ai PG**,达梦端点读**源库**。命中来自达梦
预切块语料时优先用 `/v1/dm/clauses`(源库为准);本端点覆盖非达梦来源的语料。

`perm_tags` 由 Java 下传并在 PG 侧**再次校验**(比对 `doc_versions.perm_tag`);**不存在与越权一律
`404`**,不区分——否则详情端点会变成受限条款的存在性探测器。案例端点同此口径。

## yaml 待回灌清单(biz `boundary.v1.yaml` v1.2.0 → 下一版)

当前代码与契约单一源的**全部**差异,逐条列出以便一次性回灌(改 biz 的 yaml 是**另一个仓的 PR**,
本仓不代改;biz 仓本机副本当前在他人特性分支上,更不宜代动):

| # | yaml v1.2.0 现状 | 代码实际 |
|---|---|---|
| 1 | `/v1/query` 200 响应 = `text/event-stream`,`SseEventEnvelope`(`meta`/`delta`/`citation`/`done`/`error` 事件序) | `application/json` 一次性返回 `{meta, answer_blocks, citations, structured, completion}` |
| 2 | `meta` 无 `elapsed_ms` | 有 `meta.elapsed_ms`(整数毫秒) |
| 3 | 无 `structured` | 有 `structured`(四 Tab 结构化命中,`StructuredResult.to_dict()`) |
| 4 | `B105` 挂在 `QueryErrorEvent`(SSE 事件) | 走 HTTP 500 的统一 JSON 错误体,码值不变 |
| 5 | 未定义 `/v1/cases/{case_id}` | 已实现,body `{filters:{perm_tags}}`,404 兼表"不存在/越权" |
| 6 | 未定义 `/v1/clauses/{clause_id}` | 同上,返回四级锚点 + `full_text` + `parent_text` |
| 7 | 未定义 `/v1/dm/clauses/{source_code}` | 已实现,body `{source_doc_id}`,读 `PRESEG_SOURCE_DSN` 源库 |
| 8 | 描述称「在线热路径不依赖 PG」 | 不成立:生成正文、`structured` 装配、两个详情端点都读 PG(见上节) |
| 9 | 描述隐含单次检索 | 每次 `/v1/query` 做**两次**检索(答案 + structured) |

回灌后:把本文顶部的「本文为准」改回「以主本为准」,并同步
[`BOUNDARY-biz-contract-pointer.md`](BOUNDARY-biz-contract-pointer.md) 的版本号与状态。
