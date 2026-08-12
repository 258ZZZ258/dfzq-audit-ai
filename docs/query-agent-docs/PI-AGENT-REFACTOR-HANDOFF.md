# Pi Agent 重构交接：制度查询 / 比对智能体

> 状态：当前开发基线（2026-07-27）。Pi Agent 开始重构前先读本文件、
> [`BOUNDARY-v1-query-api.md`](BOUNDARY-v1-query-api.md) 与仓库根 `README.md`。

## 1. 这次重构要解决什么

本仓是制度查询智能体和后续制度比对智能体的 Python 侧实现。当前已经有一条可用的
“浏览器 → Java → audit-ai → Milvus/PG”制度查询链路；Pi Agent 可以重构内部组织、检索和回答能力，
但必须保持 Java 边界可联通，不能把浏览器直接接到 Python。

当前**尚未实现真实的制度比对**：`javainit/idap-ui` 里的比对交互仍为 mock。不要把现有页面上的
比对进度或结果当作 audit-ai 已具备的功能。

## 2. 当前真实调用链

```text
idap-ui（浏览器）
  POST /api/v1/regulation/queries  application/json
        ↓
javainit（鉴权、授权范围、浏览器契约收口）
  POST http://<audit-ai>/v1/query  application/json + X-Internal-Token
        ↓
audit-ai query/api/routes_boundary.py
  QueryAgent.ask + Retriever.scoped（Milvus 前置过滤）
        ↓
PostgreSQL（权威全文/会话等） + Milvus（混合检索）
```

案例卡详情是同一安全边界的一条短请求：浏览器 `GET /api/v1/regulation/cases/{case_id}` →
Java `POST /v1/cases/{case_id}`（携带预计算 `perm_tags`）→ audit-ai PostgreSQL 权威详情。
`case_id` 当前等于案例文档版本 ID；Python 对该权限范围再次校验，未找到与无权访问统一返回 `404`。

条款和监管规则的“查看原文/查看详情”已改为达梦源库回查：`/v1/query` 中与 `clause_id` 同行返回
`source_code`（`LAW_CONTENT.CODE`）和 `source_doc_id`（`LAW_BASIC.CODE`）。Java 只缓存同一份、已按
权限过滤的查询响应中的这对键（默认 10 分钟），浏览器仍只传 `clause_id`：浏览器
`GET /api/v1/regulation/clauses/{clause_id}` → Java
`POST /v1/dm/clauses/{source_code}`（body: `source_doc_id`）→ audit-ai 通过
`PRESEG_SOURCE_DSN` 直读达梦/本地 PG 仿真源库。`full_text` 必须是完整源条文，不得复用结构化检索
结果里的 140 字摘要。缓存缺失或过期要求重新查询；浏览器、Java 都不持有达梦连接串或驱动。

`POST /v1/clauses/{clause_id}` 仍是 audit-ai 旧会话/API 的 PostgreSQL 回查兼容路径，Java 不得再调用它。

- 浏览器不持有、不发送 `AUDIT_AI_INTERNAL_TOKEN`。
- `X-Internal-Token` 只证明调用方是 Java，不代表终端用户身份。
- Java 预计算 `perm_tags` 和 `corpus_types`；Python 必须在**检索前**把它们下推到 Milvus，不能改为
  检索后过滤。
- 当前本地语料主要是 `P-EXT` 与 `P-CASE`；重构时不要假定 `P-INT`、`P-QA` 已有数据。

## 3. 已确认的接口契约：只用 JSON，不用 SSE

### 3.1 Java 调 audit-ai

`POST /v1/query`，请求：

```json
{
  "query": "证券法中信息披露有哪些要求",
  "request_id": "UUID",
  "filters": {
    "perm_tags": ["公开"],
    "corpus_types": ["external", "case"]
  },
  "options": {"include_superseded": false}
}
```

成功响应必须为 `200 application/json`：

```json
{
  "meta": {
    "request_id": "UUID",
    "route_type": "evidence",
    "ai_label": true,
    "review_required": false,
    "export_enabled": true
  },
  "answer_blocks": [
    {"block_seq": 0, "block_type": "text", "content": "完整回答"}
  ],
  "citations": [
    {
      "clause_id": "chunk-id",
      "chunk_id": "chunk-id",
      "score": 0.91,
      "source_code": null,
      "source_doc_id": null
    }
  ],
  "structured": {
    "regulations": {"total": 1, "items": []},
    "clauses": {"total": 2, "items": []},
    "regulatory_rules": {"total": 3, "items": []},
    "cases": {"total": 4, "items": []}
  },
  "completion": {
    "finish_reason": "stop",
    "confidence": 0.8,
    "exhausted_scope": []
  }
}
```

