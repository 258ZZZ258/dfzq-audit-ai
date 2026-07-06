# audit-biz 调用 audit-ai 查询接口(边界二)说明

> 本文件是 audit-ai 侧的**落地/联调说明**。接口契约主本仍以 audit-biz 仓
> `docs/audit-biz-docs/openapi/boundary.v1.yaml`(v1.1.0)为准;本文与主本冲突时以主本为准。
> 相关红线见本仓 `docs/query-agent-docs/BOUNDARY-biz-contract-pointer.md`。

## 一句话

Java 后端 `audit-biz` 调 Python `audit-ai` 的**无状态、无身份**制度查询热路径,SSE 流式:

```text
POST /v1/query          # 只给 Java 后端调用;前端页面接口仍是 /api/query/v1/*(不受本接口影响)
```

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

## 响应:SSE(`text/event-stream`)

首先发一个 keep-alive 注释帧(`: keep-alive`,保 TTFB / 防代理空闲断连,非事件,按 SSE 规范忽略),
随后事件序:`meta` →(`delta`* + `citation`*)→ `done`;任意时刻异常 → `error` 后关闭。

| event | data(JSON)| 说明 |
|---|---|---|
| `meta` | `{request_id, route_type, ai_label, review_required, export_enabled}` | 首事件。`ai_label` 为**布尔**恒 `true`;`route_type` ∈ 八路;`review_required=true`(判定型)→ biz 渲染人工复核框 |
| `delta` | `{block_seq, block_type, text}` | 答案块。当前按契约允许的**整块下发**(`stream=false`),`block_seq` 供拼接 |
| `citation` | `{clause_id, chunk_id, score}` | **轻量引用**:`clause_id`(=`chunk_id`,回查主键)+ `score`。四级定位由 **Java 回查 PG 装配**(§8.2),边界**不带**回查字段。无引用的路由(拒答/闲聊)不发 |
| `done` | `{finish_reason, confidence, exhausted_scope}` | 末事件。`finish_reason`:`stop`/`refused`(覆盖感知拒答,非错误)|
| `error` | `{code, message}` | 见下 |

- `citation.score`:per-hit 检索融合分,min-max 归一到 0–1,与前端 structured「匹配度」**同口径**
  (`structured.make_normalizer`)。**从 ask 的同一次检索候选派生**(经 `Retriever.scoped(collector=...)`
  收集),**不做二次检索**,无候选集漂移。契约 `nullable`:极少数自建候选的路由取不到分 → `null`,biz 降级不显。

### error 事件错误码

生成失败发 `{"code": "B105", "message": "生成失败"}`(不泄内部细节,堆栈进日志/trace)。

> ⚠ **契约待批**:`B105`(查询热路径内部错误,服务向)沿用 `B104` 先例入 B1xx **服务向**段,
> 但**尚未回灌** biz `boundary.v1.yaml` / v0.4 §8.3 错误码体系。按「契约改动先改 biz 的 yaml,本仓照做」,
> 合并前需在 biz 侧登记该码(与 B104 同处理);biz 定稿码不同则本仓照改。

## 与前端接口的关系

本接口与前端向 `/api/query/v1/*` **完全独立**:无会话落库、无导出、无 PG 引用回查装配。
前端接口的结构化载荷契约(`contract.py` 各 Hit)**不受本接口影响**、保持不变。

## 已知限制:热路径的 PG 依赖

薄壳复用 `QueryAgent.ask`(不改内核),而生成正文依赖 PG **权威全文**(§7.3,非 Milvus 截断),
故边界热路径**仍读 PG**——与 boundary.v1.yaml「在线热路径不依赖 PG」措辞不符。红线本身(引用四级回查
归 Java)已守:边界只回 `clause_id`。此为「薄壳不改内核」决策的既定残余,**待 biz yaml 修正措辞**
或本仓抽只读检索路(另议),本 PR 不改内核。

## 本地联调

```bash
export AUDIT_AI_INTERNAL_TOKEN=dev-secret
.venv/bin/python -m uvicorn query.api.app:app --host 127.0.0.1 --port 8770
# GET /healthz 健康检查;POST http://127.0.0.1:8770/v1/query(带 X-Internal-Token)
```

端口非契约,运维/Java 可改;稳定路径是 `/v1/query`。
