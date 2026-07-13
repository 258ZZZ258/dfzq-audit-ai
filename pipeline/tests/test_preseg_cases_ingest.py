"""T9 案例结构化直装集成测试(真 PG;不可达 skip)。

覆盖:外规先入库→案例直装→cited_regulations 经 align_cited 归一命中(标题精确+条号
末段,含插入条+舍款)/对齐失败置 ref_unresolved/persons 照存/case chunks(问题汇总→
summary 保真)/**LLM 与 L1 正则零触达**(monkeypatch 哨兵)/配置缝退回 llm 路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import delete, select, text

from common.pg_models import (
    Case,
    Chunk,
    Document,
    DocVersion,
    ImportBatch,
    PipelineEvent,
    ReviewQueue,
)
from pipeline.config import load_config
from pipeline.index.object_store import ObjectStore
from pipeline.index.pg_io import PgIO
from pipeline.stage_base import StageContext
from pipeline.stages import s1_parse, s3_structure, s4_meta
from pipeline.stages.s0_register import register_preseg_batch

FIXTURES = Path(__file__).parent / "fixtures" / "preseg_batch"


@pytest.fixture
def env(tmp_path):
    io_ = PgIO.from_config(load_config())
    try:
        with io_.session() as s:
            s.execute(text("select 1"))
    except Exception:
        pytest.skip("PG 不可达(demo up 未起)")
    ctx = StageContext(config=load_config(), object_store=ObjectStore(tmp_path / "obj"), db=io_)
    batches: list[str] = []
    yield ctx, batches
    with io_.session() as s:
        dvs = list(s.scalars(select(DocVersion).where(DocVersion.batch_id.in_(batches or [""]))))
        dvids = [dv.doc_version_id for dv in dvs]
        lids = {dv.logical_id for dv in dvs}
        if dvids:
            s.execute(delete(Case).where(Case.doc_version_id.in_(dvids)))
            s.execute(delete(Chunk).where(Chunk.doc_version_id.in_(dvids)))
            s.execute(delete(ReviewQueue).where(ReviewQueue.doc_version_id.in_(dvids)))
            s.execute(delete(PipelineEvent).where(PipelineEvent.doc_version_id.in_(dvids)))
            s.execute(delete(DocVersion).where(DocVersion.doc_version_id.in_(dvids)))
        if lids:
            s.execute(delete(Document).where(Document.logical_id.in_(lids)))
        if batches:
            s.execute(delete(ImportBatch).where(ImportBatch.batch_id.in_(batches)))


@pytest.fixture
def ingested(env, monkeypatch):
    """注册 fixtures 批次;外规 ext-001 走 s3 落 chunk 并置 effective(供对齐命中);
    并给 LLM/L1 通道装哨兵——被触达即失败(T9 验收:零 LLM 零正则)。"""
    ctx, batches = env
    bid = "preseg-t9"
    batches.append(bid)
    r = register_preseg_batch(ctx, bid, FIXTURES, FIXTURES / "manifest.xlsx")

    ext_dvid = next(o.doc_version_id for o in r.outcomes if o.filename == "ext-001")
    s3_structure.run(ctx, ext_dvid)  # 外规 chunks(preseg s3 分支,不需 IR)
    # 效力:S0 已按源 effective;PgRegLookup 只认 version_status=effective ✓

    def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("preseg 结构化直装不得触达 LLM/L1 抽取通道")

    from pipeline.meta import case_extract

    # case_l2(LLM 案例富集)已整段移除;仅留规则抽取哨兵(结构化直装不得触达)
    monkeypatch.setattr(case_extract, "extract_case", _boom)

    case_dvids = {
        o.filename: o.doc_version_id for o in r.outcomes if o.filename.endswith("案")
    }
    return ctx, case_dvids


def _run_case(ctx, dvid):
    s1_parse.start(ctx, dvid)
    s1_parse.run(ctx, dvid)  # 合成 IR(s4.run 要 load_ir)
    s3_structure.run(ctx, dvid)
    return s4_meta.run(ctx, dvid)


def test_structured_ingest_aligns_and_lands(ingested):
    ctx, case_dvids = ingested
    dvid = case_dvids["某证券营业部员工微信二维码违规招揽客户案"]
    _run_case(ctx, dvid)
    case = ctx.db.get_case(dvid)
    assert case.penalty_org == "上海证监局"
    assert case.violation_category == "违规展业"
    assert case.respondent == "王某" and case.respondent_type == "个人"
    assert len(case.persons) == 2 and case.persons[1]["reason"] == "管理失职"
    assert str(case.occurred_at) == "2024-11-02"
    assert case.source_url == "http://kb.internal/case/12"
    # 精确桥接:law_content_code=LC-EXT001-021 → 同一 chunk 的 doc_no+clause_path_norm(=1/21)
    (ref,) = case.cited_regulations
    assert ref["resolved"] and ref["doc_no"] == "证监会令第300号"
    assert ref["clause_path_norm"] == "1/21"  # 取自锚定 chunk 本身(非 fuzzy 末段推断)
    assert ref["match"] == "exact"  # CP-010:走精确直连而非模糊标题对齐
    assert case.ref_unresolved is False


def test_alignment_failure_sets_flag_not_block(ingested):
    ctx, case_dvids = ingested
    dvid = case_dvids["某券商未及时更新客户风险等级被责令改正案"]
    _run_case(ctx, dvid)
    case = ctx.db.get_case(dvid)
    refs = {r["title"]: r for r in case.cited_regulations}
    # 精确锚 law_content_code=LC-EXT001-021-1 → 命中 "1/21-1"(款级天然舍款取条,D5)
    ok = refs["证券公司客户招揽管理办法"]
    assert ok["resolved"] and ok["clause_path_norm"] == "1/21-1" and ok["match"] == "exact"
    # 无锚 + 标题不存在 → 回落 fuzzy → 未解析;整案置标记但**已入库**(不阻塞,§9)
    bad = refs["不存在的规章名称"]
    assert bad["resolved"] is False and bad["match"] == "fuzzy"
    assert case.ref_unresolved is True


def test_case_chunks_from_record_fidelity(ingested):
    ctx, case_dvids = ingested
    dvid = case_dvids["某证券营业部员工微信二维码违规招揽客户案"]
    _run_case(ctx, dvid)
    with ctx.db.session() as s:
        chunks = s.scalars(select(Chunk).where(Chunk.doc_version_id == dvid)).all()
    by_type = {c.chunk_type: c for c in chunks}
    assert set(by_type) == {"case_section", "case_summary"}
    # 摘要 = 问题汇总原文(人工撰写替代规则截断,映射 #18;搬运保真)
    expect = "当事人通过微信向不特定对象发送开户推广二维码,构成违规招揽。"
    assert by_type["case_summary"].text == expect
    assert by_type["case_section"].text.startswith("经查,当事人王某")
    assert all(c.page_start is None for c in chunks)  # D2
    assert all(c.clause_path_norm.startswith("case/") for c in chunks)  # 反查伪路径约定


def test_reconcile_fixes_case_first_ordering(env, monkeypatch):
    """F7:案例先于被引法规建块 → 精确锚当场查不到 → ref_unresolved;批后对账重解析 → 精确命中。

    复现驱动顺序竞态:不预跑 ext S3,先把案例推到 S4(此时锚查空)→ 再建法规块 →
    reconcile_preseg_case_refs 自愈。"""
    from pipeline.meta import case_extract
    from pipeline.preseg.cases_ingest import reconcile_preseg_case_refs

    ctx, batches = env
    bid = "preseg-t9-reconcile"
    batches.append(bid)
    r = register_preseg_batch(ctx, bid, FIXTURES, FIXTURES / "manifest.xlsx")

    def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("结构化直装不得触达 LLM/L1")

    monkeypatch.setattr(case_extract, "extract_case", _boom)

    case_dvid = next(
        o.doc_version_id for o in r.outcomes
        if o.filename == "某证券营业部员工微信二维码违规招揽客户案"
    )
    ext_dvid = next(o.doc_version_id for o in r.outcomes if o.filename == "ext-001")

    _run_case(ctx, case_dvid)  # 案例先跑(法规未建块)
    assert ctx.db.get_case(case_dvid).ref_unresolved is True  # 锚查空 + fuzzy 无块 → 未解析

    s3_structure.run(ctx, ext_dvid)  # 法规现在建块(effective P-EXT)
    assert reconcile_preseg_case_refs(ctx, bid) == 1  # 对账修 1 例

    case = ctx.db.get_case(case_dvid)
    assert case.ref_unresolved is False
    (ref,) = case.cited_regulations
    assert ref["match"] == "exact" and ref["clause_path_norm"] == "1/21"


def test_config_seam_flips_back_to_llm_path(env, monkeypatch):
    """case_ref_source 翻回 llm → 走既有 L1 路径(机制保留,B8)。"""
    ctx, batches = env
    bid = "preseg-t9-seam"
    batches.append(bid)
    r = register_preseg_batch(ctx, bid, FIXTURES, FIXTURES / "manifest.xlsx")
    dvid = next(o.doc_version_id for o in r.outcomes if o.filename.endswith("招揽客户案"))

    monkeypatch.setattr(ctx.config.profiles["P-PRESEG"], "case_ref_source", "llm")
    called = {}
    from pipeline.meta import case_extract

    real = case_extract.extract_case

    def spy(*a, **k):  # noqa: ANN002, ANN003
        called["l1"] = True
        return real(*a, **k)

    monkeypatch.setattr(case_extract, "extract_case", spy)
    _run_case(ctx, dvid)
    assert called.get("l1")  # 退回 L1 抽取路径


# ── _align_violated 纯函数(零栈:fake lookup;精确优先 / 无锚·未命中回落 fuzzy)────────


class _FakeLookup:
    def __init__(self, exact=None, doc=None):
        self._exact = exact or {}  # source_code -> {doc_no, clause_path_norm, title}
        self._doc = doc  # find() 返回的 RegDoc(None=未命中)

    def resolve_exact(self, code):
        return self._exact.get(code)

    def find(self, doc_number, title):
        return self._doc


class TestAlignViolatedPure:
    def test_exact_anchor_wins(self):
        from pipeline.preseg.cases_ingest import _align_violated

        lk = _FakeLookup(
            exact={"LC-1": {"doc_no": "DN-1", "clause_path_norm": "2/5", "title": "T"}}
        )
        out, unresolved = _align_violated([{"title": "《X》", "law_content_code": "LC-1"}], lk)
        assert out[0] == {
            "doc_no": "DN-1", "title": "《X》", "clause_path_norm": "2/5",
            "resolved": True, "match": "exact",
        }
        assert unresolved is False  # 精确命中不查 find()

    def test_no_anchor_falls_to_fuzzy(self):
        from pipeline.preseg.cases_ingest import _align_violated

        lk = _FakeLookup(doc=None)  # 无 law_content_code → fuzzy;find None → 未解析
        out, unresolved = _align_violated([{"title": "《X》", "clause_label": "第五条"}], lk)
        assert out[0]["match"] == "fuzzy" and out[0]["resolved"] is False
        assert unresolved is True

    def test_anchor_miss_falls_to_fuzzy(self):
        from pipeline.preseg.cases_ingest import _align_violated

        # 有 law_content_code 但库内无此锚(resolve_exact None)→ 回落 fuzzy(不静默丢)
        lk = _FakeLookup(exact={}, doc=None)
        out, _ = _align_violated([{"title": "《X》", "law_content_code": "LC-missing"}], lk)
        assert out[0]["match"] == "fuzzy"