失败统一为普通 JSON 错误体，例如：

```json
{"error": {"code": "B105", "message": "生成失败"}}
```

`/v1/query` 的实现位置是 `query/query/api/routes_boundary.py`；对应测试是
`query/tests/test_api_boundary.py`。

`structured` 是制度查询页面右侧四个 Tab 的数据来源。Pi 重构时可替换其内部检索和装配实现，
但必须保留四个 Tab、`total/items` 形状，并让结构化检索和回答检索使用同一 Java 下传权限 scope。
Java 的浏览器适配层负责将 `regulatory_rules` 映射为页面的 `rules`，不能再把这些命中固定清空。

### 3.3 案例详情回查

`POST /v1/cases/{case_id}`，请求体仅携带 Java 预计算的权限范围：

```json
{"filters": {"perm_tags": ["公开"]}}
```

成功返回完整案例 JSON（`case_id`、`case_name`、`full_text` 等），不包 SSE、不持久化会话；Java
将其转换为浏览器的 `Result<T>`。这条接口与 `/v1/query` 共用 `X-Internal-Token` 校验，且必须继续将
“不存在”和“无权限”统一为 `404`。对应接口契约测试也在 `query/tests/test_api_boundary.py`。

### 3.4 达梦条款与监管规则详情回查

`POST /v1/dm/clauses/{source_code}`，请求体：

```json
{"source_doc_id": "LAW_BASIC.CODE"}
```

该接口需要 `X-Internal-Token`，从 `ZNFG_IAM_LAW_CONTENT` 与 `ZNFG_IAM_LAW_BASIC` 按两个 CODE 精确
关联并返回 `source_code`、`source_doc_id`、文档元数据、`full_text` 和兼容字段 `text`。数据源仅从
`PRESEG_SOURCE_DSN` 读取；本地环境它是端口 5434 的 PG 仿真，内网部署时是达梦 DSN。为保持 Java
零新增 JDBC 依赖，达梦驱动只由 audit-ai Python 运行环境提供。

**正文来源不可简化。**2026-07-28 对真实达梦在册数据的抽样统计显示，`LAW_CONTENT.CONTENT` 在
445,477 条记录中有 445,475 条为空；可读正文位于
`ZNFG_IAM_LAW_CONTENT_DETAIL.CONTENT`。因此，预切块导出和这个详情接口都必须遵循同一规则：

1. 主表 `CONTENT` 非空时优先使用它，保持旧数据与 PG 仿真的兼容；
2. 主表为空时，以 `LAW_CONTENT_CODE = LAW_CONTENT.CODE` 关联详情表，只取未删除的
   `CONTENT_TYPE = 0` 文本段，按 `CONTENT_ORDER, ID` 拼接；
3. `CONTENT_TYPE = 1/2`（图片/视频）不进入当前纯文本检索或 `full_text`；同一条款、同一顺序而
   文本不同视为源数据冲突，必须报错，不能任意选一条；
4. `LAW_CONTENT.CODE` 仍是检索锚和 Java 回查键，不能用详情行 `ID` 替换它。

Pi Agent 若替换 source adapter、详情端点或摄取流程，必须保留这套回退逻辑，并用“主表正文为空、详情
多段乱序、图片和删除段存在”的测试样例锁住它。

浏览器仍调用 Java 的 `GET /api/v1/regulation/clauses/{clause_id}`。Java 必须仅接受自己从受权限约束
查询响应缓存的 `clause_id → source_code/source_doc_id` 映射，不能让浏览器提交这两个源键，也不能回退到
audit-ai PG 的 `/v1/clauses/{clause_id}`。结构化三个法规 Tab 中的命中项也必须 add-only 携带这两个源键，
否则其 `clause_id` 不在答案引用里时无法安全回查。

### 3.2 不要恢复 SSE

2026-07-27 已移除：

- `/v1/query` 的 `text/event-stream`、keep-alive 和事件帧；
- 原会话接口按 `Accept: text/event-stream` 分支的流式行为；
- `query/query/api/sse.py` 和专用 SSE 测试。

