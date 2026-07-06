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

    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
