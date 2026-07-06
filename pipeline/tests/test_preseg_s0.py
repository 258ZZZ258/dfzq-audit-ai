"""T5 S0 preseg 批次注册集成测试(连真 PG,不可达 skip;测完按 FK 序清理)。

覆盖:注册全字段/效力状态映射+未知值入队/幂等重跑零重复/换 hash 自动 revise_replace/
blocks 契约违约隔离/manifest 列集整批拒收/案例虚拟文档合成(D4)。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from openpyxl import load_workbook
from sqlalchemy import delete, select, text

from common.pg_models import (
    Document,
    DocVersion,
    ImportBatch,
    PipelineEvent,
    RemediationRecord,
    ReviewQueue,
)
from pipeline.config import load_config
from pipeline.index.object_store import ObjectStore
from pipeline.index.pg_io import PgIO
from pipeline.stage_base import StageContext
from pipeline.stages.s0_register import register_preseg_batch

FIXTURES = Path(__file__).parent / "fixtures" / "preseg_batch"


@pytest.fixture
def pg():
    io_ = PgIO.from_config(load_config())
    try:
        with io_.session() as s:
            s.execute(text("select 1"))
    except Exception:
        pytest.skip("PG 不可达(demo up 未起)")
    return io_


@pytest.fixture
def reg(pg, tmp_path):
    ctx = StageContext(config=load_config(), object_store=ObjectStore(tmp_path / "obj"), db=pg)
    batches: list[str] = []
    yield ctx, tmp_path, batches
    with pg.session() as s:
        dvs = list(s.scalars(select(DocVersion).where(DocVersion.batch_id.in_(batches or [""]))))
        dvids = [dv.doc_version_id for dv in dvs]
        lids = {dv.logical_id for dv in dvs}
        if dvids:
            s.execute(delete(RemediationRecord).where(RemediationRecord.doc_version_id.in_(dvids)))
            s.execute(delete(ReviewQueue).where(ReviewQueue.doc_version_id.in_(dvids)))
            s.execute(delete(PipelineEvent).where(PipelineEvent.doc_version_id.in_(dvids)))
            s.execute(delete(DocVersion).where(DocVersion.doc_version_id.in_(dvids)))
        if lids:
            s.execute(delete(Document).where(Document.logical_id.in_(lids)))
        if batches:
            s.execute(delete(ImportBatch).where(ImportBatch.batch_id.in_(batches)))


def _by_fn(report, fn):
    return next(o for o in report.outcomes if o.filename == fn)


def test_register_fixture_batch_full_fields(reg):
    ctx, _, batches = reg
    bid = "preseg-t5-full"
    batches.append(bid)
    report = register_preseg_batch(ctx, bid, FIXTURES, FIXTURES / "manifest.xlsx")
    assert report.accepted

    # 外规:效力状态命中映射 → 源权威直写 + 留痕
    ext = _by_fn(report, "ext-001")
    assert ext.status == "REGISTERED"
    with ctx.db.session() as s:
        dv = s.get(DocVersion, ext.doc_version_id)
        assert dv.source_format == "preseg"
        assert dv.source_doc_id == "KB-EXT-0001" and dv.content_hash == "sha-ext-001-v1"
        assert dv.version_status == "effective" and dv.version_status_source == "source"
        assert dv.tags == ["招揽", "展业"]
        assert dv.issuer_level_src == "部门规章"
        doc = s.get(Document, dv.logical_id)
        assert doc.corpus_type == "P-EXT"  # 检索分区归属(口径钉子)

    # 内规:未知效力状态「试行中」→ 保默认 + meta_confirm 队列,source 不标
    int_ = _by_fn(report, "int-001")
    with ctx.db.session() as s:
        dv = s.get(DocVersion, int_.doc_version_id)
        assert dv.version_status == "effective" and dv.version_status_source is None
        assert dv.file_no == "ZD-2024-005"
        q = s.scalars(
            select(ReviewQueue).where(
                ReviewQueue.doc_version_id == int_.doc_version_id,
                ReviewQueue.queue_type == "meta_confirm",
            )
        ).all()
        assert len(q) == 1 and "试行中" in q[0].reason

    # 案例:2 条虚拟文档(D4),corpus_type=P-CASE
    case_outcomes = [o for o in report.outcomes if o.filename.endswith("案")]
    assert len(case_outcomes) == 2
    with ctx.db.session() as s:
        for o in case_outcomes:
            dv = s.get(DocVersion, o.doc_version_id)
            assert dv.source_format == "preseg" and dv.raw_object_key
            assert s.get(Document, dv.logical_id).corpus_type == "P-CASE"
    assert dv.source_doc_id == "KB-CASE-0002"


def test_idempotent_rerun_zero_new_rows(reg):
    ctx, _, batches = reg
    bid = "preseg-t5-idem"
    batches.append(bid)
    register_preseg_batch(ctx, bid, FIXTURES, FIXTURES / "manifest.xlsx")
    with ctx.db.session() as s:
        n1 = len(s.scalars(select(DocVersion).where(DocVersion.batch_id == bid)).all())
    report2 = register_preseg_batch(ctx, bid, FIXTURES, FIXTURES / "manifest.xlsx")
    assert {o.status for o in report2.outcomes} == {"DUPLICATE"}
    with ctx.db.session() as s:
        n2 = len(s.scalars(select(DocVersion).where(DocVersion.batch_id == bid)).all())
    assert n1 == n2 == 4  # 2 文档 + 2 案例,重跑零新增


def test_content_hash_change_autochains(reg, tmp_path):
    ctx, _, batches = reg
    bid1, bid2 = "preseg-t5-chain-a", "preseg-t5-chain-b"
    batches.extend([bid1, bid2])
    r1 = register_preseg_batch(ctx, bid1, FIXTURES, FIXTURES / "manifest.xlsx")
    old = _by_fn(r1, "ext-001")

    # 复制批次,改 ext-001 的 content_hash(源记录更新)+ 去掉 cases 干扰
    batch2 = tmp_path / "batch2"
    shutil.copytree(FIXTURES, batch2)
    (batch2 / "cases.jsonl").unlink()
    wb = load_workbook(batch2 / "manifest.xlsx")
    ws = wb.active
    header = [c.value for c in ws[1]]
    col = header.index("content_hash") + 1
    for row in ws.iter_rows(min_row=2):
        if row[header.index("filename")].value == "ext-001":
            row[col - 1].value = "sha-ext-001-v2"
    wb.save(batch2 / "manifest.xlsx")

    r2 = register_preseg_batch(ctx, bid2, batch2, batch2 / "manifest.xlsx")
    new = _by_fn(r2, "ext-001")
    assert new.status == "REGISTERED" and new.doc_version_id != old.doc_version_id
    with ctx.db.session() as s:
        dv = s.get(DocVersion, new.doc_version_id)
        assert dv.version_relation == "revise_replace"
        assert dv.supersedes_version_id == old.doc_version_id
        assert dv.logical_id == old.logical_id  # 内容延续,继承 logical
    assert _by_fn(r2, "int-001").status == "DUPLICATE"  # 未变件照常幂等


def test_bad_blocks_quarantined(reg, tmp_path):
    ctx, _, batches = reg
    bid = "preseg-t5-badblocks"
    batches.append(bid)
    batch = tmp_path / "bad"
    shutil.copytree(FIXTURES, batch)
    (batch / "cases.jsonl").unlink()
    (batch / "blocks" / "ext-001.jsonl").write_text(
        '{"block_seq": 0}\n', encoding="utf-8"
    )  # text 缺失 → 契约违约
    report = register_preseg_batch(ctx, bid, batch, batch / "manifest.xlsx")
    out = _by_fn(report, "ext-001")
    assert out.status == "QUARANTINED" and "契约违约" in out.reason
    with ctx.db.session() as s:
        q = s.scalars(
            select(ReviewQueue).where(
                ReviewQueue.doc_version_id == out.doc_version_id,
                ReviewQueue.queue_type == "quarantine",
            )
        ).all()
        assert len(q) == 1


def test_manifest_mismatch_rejects_whole_batch(reg, tmp_path):
    ctx, _, batches = reg
    bid = "preseg-t5-reject"
    batches.append(bid)
    batch = tmp_path / "rej"
    shutil.copytree(FIXTURES, batch)
    wb = load_workbook(batch / "manifest.xlsx")
    ws = wb.active
    ws.delete_cols(1)  # 砍掉 filename 列 → 列集不匹配
    wb.save(batch / "manifest.xlsx")
    report = register_preseg_batch(ctx, bid, batch, batch / "manifest.xlsx")
    assert not report.accepted and "filename" in report.reject_reason
    with ctx.db.session() as s:
        assert not s.scalars(select(DocVersion).where(DocVersion.batch_id == bid)).all()
