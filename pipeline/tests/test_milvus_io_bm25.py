"""T4(CP-012):建 bm25 集合(Function+BM25 索引)+ upsert 剔除 sparse_vec。

- 单元(零栈):_to_milvus_dict bm25 剔 sparse_vec / bge 保留(byte 等价守护)。
- 集成(真栈 Milvus 2.5):bm25 集合建成 + upsert 无客户端 sparse 成功(Milvus 从 text 产 sparse_vec;
  若误传 sparse_vec 会被 Milvus 拒插 function 输出字段)。
"""

from __future__ import annotations

import uuid

import pytest

from pipeline.config import load_config
from pipeline.index.milvus_io import CorpusRow, MilvusIO, _to_milvus_dict

DENSE = [float((i * 7) % 13) + 0.5 for i in range(1024)]


def _row(cid: str, text: str) -> CorpusRow:
    return CorpusRow(
        chunk_id=cid, dense=DENSE, sparse={"1": 0.9},
        doc_id="D1", doc_version_id="V1", corpus_type="P-INT", sub_type="",
        status="effective", perm_tag=[], biz_domain=[], issuer_level=1, entity_type=[],
        chunk_type="clause", clause_path="第一条", page_start=0, effective_date=0,
        text=text, degraded=False,
    )


# ── 单元(零栈):upsert payload 分形态 ──
def test_to_milvus_dict_bm25_drops_sparse():
    d = _to_milvus_dict(_row("c1", "文本"), bm25=True)
    assert "sparse_vec" not in d  # Milvus function 产出字段,摄取端不写
    assert d["text"] == "文本"  # BM25 输入保留


def test_to_milvus_dict_bge_keeps_sparse():
    d = _to_milvus_dict(_row("c1", "文本"), bm25=False)
    assert d["sparse_vec"] == {1: 0.9}  # bge:客户端稀疏写入(byte 等价)


# ── 集成(真栈 Milvus 2.5)──
@pytest.fixture
def bm25_milvus():
    from pymilvus import utility

    settings = load_config()
    settings.embedding.sparse_backend = "bm25"
    settings.milvus.collection = "audit_corpus_bm25_t4_" + uuid.uuid4().hex[:8]
    mio = MilvusIO(settings)
    try:
        mio.connect()
        utility.list_collections()  # 强制真实连接,不可达则 skip
    except Exception:
        pytest.skip("Milvus 不可达(demo up 未起)")
    mio.create_collection(drop_existing=True)
    yield mio
    if utility.has_collection(settings.milvus.collection):
        utility.drop_collection(settings.milvus.collection)
    mio.disconnect()


def test_bm25_collection_built(bm25_milvus):
    fields = bm25_milvus.describe()
    assert fields["sparse_vec"] == "SPARSE_FLOAT_VECTOR"  # 字段仍稀疏向量(Function 产出)
    assert fields["dense_vec"] == "FLOAT_VECTOR"


def test_bm25_upsert_without_client_sparse(bm25_milvus):
    n = bm25_milvus.upsert([_row("c1", "证监会公告第十五号 处罚标准"), _row("c2", "内部控制制度")])
    bm25_milvus.flush()
    assert n == 2
    assert bm25_milvus.count("V1") == 2  # Milvus 接受无 sparse_vec 的插入(function 从 text 产)
