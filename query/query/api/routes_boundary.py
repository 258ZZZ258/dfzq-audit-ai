"""audit-biz → audit-ai 边界二:无状态 ``POST /v1/query`` SSE 薄壳(boundary.v1.yaml v1.1.0)。

独立于前端向 ``/api/query/v1/*`` 会话式 API:无用户身份、无会话落库、无导出;引用只回轻量
标识(clause_id/chunk_id/score),四级回查归 Java(§8.2 收口)。Java 预计算的 ``filters`` 经
``Retriever.scoped`` 下推为 Milvus **前置过滤**(检索**前**生效,红线:算在 Java、用在 Python);
per-hit 分数从**同一次**检索的候选收集槽派生,不二次检索。

已知限制(详见 ``docs/query-agent-docs/BOUNDARY-v1-query-api.md``):薄壳不改 AI 内核 →
正文生成仍依赖 PG 权威全文(契约「热路径不依赖 PG」措辞待修);``delta`` 按契约允许的
整块下发(首帧 keep-alive 注释帧保 TTFB)。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from query.api.auth import require_internal_token
from query.api.errors import validation_error
from query.api.service import QueryService, get_service
from query.api.sse import KEEPALIVE, format_sse
from query.api.structured import _display_score, make_normalizer
from query.contract import RouteType
from query.listing.r4_listing import array_any_expr

router = APIRouter(tags=["boundary"])

#: 边界 corpus_types → Milvus corpus_type 分区值(audit_project 未接入 → 422,见 ``_build_scope``)。
_CORPUS_MAP = {"internal": "P-INT", "external": "P-EXT", "qa": "P-QA", "case": "P-CASE"}
#: SSE ``error`` 事件码:查询热路径内部错误(服务向)。同 ``B104`` 属 B1xx 服务向段;
#: 已在契约单一源登记(biz ``boundary.v1.yaml`` QueryErrorEvent + SPEC-BOUNDARY §3.3,
#: audit-biz PR#6)。合并顺序:biz 登记先行 → 本仓照做。
_ERR_INTERNAL = "B105"


class BoundaryFilters(BaseModel):
    """Java jCasbin 预计算过滤位(boundary.v1.yaml ``Filters``)。

    ``perm_tags`` 空数组=无额外限制(契约明文,非 fail-open);``owner``/``project_id`` 仅
    audit_project 语义,制度语料按契约忽略。``corpus_types`` 空没有可检分区 → 直接 422。
    """

    perm_tags: list[str]
    corpus_types: list[
        Literal["internal", "external", "qa", "case", "audit_project"]
    ] = Field(..., min_length=1)
    project_id: str | None = None
    owner: str | None = None


class BoundaryOptions(BaseModel):
    top_k: int | None = Field(default=None, ge=1)
    include_superseded: bool = False


class BoundaryQueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    request_id: str = Field(..., min_length=1)
    filters: BoundaryFilters
    options: BoundaryOptions = Field(default_factory=BoundaryOptions)


@router.post("/v1/query")
def query_boundary(
    body: BoundaryQueryRequest,
    _auth: None = Depends(require_internal_token),
    svc: QueryService = Depends(get_service),
):
    scope = _build_scope(body.filters, body.options)  # 422 在流开始前抛(统一 JSON 错误体)
    return StreamingResponse(
        _stream_query(svc, body, scope), media_type="text/event-stream"
    )


def _build_scope(filters: BoundaryFilters, options: BoundaryOptions) -> dict:
    """filters/options → ``Retriever.scoped`` 入参。校验失败抛 422(绝不静默放宽/静默零命中)。"""
    if "audit_project" in filters.corpus_types:
        # v1.6 Milvus schema 无 audit_project 分区、无 project_id/owner 标量:静默零命中会把
        # 集成/配置问题伪装成「未找到依据」,检索后过滤则破红线 → 显式拒绝,支持后一并放开。
        raise validation_error(
            "audit_project 语料未接入 audit-ai 当前 Milvus schema,暂不支持",
            {"corpus_types": list(filters.corpus_types)},
        )
    return {
        "corpora": tuple(_CORPUS_MAP[c] for c in filters.corpus_types),
        # 空 perm_tags = 无额外限制(契约);非空走 r4_listing 加固构造(白名单字段 + json 转义)
        "extra_expr": (
            array_any_expr("perm_tag", list(filters.perm_tags)) if filters.perm_tags else None
        ),
        "topk": options.top_k,
        "include_superseded": options.include_superseded,
    }


def _stream_query(svc: QueryService, body: BoundaryQueryRequest, scope: dict):
    yield KEEPALIVE  # 首字节立即出(注释帧防代理空闲断连;非事件,Java 侧按 SSE 规范忽略)
    try:
        collected: list = []
        # fail-closed:retriever 无 scoped 即异常 → error 事件,绝不无过滤放行(权限红线)
        with svc.retriever.scoped(collector=collected, **scope):
            result = svc.agent.ask(body.query, trace_id=body.request_id)

        yield format_sse("meta", {
            "request_id": body.request_id,
            "route_type": result.route_type.value,
            "ai_label": result.ai_label,
            "review_required": result.review_required,
            "export_enabled": result.export_enabled,
        })
        for seq, block in enumerate(result.answer_blocks):
            yield format_sse("delta", {
                "block_seq": seq,
                "block_type": block.type.value,
                "text": block.content,
            })
        if result.citations:
            score_map = _score_map(collected)
            for citation in result.citations:
                # 轻量引用:clause_id(=chunk_id,回查主键)+ score;绝不带 PG 回查字段
                yield format_sse("citation", {
                    "clause_id": citation.clause_id,
                    "chunk_id": citation.clause_id,
                    "score": score_map.get(citation.clause_id),
                })
        yield format_sse("done", {
            "finish_reason": "refused" if result.route_type is RouteType.REFUSE else "stop",
            "confidence": result.confidence,
            "exhausted_scope": list(result.exhausted_scope),
        })
    except Exception:
        # 500 语义:不泄内部细节(堆栈进日志/trace);码在契约 B1xx 服务向段(见 _ERR_INTERNAL)
        yield format_sse("error", {"code": _ERR_INTERNAL, "message": "生成失败"})


def _score_map(candidates: list) -> dict[str, float]:
    """同一次检索的候选(collector)→ ``chunk_id → 归一分``。

    同 chunk 保最高分;min-max 与前端 structured「匹配度」同口径(``make_normalizer``)。
    引用不在集合内(极少数路由自建候选)→ ``score: null``(契约 nullable,biz 降级不显)。
    """
    by_id: dict[str, float] = {}
    for c in candidates:
        s = _display_score(c)  # 与前端 structured 同口径:rerank 开→相关性分,否则 RRF
        prev = by_id.get(c.chunk_id)
        if prev is None or s > prev:
            by_id[c.chunk_id] = s
    norm = make_normalizer(list(by_id.values()))
    return {cid: norm(s) for cid, s in by_id.items()}
