# SPEC:对话上传文档 · 按需处理(context-first MVP)

> 状态:**已批准(2026-07-09,humbletoaim)**。契约段(§5)**待同步 audit-biz**(`boundary.v1.yaml` /
> `SPEC-BOUNDARY.md`),biz 对齐后开实现。本设计在 audit-ai 侧先定,契约主本仍回归 biz。
>
> 关联:[[audit-ai-role-in-java-topology]] 边界红线;`docs/query-agent-docs/BOUNDARY-biz-contract-pointer.md`(边界指针);
> `docs/predeploy-docs/predeploy_devlog.md`(同工作树,内网预部署对齐线)。

## 1. 背景:文件处理管线的定位重构

甲方**自维护知识库且已足够结构化** → 文件处理管线**批次建库**角色收缩(权威语料经甲方结构化 KB /
preseg 通道入,不再是主职)。管线**新增主职**:处理**用户在对话框上传的文件**。

- 触发链:用户对话框上传 → **Java(audit-biz)读取 + 校验 → 传 MinIO** → Java 调 audit-ai
  `POST /v1/documents:process`(带 object_key)→ audit-ai 从 MinIO 拉对象处理。
- 守边界:与前端交互(上传 UI)**全走 Java**;audit-ai 只拿 object_key 做**无身份·无状态**解析。

## 2. 核心设计洞察(为什么是 context-first)

上传文档做问答/总结/抽取,**只要文档塞得进 LLM 上下文窗**就**不需要向量检索**:

- 中间产物 = **解析后的结构化切块(带条款路径)**,直接作 LLM 上下文;用**结构化**(非原始文本)
  → LLM 能按条引用,正是本管线解析强项。
- **零向量库、零检索、零 TTL 运维,audit-ai 纯无状态解析器**;唯一中间件 = **MinIO**(已有)。

### 否决的方案(记录"为什么不那样")

- ❌ **上传文档落权威语料库 / 权威 PG**:用户上传=瞬态·权限敏感,与 audit-ai 无状态 + 红线
  「会话/导出不落本仓」冲突。
- ❌ **为上传向量新建向量中间件**(Redis/Qdrant/临时 Milvus 集合):context-first 下**根本不需要**
  存上传文档的向量。仅两种情况才碰向量,且**都不需新增中间件**:①制度比对=拿文档 chunks 当
  **query 打现成权威 Milvus 语料库**(向量瞬时用,不存);②超大文档 RAG=对 MinIO 自包含产物里
  预算向量做**请求内 brute-force**。二者均为 **post-MVP**(§8)。
- ❌ **audit-ai 进程内持会话状态**:产物一律落 MinIO 中间件,进程零状态。

## 3. 范围(MVP)

**做**:MinIO 对象 → 解析 → 结构化切块 → **结构化产物落 MinIO** → 返回元数据。产物供下游作
LLM 上下文(问答/总结/抽取)。

**不做(post-MVP,§8)**:向量嵌入 / 制度比对 / 超大文档 brute-force RAG / 异步 job / SSE 进度 /
多租户配额。**不写权威 PG、不进状态机、不跑版本链·META·finalize·T2/T4。**

## 4. 架构与处理轻链

```
Java(前端交互 + 上传归 Java):
  用户上传 → 校验(类型白名单/大小)→ MinIO put  {uploads_bucket}/upload/{upload_id}/{filename}
           → POST /v1/documents:process { object_key, upload_id, filename }   (X-Internal-Token,同步)
audit-ai(无状态轻链,复用 stage 核心但不经 PG 状态机):
  1. 从 MinIO 拉 raw bytes(新增 MinIO ObjectStore 后端)
  2. 类型识别 + 白名单(PDF/Word/Excel;非白名单 → 415)
  3. S1 解析(ParserAdapter)→ IR(docx→PDF 渲染走 soffice;页锚可选)
  4. S3-lite 结构化切块(profile_router;默认条款树,corpus_hint 可选)→ chunks(带 clause_path)
  5. 渲染 markdown(供直接塞上下文)
  6. 产物写 MinIO  {uploads_bucket}/artifact/{upload_id}.json
  7. 返回 { upload_id, artifact_key, title, page_count, chunk_count, status }
下游(Java 编排):
  取 artifact(chunks/markdown)→ 作 LLM 上下文(问答/总结/抽取)
清理:MinIO 对象生命周期策略(TTL)/ Java 显式 DELETE
```

### 4.1 中间产物格式(`artifact/{upload_id}.json`)

```jsonc
{
  "upload_id": "01J...",
  "source": {"filename": "x.pdf", "object_key": "upload/01J.../x.pdf",
             "sha256": "...", "content_type": "application/pdf"},
  "doc": {"title": "某某管理办法", "page_count": 12, "chunk_count": 34},
  "chunks": [
    {"seq": 0, "clause_path": "第一章 总则/第一条", "chunk_type": "clause",
     "text": "…", "page": 1}
  ],
  "markdown": "# 某某管理办法\n\n## 第一章 总则\n第一条 …\n"   // 直接可塞上下文
}
```

- `chunks`:结构化、可按条引用/选择性取用;`markdown`:整篇渲染,供直接 context stuffing。
- **不含向量**(MVP);post-MVP 比对/大文档时才在产物加 `chunks[].embedding`。

