"""PG 外规查询:``case_ref_align.RegLookup`` 的生产实现(零 LLM)。

从 case_l2 移出——case_l2(LLM 案例富集)整段移除后,``PgRegLookup`` 是纯 PG 工具,**preseg
结构化案例引用对齐仍用**(``cases_ingest.align_cited(cited, PgRegLookup(db))``),故独立保留于此。
"""

from __future__ import annotations

from sqlalchemy import select

from common.pg_models import Chunk, Document, DocVersion
from pipeline.meta.case_ref_align import RegDoc


class PgRegLookup:
    """生产外规查询(``case_ref_align.RegLookup`` 实现):PG 按文号 / 标题命中 effective **外规
    (P-EXT)**,聚合其全部 chunk 的 ``clause_path_norm`` 供超界校验。

    **corpus 限定 P-EXT**:案例引用的是外规(§9「引用外规条款」)。若不限语料,同文号 / 同标题的
    内规(P-INT)或案例(P-CASE)会被误取,把其 chunk 当外规条款落进 ``cited_regulations``,污染
    query 案例反查 → 故 join ``Document`` 钉死 ``corpus_type="P-EXT"``。
    """

    def __init__(self, db) -> None:
        self._db = db

    @staticmethod
    def _find_ext(s, predicate) -> DocVersion | None:
        """按 predicate 命中 effective 的 **P-EXT** doc_version(非外规即便文号/标题撞也不取)。"""
        return s.scalars(
            select(DocVersion)
            .join(Document, DocVersion.logical_id == Document.logical_id)
            .where(
                predicate,
                DocVersion.version_status == "effective",
                Document.corpus_type == "P-EXT",
            )
        ).first()

    def find(self, doc_number: str | None, title: str | None) -> RegDoc | None:
        with self._db.session() as s:
            dv = self._find_ext(s, DocVersion.doc_number == doc_number) if doc_number else None
            if dv is None and title:  # 文号未命中 → 标题精确兜底(仍限 P-EXT)
                dv = self._find_ext(s, DocVersion.title == title)
            if dv is None:
                return None
            norms = frozenset(
                n
                for n in s.scalars(
                    select(Chunk.clause_path_norm).where(
                        Chunk.doc_version_id == dv.doc_version_id,
                        Chunk.clause_path_norm.is_not(None),
                    )
                )
            )
            return RegDoc(
                doc_version_id=dv.doc_version_id, doc_number=dv.doc_number, clause_norms=norms
            )
