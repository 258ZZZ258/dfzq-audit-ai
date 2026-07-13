"""达梦 8 表 → 批次目录转换(CP-010 Phase 5,零栈:FakeSource)。

核心验证:转换产物**能被 reader 接收契约接受**(round-trip,不 PresegFormatError)、精确桥接锚
(LAW_CONTENT.CODE ↔ CASE_PUNISH.LAW_CONTENT_CODE)端到端串上、DEL_FLAG 删除件过滤、is_catalog
权威、content_hash 确定。PATH_CODE→norm / STATUS_CODE 值域是部署期 seam,不在此断言。
"""

from __future__ import annotations

from pipeline.preseg import export
from pipeline.preseg.reader import (
    read_blocks,
    read_cases,
    validate_manifest_header,
    validate_manifest_rows,
)


class FakeSource(export.Source):
    """达梦形态假数据(大写列名);含删除件与空法规,验证过滤。"""

    def iter_laws(self):
        return [
            {"CODE": "L-1", "NAME": "证券公司客户招揽管理办法", "DOC_NO": "证监会令第300号",
             "ISSUE_AUTH_CN": "中国证监会", "ISSUE_DATE": "2024-01-15", "EFFECT_DATE": "2024-03-01",
             "INVALID_DATE": None, "STATUS_CODE": "现行有效", "SOURCE_LAW_ID": "L-0",
             "LEVELS": "部门规章", "TAG": "招揽;展业", "DEL_FLAG": "A"},
            {"CODE": "L-DEL", "NAME": "已废止规章", "LEVELS": "部门规章",
             "STATUS_CODE": "已废止", "DEL_FLAG": "D"},  # 删除件 → 整条过滤
            {"CODE": "L-EMPTY", "NAME": "无正文", "LEVELS": "部门规章", "DEL_FLAG": "A"},
        ]

    def contents_for(self, law_code):
        if law_code != "L-1":
            return []
        return [
            {"CODE": "LC-CH1", "LAW_CODE": "L-1", "PATH_CODE": "01", "IS_CATALOG": 1,
             "TITLE": "第一章", "INDEX_NO": 0, "CONTENT": "总则", "DEL_FLAG": "A"},
            {"CODE": "LC-021", "LAW_CODE": "L-1", "PATH_CODE": "01.021", "IS_CATALOG": 0,
             "TITLE": "第二十一条", "INDEX_NO": 1,
             "CONTENT": "证券公司不得通过二维码向不特定对象招揽客户。", "DEL_FLAG": "A"},
            {"CODE": "LC-DEL", "LAW_CODE": "L-1", "PATH_CODE": "01.099", "IS_CATALOG": 0,
             "TITLE": "第九十九条", "INDEX_NO": 2, "CONTENT": "已删除条文。", "DEL_FLAG": "D"},
        ]

    def iter_cases(self):
        return [{"CODE": "C-1", "NAME": "某营业部违规招揽案", "DOC_NO": "沪证监决〔2025〕12号",
                 "PUB_AUTH_CN": "上海证监局", "PUB_DATE": "2025-03-18", "DOC_TYPE": "行政处罚",
                 "EVENT_DATE": "2024-11-02", "CASE_DESC": "经查……", "SUMMARY": "违规招揽。",
                 "URL": "http://kb/case/1", "TAG": "招揽", "DEL_FLAG": "A"}]

    def parties_for(self, case_code):
        return [
            {"PARTY_INDEX": 0, "CASE_CODE": "C-1", "NAME": "王某", "TYPE_CN": "个人",
             "IDENTITY_CN": "营业部员工", "VIOL_TYPE_CN": "违规招揽", "FINE_AMT": "5",
             "PUNISH_CUR_CN": "万元", "IND_CN": "证券", "DEL_FLAG": "A"},
            {"PARTY_INDEX": 1, "CASE_CODE": "C-1", "NAME": "某证券公司", "TYPE_CN": "机构",
             "DEL_FLAG": "A"},
        ]

    def punishes_for(self, case_code):
        return [{"CASE_CODE": "C-1", "LAW_CODE": "L-1", "LAW_CONTENT_CODE": "LC-021",
                 "PUNISH_INDEX": 0, "PUNISH_LAW_TITLE": "证券公司客户招揽管理办法",
                 "PUNISH_LAW": "第二十一条", "CONTENT": "违反第二十一条。", "DEL_FLAG": "A"}]


def test_build_batch_roundtrips_through_reader(tmp_path):
    stats = export.build_batch(FakeSource(), tmp_path)
    assert stats["laws"] == 1 and stats["cases"] == 1  # L-DEL/L-EMPTY 过滤

    # blocks:reader 接受;删除条文过滤;is_catalog 权威;source_code 精确锚在位
    blocks = read_blocks(tmp_path / "blocks" / "L-1.jsonl")
    assert len(blocks) == 2  # 章 + 第二十一条(LC-DEL 删除件过滤)
    by_code = {b.source_code: b for b in blocks}
    assert by_code["LC-CH1"].is_catalog is True
    assert by_code["LC-021"].is_catalog is False
    assert by_code["LC-021"].clause_label == "第二十一条"

    # manifest:21 列精确匹配 + 行级契约通过;源逻辑键/版本链/效力就位
    import openpyxl

    ws = openpyxl.load_workbook(tmp_path / "manifest.xlsx").active
    rows = list(ws.iter_rows(values_only=True))
    header = list(rows[0])
    validate_manifest_header(header)  # 不 raise = 21 列精确匹配
    row = dict(zip(header, rows[1], strict=True))
    assert row["source_doc_id"] == "L-1" and row["corpus_type"] == "P-EXT"
    assert row["source_law_id"] == "L-0" and row["effective_status"] == "现行有效"
    assert row["content_hash"]  # 语义指纹已填(声明==实际)
    validate_manifest_rows([row])  # 幂等键非空 + corpus_type 合法

    # cases:reader 接受;**精确桥接锚串上**(law_content_code == block.source_code)
    cases = read_cases(tmp_path / "cases.jsonl")
    assert len(cases) == 1
    c = cases[0]
    assert c.case_name == "某营业部违规招揽案" and c.source_case_id == "C-1"
    assert c.violated_regulations[0].law_content_code == "LC-021" == by_code["LC-021"].source_code
    # 富涉案主体:全字段照带(D6),消费面 name/type 键在
    assert len(c.persons) == 2
    assert c.persons[0]["name"] == "王某" and c.persons[0]["type"] == "个人"
    assert c.persons[0]["fine_amt"] == "5" and c.persons[0]["industry"] == "证券"


def test_content_hash_deterministic(tmp_path):
    export.build_batch(FakeSource(), tmp_path)
    import openpyxl

    def _hash(p):
        ws = openpyxl.load_workbook(p / "manifest.xlsx").active
        rows = list(ws.iter_rows(values_only=True))
        return dict(zip(rows[0], rows[1], strict=True))["content_hash"]

    h1 = _hash(tmp_path)
    out2 = tmp_path / "again"
    export.build_batch(FakeSource(), out2)
    assert h1 == _hash(out2)  # 同源两次导出 content_hash 一致(幂等根基)


def test_deleted_and_empty_laws_filtered(tmp_path):
    stats = export.build_batch(FakeSource(), tmp_path)
    assert not (tmp_path / "blocks" / "L-DEL.jsonl").exists()  # 删除件不产 blocks
    assert not (tmp_path / "blocks" / "L-EMPTY.jsonl").exists()  # 空正文法规不产件
    assert stats["laws"] == 1
