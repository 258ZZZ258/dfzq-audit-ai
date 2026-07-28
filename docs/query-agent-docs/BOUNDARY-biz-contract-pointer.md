# 边界契约指针:audit-ai query API ↔ audit-biz 主本

> **本文件是历史指针(pointer),不是当前本地联调主本。** audit-ai 对 audit-biz 的服务契约(边界二)+ 对账/整改方案的
> **权威主本在 audit-biz 仓**;本仓只留指针,防「两份漂移」(v0.4 §15「以 CP 回灌」)。
> 远端 `boundary.v1.yaml` 仍记录旧版本。当前 `javainit ↔ audit-ai` 联调已切换为 JSON，
> 以 [`PI-AGENT-REFACTOR-HANDOFF.md`](PI-AGENT-REFACTOR-HANDOFF.md) 和
> [`BOUNDARY-v1-query-api.md`](BOUNDARY-v1-query-api.md) 为准；不要按本指针恢复 SSE。

## 权威来源(audit-biz 远端)

- 仓库:`https://github.com/258ZZZ258/audit-biz`(SSH: `ssh://git@ssh.github.com:443/258ZZZ258/audit-biz.git`)
- **边界契约(规范单一源)**:`docs/audit-biz-docs/openapi/boundary.v1.yaml`(**v1.1.0**)
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

已在 `query/query/api/routes_boundary.py` 新增薄壳 `POST /v1/query`:

1. `X-Internal-Token` 静态共享密钥鉴权、**无身份**(`auth.require_internal_token`,常数时间比较、
   env 未配 fail-closed;**不**复用 `auth.py` 的 subject/role)。
2. 请求 `filters{perm_tags, corpus_types, project_id, owner}` → 经 `Retriever.scoped()` 构 **Milvus 前置过滤**
   (检索**前**生效,红线:算在 Java、用在 Python)。`perm_tag` 过滤走 `r4_listing.array_any_expr` 加固构造
   (白名单字段 + json 转义,防注入);`audit_project` 未接入 schema → **显式 422**(不静默零命中)。
3. 当前边界使用一次性 JSON：`meta / answer_blocks / citations / completion`；`citation` 的 per-hit
   `score` 从 ask **同一次检索**候选派生(`scoped(collector=...)`)，**不二次检索**；归一同 `structured.make_normalizer`。
4. `request_id` 经 `QueryAgent.ask(trace_id=...)` 注入 Langfuse trace。
5. 会话 / 身份 / PG 引用回查 / 导出**不进边界**(归 biz);`QueryAgent.ask` 域逻辑**原样复用,不改内核**。
   前端向 `/api/query/v1/*` 契约(`contract.py` 各 Hit)**不受影响**。

差异对照 BR-1~8 见 `BOUNDARY-RECONCILIATION-001.md §2`。

> **待同步远端契约项**:JSON 错误 `error.code=B105`(查询热路径内部错误,服务向)沿用 B104 先例入 B1xx 服务向段，
> **需回灌** biz `boundary.v1.yaml` / v0.4 §8.3 后定稿;热路径 PG 依赖与 yaml「不依赖 PG」措辞的残余待修
> (见 `BOUNDARY-v1-query-api.md` 两节「已知限制」)。

## 双向引用坐标(remote ↔ remote)

| 方向 | 位置 |
|---|---|
| audit-ai → audit-biz | 即本文件;引 `https://github.com/258ZZZ258/audit-biz` 的 `boundary.v1.yaml` + `BOUNDARY-RECONCILIATION-001.md` |
| audit-biz → audit-ai | `BOUNDARY-RECONCILIATION-001.md §7/§8`;远端坐标 `https://github.com/258ZZZ258/audit-ai`,落地代码 `query/query/api/routes_boundary.py` |