## 5. 边界契约(供同步 audit-biz —— 契约单一源仍在 biz 仓)

> 本段是**待同步项**。同步后契约主本回归 biz `boundary.v1.yaml`,本仓照做。

### 5.1 新端点 `POST /v1/documents:process`

- **鉴权**:`X-Internal-Token`(复用 `auth.require_internal_token`,常数时间比较,env 未配 fail-closed;
  **无身份**,不传 JWT/subject/role)。
- **请求体**:
  | 字段 | 必填 | 说明 |
  |---|---|---|
  | `object_key` | 是 | Java 上传到 MinIO 的 raw 对象 key |
  | `upload_id` | 是 | Java 生成的句柄(ULID);= 产物 key + 幂等键 |
  | `filename` | 是 | 原文件名(类型/扩展名识别) |
  | `corpus_hint` | 否 | internal/external/qa/case → 选切块 profile;缺省通用条款树 |
- **响应(同步 JSON)**:`{ upload_id, artifact_key, title, page_count, chunk_count, chunk_types, status }`。
  产物本体在 MinIO(Java 按 `artifact_key` 自取),响应轻。
- **错误(服务向,B1xx 段,待 biz 定码)**:415 非白名单类型 · 413 超大小上限 · 422 object_key 不存在/
  拉取失败 · 500 解析失败(带阶段码)。
- **幂等**:同 `upload_id` 重复调 → 复用/覆盖同产物(MinIO artifact 写一次或幂等覆盖)。

### 5.2 MinIO key 约定(Java ↔ audit-ai 共识)

- raw 上传(Java put):`upload/{upload_id}/{filename}`
- 产物(audit-ai put):`artifact/{upload_id}.json`
- bucket:`{uploads_bucket}`(env 配置,与 Milvus 用的 MinIO 可同实例不同 bucket)。

### 5.3 职责划分(「算在 Java、用在 Python」)

| 归 Java | 归 audit-ai |
|---|---|
| 前端上传交互、鉴权/授权、文件类型/大小校验、put MinIO、生成 upload_id、生命周期/清理、编排下游任务 | 拉对象、解析、结构化切块、写产物、返回元数据 |

### 5.4 红线核对

- [x] 单向·无身份·`X-Internal-Token`  [x] audit-ai 无状态(产物落 MinIO 中间件,零进程/PG 状态)
- [x] 与前端交互走 Java  [x] 会话/导出不落本仓(无会话存储;产物 per-upload 瞬态、Java/TTL 清)
- [x] 不传身份/JWT  [x] 不回查 PG 装 citation(MVP 无 citation 回查)

## 6. 实现改造点(复用优先)

- **MinIO ObjectStore 后端**:现 `ObjectStore` 为本地 FS(key 布局已对齐 MinIO)。新增 S3/MinIO
  适配(`minio` SDK 或 boto3),`from_config` 按 `object_store.backend`(local|minio)选;env 配
  endpoint/bucket/creds(**绝不入库**)。
- **PG-free 解析/结构化入口**:现 stage `run(ctx, dvid)` 读写 PG 状态。抽出 S1/S3 的**纯核心**
  (parse bytes→IR、IR→chunks),用**合成 doc handle** `upload:{upload_id}` 当身份跑,不建 doc_versions
  行、不写 pipeline_events。新增 `pipeline/pipeline/ondemand/process.py` 编排轻链。
- **端点**:`POST /v1/documents:process` 落 query API(`routes_boundary.py` 同款鉴权),或新
  `routes_documents.py`。替换漂移的 `routes_misc.py::/uploads`(只存不消费)。
- **配置**:`[object_store]` 加 backend/minio 段;`[uploads]` 加白名单类型/大小上限(复用 query 的
  50MB / PDF·Word·Excel)。

## 7. 测试策略(TDD)

- 单元(零栈):MinIO 后端(mock S3)· 轻链 parse→structure→artifact(小样例 golden)· 类型白名单/
  大小拒绝 · 幂等(同 upload_id)· 鉴权(缺 token fail-closed)。
- 集成(真栈,合并前):真 MinIO put→process→artifact 端到端;真解析器对 mini golden 文档结构 F1。

## 8. Post-MVP(接缝预留,不在本期)

- **制度比对**:文档 chunks 作 query → 打权威 Milvus 语料库(复用检索 + 刚落地的嵌入 endpoint)。
- **超大文档 RAG**:产物加预算向量;请求内 brute-force(单文)或升临时向量库(仅超大/高并发才评)。
- **异步 job + SSE 进度**:大文件慢解析(渲染/OCR);job 态归 Java 或短暂存。
- **多路产物**:同一 artifact 适配更多任务(要素抽取/风险点)。

## 9. 待定(需与 Java 同事 / biz 定)

- MinIO bucket 命名 + 是否与 Milvus MinIO 同实例;凭证注入方式(内网)。
- 清理归属:Java 显式 DELETE vs MinIO lifecycle TTL(建议二者都留,TTL 兜底)。
- 错误码 B1xx 具体值(回灌 biz `boundary.v1.yaml` §8.3 服务向段)。
- 漂移端点 `/uploads` 是本期移除还是并存过渡。
- 是否需要 audit-ai 回传 `markdown` 之外的"就绪上下文串",还是 Java 自拼 chunks。
