"""案例结构化直装——虚拟文档合成部分(CP-010 T5,决策 D4;完整直装链 T9)。

源案例是「结构化记录 + 附件」,不天然对应一份决定书文件。每案合成一个 doc_version
(``source_format="preseg"``,raw = 案例记录 JSON 原文,溯源即源记录),cases→doc_versions
外键不动 → bridge/r5 消费链零改。**corpus_type=P-CASE**(检索分区归属,见 reader 口径钉子)。

幂等键:``source_case_id + record_hash``(reader 计算的记录规范化哈希);源主键缺失时退
record_hash 单键。同键重跑 → DUPLICATE 不新建;同 source_case_id 换 hash → 新版本自动
version chain(revise_replace 继承 logical)。
"""

from __future__ import annotations

import json
from dataclasses import asdict

from sqlalchemy import select
from ulid import ULID

from common.pg_models import Document, DocVersion, PipelineEvent
from pipeline.preseg.reader import PresegCase
from pipeline.stage_base import StageContext
from pipeline.states import PipelineState


def find_existing_case_doc(ctx: StageContext, case: PresegCase) -> DocVersion | None:
    """幂等命中:同 source_case_id+record_hash(或无源主键时同 record_hash 的 preseg 件)。"""
    with ctx.db.session() as s:
        q = select(DocVersion).where(DocVersion.content_hash == case.record_hash)
        if case.source_case_id:
            q = q.where(DocVersion.source_doc_id == case.source_case_id)
        else:
            q = q.where(DocVersion.source_format == "preseg")
        return s.scalars(q).first()


def _prior_case_doc(ctx: StageContext, case: PresegCase) -> DocVersion | None:
    """同源主键的既有版本(换 hash 场景)→ 自动 revise_replace 链目标。"""
    if not case.source_case_id:
        return None
    with ctx.db.session() as s:
        return s.scalars(
            select(DocVersion)
            .where(DocVersion.source_doc_id == case.source_case_id)
            .order_by(DocVersion.created_at.desc())
        ).first()


def synthesize_case_doc(
    ctx: StageContext, batch_id: str, case: PresegCase
) -> tuple[str, str]:
    """一条案例记录 → 虚拟 Document + DocVersion(REGISTERED)。返回 (dvid, logical_id)。

    调用方须先用 ``find_existing_case_doc`` 去重;本函数只管建。chunk 构建与 cases 表
    直装在 T9(cases_ingest 完整链),此处仅落文档骨架 + raw 溯源 + 事件。
    """
    raw = json.dumps(asdict(case), ensure_ascii=False, sort_keys=True).encode("utf-8")
    dvid = str(ULID())
    raw_key = ctx.object_store.put_raw("P-CASE", batch_id, dvid, "json", raw)

    prior = _prior_case_doc(ctx, case)
    logical_id = prior.logical_id if prior is not None else str(ULID())
    with ctx.db.session() as s:
        if prior is None:
            s.add(Document(logical_id=logical_id, corpus_type="P-CASE", title=case.case_name))
            s.flush()
        s.add(
            DocVersion(
                doc_version_id=dvid,
                logical_id=logical_id,
                batch_id=batch_id,
                source_format="preseg",  # 虚拟文档标识(D4;通道性标志,非文件格式)
                source_hash=case.record_hash,
                raw_object_key=raw_key,
                source_filename=None,
                pipeline_status=PipelineState.REGISTERED.value,
                perm_tag="internal",  # 案例库默认密级;真值域待样例(P1)
                title=case.case_name,
                doc_number=case.doc_number,
                issue_date=_iso_date(case.issue_date),
                source_doc_id=case.source_case_id,
                content_hash=case.record_hash,
                tags=case.tags or None,
                version_relation="revise_replace" if prior is not None else None,
                supersedes_version_id=prior.doc_version_id if prior is not None else None,
            )
        )
        s.flush()
        s.add(
            PipelineEvent(
                doc_version_id=dvid,
                from_state=None,
                to_state=PipelineState.REGISTERED.value,
                actor=ctx.user,
                detail={"preseg_case": {"source_case_id": case.source_case_id}},
            )
        )
    return dvid, logical_id


def _iso_date(value: str | None):
    from datetime import date

    # 非 str(reader 已拦;此为纵深:防 date.fromisoformat 收到非 str 抛未捕获 TypeError)
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