原因：前端、Java、Python 三层各自维护事件终态，曾造成“已出结果但浏览器持续转圈”的不一致。
完整 JSON 有单一成功/失败语义，且 Java 不会再把 Hutool 的 `JSONNull` 写入 SSE 序列化器。

若未来真实制度比对需要分钟级处理，采用**异步任务 + 轮询**，不要恢复 SSE：

1. `POST /v1/comparisons` 返回 `202`、`task_id`、`queued`；
2. `GET /v1/comparisons/{task_id}` 返回 `queued/running/succeeded/failed/cancelled`；
3. 成功后返回完整 JSON 结果；提供取消接口和幂等键；
4. Java 再把任务状态/结果转为浏览器契约。

这能正确处理刷新页面、断网、服务重启、取消与重试。

### 3.3 外规逐条圈定内规的并发边界

2026-08-12 起，`query.retrieve.hybrid.Retriever.retrieve_batch()` 提供了制度比对可复用的第一段能力：
对一份上传外规的多个条款，**一次批量嵌入**后，以受限线程池并行检索 `P-INT` 内规分区；默认并发为
`4`，可由 `QUERY_BATCH_RETRIEVE_CONCURRENCY` 覆盖。返回顺序与上传条款顺序一致，单条嵌入/检索失败
只标记该条，不中断整份文档。

不得将本地 `BAAI/bge-m3` 的推理置于多线程：它是共享模型、内存压力大。可并行的是完成向量化之后的
Milvus 查询；重排也应保留在调用线程。真实比对任务落地时，先通过此方法圈定每条外规的内规候选，再做
覆盖判定；不要把 `P-EXT` 外规候选混入该阶段。

## 4. 重构不可破坏的约束

1. 不新增运行时或构建依赖，除非项目负责人明确批准。
2. 不向浏览器暴露内部令牌、数据库连接串、Milvus 地址或原始异常堆栈。
3. `Retriever.scoped()` 的 corpus 和 `perm_tag` 前置过滤必须覆盖答案与引用的全部检索路径；
   `audit_project` 在当前 schema 未接入时必须继续显式 `422`，不能静默返回零命中。
4. 保留 `request_id → QueryAgent.ask(trace_id=...)` 的可观测性传递。
5. 保持 BM25 混合检索：本地栈使用支持原生 BM25 的 Milvus `v2.5.27` 与 `BAAI/bge-m3`。
6. 不修改 `javainit/idap-ui`，除非项目负责人再次明确授权；Python 重构的浏览器契约变更必须先同步 Java。
7. 不把 mock 制度比对伪装成已接入的 AI 功能。

## 5. 建议的 Pi Agent 重构顺序

1. 先写目标架构和接口迁移计划，列明哪些 `QueryAgent` 行为、引用字段、过滤规则必须兼容。
2. 为 `/v1/query` 当前 JSON 契约保留契约测试，再重构内部模块；先绿后迁移。
3. 将真实制度比对独立为异步任务域，不与查询端点混用，也不复用前端 mock。
4. 以真实最小语料做端到端验证：外规查询、案例查询、空结果、拒答、过滤拒绝、内部令牌错误。
5. 只有在 Java 和前端明确完成配套改造后，才允许版本化变更边界响应。

## 6. 本地运行与验证

环境变量至少需要：`AUDIT_AI_INTERNAL_TOKEN`、`PIPELINE_DB_DSN`、`PIPELINE_MILVUS_HOST`、
`PRESEG_SOURCE_DSN` 和嵌入模型相关 `PIPELINE_EMBEDDING_*`；离线 BGE 模型见根 `README.md`。

```bash
PYTHONPATH='pipeline:libs/common:query:eval' \
  .venv/bin/python -m pytest query/tests/test_api_boundary.py query/tests/test_api_ask.py -q

PYTHONPATH='pipeline:libs/common:query:eval' \
  .venv/bin/python -m uvicorn query.api.app:app --host 127.0.0.1 --port 8771
```

联调完成条件：`POST /v1/query`、`POST /v1/cases/{case_id}`、`POST /v1/dm/clauses/{source_code}`、Java
`/api/v1/regulation/queries`、`/api/v1/regulation/cases/{case_id}`、`/api/v1/regulation/clauses/{clause_id}`
和前端 Vite 代理均返回
`200 application/json`；任何一段都不得返回 `text/event-stream`。
