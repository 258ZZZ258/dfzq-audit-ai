# 边界契约指针:audit-ai query API ↔ audit-biz 主本

> **本文件是指针(pointer),不是主本。** audit-ai 对 audit-biz 的服务契约(边界二)+ 对账/整改方案的
> **权威主本在 audit-biz 仓**;本仓只留指针,防「两份漂移」(v0.4 §15「以 CP 回灌」)。
>
> ⚠ **当前处于「代码先行、契约待回灌」状态(2026-07)**:javainit ↔ audit-ai 当面谈定把 `/v1/query`
> 从 SSE 改为一次性 JSON 并加了三个详情端点,**代码已落地,biz `boundary.v1.yaml`(v1.2.0)尚未回灌**。
> 这条路径**绕过了**「先改 biz yaml、本仓照做」的常规流程——记录在案,不作为新惯例。
> 期间以 [`BOUNDARY-v1-query-api.md`](BOUNDARY-v1-query-api.md) 描述实际行为(**不要按 yaml 恢复 SSE**),
> 全部差异见该文「yaml 待回灌清单」9 条;回灌后本节与版本号一并改回「以主本为准」。

## 权威来源(audit-biz 远端)

- 仓库:`https://github.com/258ZZZ258/audit-biz`(SSH: `ssh://git@ssh.github.com:443/258ZZZ258/audit-biz.git`)
- **边界契约(规范单一源)**:`docs/audit-biz-docs/openapi/boundary.v1.yaml`(**v1.2.0**;
  v1.2 增定 `/v1/documents:process`。⚠ 其 `/v1/query` 仍写 SSE,见顶部待回灌说明)
  - https://github.com/258ZZZ258/audit-biz/blob/main/docs/audit-biz-docs/openapi/boundary.v1.yaml
- **语义主本**:`docs/audit-biz-docs/SPEC-BOUNDARY.md`
- **对账 + 整改方案(本仓要照做的完整方案)**:`docs/audit-biz-docs/BOUNDARY-RECONCILIATION-001.md`
  - https://github.com/258ZZZ258/audit-biz/blob/main/docs/audit-biz-docs/BOUNDARY-RECONCILIATION-001.md

## 背景(为什么有这份指针)

audit-ai 的 query-api(PR#39,已并 `main`)是对着产品原型**直连前端**的会话式富 API
(`POST /api/query/v1/conversations/{cid}/messages` + `/clauses` + `/exports`,`auth.py` 带 subject/role 身份,
新建 `query_*` 会话表),**与 audit-biz 冻结的边界契约 CP-A 漂移**。
用户决策(2026-07-02):**守边界**——audit-ai 在现有 `QueryAgent.ask` 上加**薄壳** `POST /v1/query`,**不改 AI 内核**
(漂移在 HTTP 薄壳、不在域逻辑;`contract.py` 的 `QueryResult/AnswerBlock/Citation` 本就是 CP-A 对齐对象)。

## 本仓做了什么(已落地;联调说明见 `BOUNDARY-v1-query-api.md`)

`query/query/api/routes_boundary.py` 现有**四个**端点(全部 `X-Internal-Token`、无身份):
`POST /v1/query` 薄壳,加三个详情回查 `POST /v1/cases/{case_id}`(读 PG)、
`POST /v1/clauses/{clause_id}`(读 PG)、`POST /v1/dm/clauses/{source_code}`(读达梦源库)。
`/v1/query` 的性质:

1. `X-Internal-Token` 静态共享密钥鉴权、**无身份**(`auth.require_internal_token`,常数时间比较、
   env 未配 fail-closed;**不**复用 `auth.py` 的 subject/role)。
2. 请求 `filters{perm_tags, corpus_types, project_id, owner}` → 经 `Retriever.scoped()` 构 **Milvus 前置过滤**
   (检索**前**生效,红线:算在 Java、用在 Python)。`perm_tag` 过滤走 `r4_listing.array_any_expr` 加固构造
   (白名单字段 + json 转义,防注入);`audit_project` 未接入 schema → **显式 422**(不静默零命中)。
3. 当前边界使用一次性 JSON:`meta / answer_blocks / citations / structured / completion`;`citation`
   的 per-hit `score` 从 ask **同一次检索**候选派生(`scoped(collector=...)`),归一同
   `structured.make_normalizer`。⚠ **每次请求实际做两次检索**(答案一次、`structured` 装配一次),
   两次都在 Java 下传的同一前置过滤 scope 内;详见 `BOUNDARY-v1-query-api.md` 同名小节。
4. `request_id` 经 `QueryAgent.ask(trace_id=...)` 注入 Langfuse trace。
5. 会话 / 身份 / 导出**不进边界**(归 biz);`QueryAgent.ask` 域逻辑**原样复用,不改内核**。
   前端向 `/api/query/v1/*` 契约(`contract.py` 各 Hit)**不受影响**。
   ⚠ 「PG 引用回查不进边界」**已不再成立**:`/v1/cases`、`/v1/clauses` 就是 Java 显式发起的 PG 详情
   回查(带 `perm_tags` 再校验)。红线里守住的是**四级引用装配由 Java 收口**——`/v1/query` 响应本身
   仍只回 `clause_id` + 轻量键,不自行装配四级引用。

差异对照 BR-1~8 见 `BOUNDARY-RECONCILIATION-001.md §2`。

> **待同步远端契约项**:完整 9 条差异见 `BOUNDARY-v1-query-api.md`「yaml 待回灌清单」——含 SSE→JSON、
> 三个新端点、`structured`/`elapsed_ms` 新字段、`B105` 载体从 SSE 事件改为 JSON 错误体、
> 以及 yaml「在线热路径不依赖 PG」措辞已整体不成立。改 yaml 是 **biz 仓的 PR**,本仓不代改。

## 双向引用坐标(remote ↔ remote)

| 方向 | 位置 |
|---|---|
| audit-ai → audit-biz | 即本文件;引 `https://github.com/258ZZZ258/audit-biz` 的 `boundary.v1.yaml` + `BOUNDARY-RECONCILIATION-001.md` |
| audit-biz → audit-ai | `BOUNDARY-RECONCILIATION-001.md §7/§8`;远端坐标 `https://github.com/258ZZZ258/audit-ai`,落地代码 `query/query/api/routes_boundary.py` |