# ── T9:结构化直装完整链(替代 L1 正则案例抽取;case_l2 LLM 通道已整段移除)──────────────


def use_structured_case(dv, profiles: dict) -> bool:
    """分派条件(B8 配置缝):preseg 案例且 P-PRESEG 档案 case_ref_source=='structured'。

    非 'structured' 值 → 退回自建通道规则抽取(``s4_meta._extract_case``,零 LLM;case_l2 已移除)。
    """
    prof = profiles.get("P-PRESEG")
    return (
        dv is not None
        and dv.source_format == "preseg"
        and prof is not None
        and getattr(prof, "case_ref_source", "rule") == "structured"
    )


def extract_case_structured(ctx: StageContext, dv: DocVersion) -> None:
    """源案例记录(raw JSON)→ cases 行直装:**零 LLM、零正则抽取**。

    违反法规子表经 ``align_cited``+``PgRegLookup``(纯逻辑,复用)归一对齐——结构化数据
    仍需对齐验证(源给的是标题+条款标识,库内锚点是 doc_no+clause_path_norm);对齐失败
    照旧仅置 ``ref_unresolved`` 标记不阻塞(§9 现状口径)。persons 原样照存(D6)。
    respondent 单值列 = persons 首条(兼容既有消费面);全量在 persons。
    """
    from pipeline.meta.case_ref_align import align_cited
    from pipeline.meta.reg_lookup import PgRegLookup

    rec = json.loads(ctx.object_store.get(dv.raw_object_key).decode("utf-8"))
    cited_in = [
        {"title": v.get("title"), "clause": v.get("clause_label")}
        for v in rec.get("violated_regulations") or []
    ]
    aligned, unresolved = align_cited(cited_in, PgRegLookup(ctx.db))

    persons = rec.get("persons") or []
    first = persons[0] if persons else {}
    rtype = first.get("type")
    ctx.db.upsert_case(
        dv.doc_version_id,
        {
            "penalty_org": rec.get("issuing_org"),
            "doc_number": rec.get("doc_number"),
            "penalty_date": _iso_date(rec.get("issue_date")),  # 发文日期≈处罚日期(#17 待样例校核)
            "respondent": first.get("name"),
            "respondent_type": rtype if rtype in ("机构", "个人") else None,
            "violation_category": rec.get("case_type"),  # 值域 vs dict 待校核(#16),直写
            "cited_regulations": aligned,
            "ref_unresolved": unresolved,
            "persons": persons,
            "occurred_at": _iso_date(rec.get("occurred_at")),
            "source_url": rec.get("source_url"),
        },
    )


def build_case_specs_from_record(dvid: str, rec: dict, cfg) -> list:
    """源案例记录 → case chunks(与 case_chunker 同约定:``case/{k}`` 伪路径,k 1-based)。

    - ``case_section`` ← 案例描述(description);
    - ``case_summary`` ← **问题汇总(人工撰写)**,替代规则截断版/默认关的 LLM 摘要(映射 #18);
      缺失时退 case_name。搬运保真:文本原样,零页码(D2)。超预算复用 _split_oversize。
    """
    from common.chunk_id import compute_chunk_id
    from pipeline.chunking.chunker import ChunkSpec, _split_oversize, count_tokens

    parts: list[tuple[str, str, str]] = []  # (name, text, chunk_type)
    if rec.get("description"):
        parts.append(("描述", rec["description"], "case_section"))
    summary = rec.get("problem_summary") or rec.get("case_name") or ""
    if summary:
        parts.append(("摘要", summary, "case_summary"))

    specs: list = []
    k = 0
    for name, text, ctype in parts:
        pieces = (
            _split_oversize(text, cfg.target_token_max)
            if count_tokens(text) > cfg.target_token_max
            else [(text, False)]
        )
        for piece, hard in pieces:
            k += 1
            norm = f"case/{k}"
            specs.append(
                ChunkSpec(
                    chunk_id=compute_chunk_id(dvid, norm, 0),
                    doc_version_id=dvid,
                    clause_path=name,
                    clause_path_norm=norm,
                    seq=0,
                    text=piece,
                    breadcrumb=name,
                    page_start=None,  # D2 零页码
                    page_end=None,
                    token_count=count_tokens(piece),
                    oversize=hard,
                    chunk_type=ctype,
                )
            )
    return specs
