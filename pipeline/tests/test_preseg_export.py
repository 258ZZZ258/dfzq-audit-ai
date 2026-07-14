"""达梦 8 表 → 批次目录转换(CP-010 Phase 5,零栈:FakeSource)。

核心:转换产物 round-trip 过 reader 接收契约、精确桥接锚串上、DEL_FLAG 过滤、is_catalog 权威、
content_hash 确定。Codex 修复:SCOPE 权威分类 fail-closed、日期规范化、_fit 截断、文件名单射、
陈旧产物清理。
"""

from __future__ import annotations

from datetime import datetime

from pipeline.preseg import export
from pipeline.preseg.reader import (
    read_blocks,
    read_cases,
    validate_manifest_header,
    validate_manifest_rows,
)


class FakeSource(export.Source):
    """达梦形态假数据(大写列名);含删除件、空件、未知 SCOPE,验证分类/过滤/fail-closed。"""

    def iter_laws(self):
        return [
            {"CODE": "L-1", "NAME": "证券公司客户招揽管理办法", "DOC_NO": "证监会令第300号",
             "SCOPE": 0, "ISSUE_AUTH_CN": "中国证监会", "SUIT_OBJ_CODE": "证券、基金",
             "CREATOR_ID": "kb_admin", "ISSUE_DATE": datetime(2024, 1, 15, 9, 30),
             "EFFECT_DATE": "2024-03-01", "INVALID_DATE": None, "STATUS_CODE": "inuse",
             "SOURCE_LAW_ID": "L-0", "LEVELS": "部门规章", "TAG": "招揽;展业", "DEL_FLAG": "A"},
            {"CODE": "L-INT", "NAME": "公司内控制度", "SCOPE": 1, "LEVELS": "公司制度",
             "STATUS_CODE": "inuse", "DEL_FLAG": "A"},
            {"CODE": "L-BADSCOPE", "NAME": "分类不明", "SCOPE": None, "DEL_FLAG": "A"},  # 拒收
            {"CODE": "L-TEST", "NAME": "测试", "SCOPE": 0, "STATUS_CODE": "test_run",
             "DEL_FLAG": "A"},  # test_run 跳过
            {"CODE": "L-DEL", "NAME": "已删除", "SCOPE": 0, "DEL_FLAG": "D"},  # 删除件过滤
            {"CODE": "L-EMPTY", "NAME": "无正文", "SCOPE": 0, "DEL_FLAG": "A"},
        ]

    def contents_for(self, law_code):
        if law_code == "L-INT":
            return [{"CODE": "LC-INT-1", "IS_CATALOG": 0, "TITLE": "第一条",
                     "INDEX_NO": 0, "CONTENT": "内控正文。", "DEL_FLAG": "A"}]
        if law_code != "L-1":
            return []
        return [
            {"CODE": "LC-CH1", "IS_CATALOG": 1, "TITLE": "第一章", "INDEX_NO": 0,
             "CONTENT": "总则", "DEL_FLAG": "A"},
            {"CODE": "LC-021", "IS_CATALOG": 0, "TITLE": "第二十一条", "INDEX_NO": 1,
             "CONTENT": "证券公司不得通过二维码向不特定对象招揽客户。", "DEL_FLAG": "A"},
            {"CODE": "LC-DEL", "IS_CATALOG": 0, "TITLE": "第九十九条", "INDEX_NO": 2,
             "CONTENT": "已删除条文。", "DEL_FLAG": "D"},
        ]

    def iter_cases(self):
        return [{"CODE": "C-1", "NAME": "某营业部违规招揽案", "DOC_NO": "沪证监决〔2025〕12号",
                 "PUB_AUTH_CN": "上海证监局", "PUB_DATE": datetime(2025, 3, 18, 0, 0),
                 "DOC_TYPE": "行政处罚", "EVENT_DATE": "2024-11-02", "CASE_DESC": "经查……",
                 "SUMMARY": "违规招揽。", "URL": "http://kb/c/1", "TAG": "招揽", "DEL_FLAG": "A"}]

    def parties_for(self, case_code):
        return [
            {"PARTY_INDEX": 0, "CASE_CODE": "C-1", "NAME": "王某", "TYPE_CN": "个人",
             "IDENTITY_CN": "营业部员工", "VIOL_TYPE_CN": "违规招揽", "FINE_AMT": "5",
             "IND_CN": "证券", "DEL_FLAG": "A"},
            {"PARTY_INDEX": 1, "CASE_CODE": "C-1", "NAME": "某证券公司", "TYPE_CN": "机构",
             "DEL_FLAG": "A"},
        ]

    def punishes_for(self, case_code):
        # 真数据语义:PUNISH_LAW=法规名,PUNISH_LAW_TITLE=条款标识(探查 C2)
        return [{"CASE_CODE": "C-1", "LAW_CODE": "L-1", "LAW_CONTENT_CODE": "LC-021",
                 "PUNISH_INDEX": 0, "PUNISH_LAW": "证券公司客户招揽管理办法",
                 "PUNISH_LAW_TITLE": "第二十一条", "CONTENT": "违反第二十一条。", "DEL_FLAG": "A"}]


def _manifest_rows(out_dir):
    import openpyxl

    ws = openpyxl.load_workbook(out_dir / "manifest.xlsx").active
    rows = list(ws.iter_rows(values_only=True))
    header = list(rows[0])
    validate_manifest_header(header)
    return header, [dict(zip(header, r, strict=True)) for r in rows[1:]]


def test_build_batch_roundtrips_through_reader(tmp_path):
    stats = export.build_batch(FakeSource(), tmp_path)
    assert stats["laws"] == 2 and stats["cases"] == 1  # L-1 + L-INT;L-DEL/L-EMPTY/L-BADSCOPE 出局

    header, rows = _manifest_rows(tmp_path)
    by_code = {r["source_doc_id"]: r for r in rows}
    # SCOPE 权威分类:0→P-EXT public,1→P-INT internal
    assert by_code["L-1"]["corpus_type"] == "P-EXT" and by_code["L-1"]["perm_tag"] == "public"
    assert by_code["L-INT"]["corpus_type"] == "P-INT" and by_code["L-INT"]["perm_tag"] == "internal"
    # 日期规范化:datetime(含时分秒)→ ISO 日期(否则 S0 fromisoformat 静默 None)
    assert by_code["L-1"]["issue_date"] == "2024-01-15"
    assert by_code["L-1"]["content_hash"]
    # 适用对象(多值拆 ;-join)+ 源创建人
    assert by_code["L-1"]["entity_types"] == "证券;基金"
    assert by_code["L-1"]["source_created_by"] == "kb_admin"
    for r in rows:
        validate_manifest_rows([r])

    # blocks + cases 过 reader 契约;精确锚串上(文件名 = 基名-哈希后缀,glob 取)
    blocks = read_blocks(next((tmp_path / "blocks").glob("L-1-*.jsonl")))
    by_sc = {b.source_code: b for b in blocks}
    assert by_sc["LC-CH1"].is_catalog is True and by_sc["LC-021"].is_catalog is False
    cases = read_cases(tmp_path / "cases.jsonl")
    v0 = cases[0].violated_regulations[0]
    assert v0.law_content_code == "LC-021" == by_sc["LC-021"].source_code  # 精确锚串上
    # 真数据语义:title=法规名,clause_label=条款标识(不再反)
    assert v0.title == "证券公司客户招揽管理办法" and v0.clause_label == "第二十一条"
    assert cases[0].issue_date == "2025-03-18"  # PUB_DATE datetime → ISO 日期
    assert cases[0].persons[0]["fine_amt"] == "5"


def test_unknown_scope_fail_closed(tmp_path):
    stats = export.build_batch(FakeSource(), tmp_path)
    # SCOPE 未知 → 拒收(不默认 public),记 skipped
    assert any("L-BADSCOPE" in s for s in stats["skipped"])
    _, rows = _manifest_rows(tmp_path)
    assert "L-BADSCOPE" not in {r["source_doc_id"] for r in rows}


def test_entity_types_multi_delimiter_split():
    # 真数据 SUIT_OBJ_CODE 中文多值,分隔符混用 顿号/竖线 → 统一拆为 ;-join
    assert export.entity_types_of("证券、基金") == "证券;基金"
    assert export.entity_types_of("证券|基金") == "证券;基金"
    assert export.entity_types_of("通用") == "通用"  # 特殊值原样保留
    assert export.entity_types_of(None) is None


def test_test_run_status_skipped(tmp_path):
    stats = export.build_batch(FakeSource(), tmp_path)
    assert any("L-TEST" in s for s in stats["skipped"])  # test_run 测试数据不导出
    _, rows = _manifest_rows(tmp_path)
    assert "L-TEST" not in {r["source_doc_id"] for r in rows}


def test_status_map_real_domain():
    from pipeline.preseg.status_map import map_effective_status

    assert map_effective_status("inuse").status == "effective"
    assert map_effective_status("abolish").status == "abolished"
    assert map_effective_status("modified").status == "superseded"
    assert map_effective_status("pending").status == "upcoming"
    assert map_effective_status("draft").status == "upcoming"
    assert map_effective_status("weird_val").needs_review is True  # 未知 → meta_confirm


def test_classify_scope_mapping():
    assert export.classify_scope(0) == ("P-EXT", "public")
    assert export.classify_scope(1) == ("P-INT", "internal")
    assert export.classify_scope(2) == ("P-EXT", "internal")  # 标准:外规分区,密级保守
    assert export.classify_scope(None) is None  # fail-closed
    assert export.classify_scope("x") is None
    assert export.classify_scope(9) is None


def test_date_normalization():
    assert export._date(datetime(2026, 7, 13, 9, 30)) == "2026-07-13"  # datetime 丢时分秒
    assert export._date("2026-07-13 00:00:00") == "2026-07-13"  # 串取前 10
    assert export._date("2026-07-13") == "2026-07-13"
    assert export._date(None) is None


def test_bound_rejects_over_width_not_truncate():
    import pytest

    # 落 PG 定长列超宽 → 拒收(不截断法律元数据;Codex 二轮 F2/F3)
    assert export._bound("甲" * 500, "title") == "甲" * 500  # 512 内正常
    with pytest.raises(export.PresegExportError):
        export._bound("甲" * 600, "title")  # 超 512 → 拒收
    with pytest.raises(export.PresegExportError):
        export._bound("x" * 300, "source_code")  # 键超 256 → 拒收


def test_over_width_field_skips_record_with_audit(tmp_path):
    # 法规 issuer 超 128 → 整件拒收(skipped 记审计),不截断不 DataError
    class OverSource(FakeSource):
        def iter_laws(self):
            laws = super().iter_laws()
            laws[0]["ISSUE_AUTH_CN"] = "机" * 200  # issuer 列宽 128
            return laws

    stats = export.build_batch(OverSource(), tmp_path)
    assert any("issuer" in s for s in stats["skipped"])  # 审计留痕
    _, rows = _manifest_rows(tmp_path)
    assert "L-1" not in {r["source_doc_id"] for r in rows}  # 该件未入


def test_safe_filename_injective():
    # 不同 CODE 清洗后同基名,哈希后缀保证单射(不覆盖)
    a, b = export._safe_filename("A/B"), export._safe_filename("A?B")
    assert a != b and a.startswith("A_B-") and b.startswith("A_B-")


def test_stale_output_cleaned_on_reuse(tmp_path):
    export.build_batch(FakeSource(), tmp_path)
    assert (tmp_path / "cases.jsonl").exists()

    class NoCases(FakeSource):
        def iter_cases(self):
            return []

    export.build_batch(NoCases(), tmp_path)  # 复用同目录,本轮无案例
    assert not (tmp_path / "cases.jsonl").exists()  # 旧 cases.jsonl 已清(不残留再摄取)


def test_deleted_and_empty_laws_filtered(tmp_path):
    export.build_batch(FakeSource(), tmp_path)
    assert not list((tmp_path / "blocks").glob("L-DEL-*.jsonl"))
    assert not list((tmp_path / "blocks").glob("L-EMPTY-*.jsonl"))


def test_duplicate_code_rows_deduped(tmp_path):
    # 真数据(G1/G2):每节点 2 物理行,同 CODE 异 ID,内容一致 → 按 CODE 去重成 1 block
    class DupSource(FakeSource):
        def contents_for(self, law_code):
            base = super().contents_for(law_code)
            if law_code == "L-1":
                dup = dict(base[1])  # LC-021 的第二物理行(同 CODE)
                return base + [dup]
            return base

    export.build_batch(DupSource(), tmp_path)
    blocks = read_blocks(next((tmp_path / "blocks").glob("L-1-*.jsonl")))
    codes = [b.source_code for b in blocks if b.source_code]
    assert codes.count("LC-021") == 1  # 同 CODE 去重,不产双 block


def test_duplicate_code_content_conflict_warns(tmp_path):
    # 同 CODE 但内容不一致(异常/未来数据)→ 告警留痕(不静默丢失冲突;Codex 二轮 F8)
    class ConflictSource(FakeSource):
        def contents_for(self, law_code):
            base = super().contents_for(law_code)
            if law_code == "L-1":
                dup = dict(base[1])
                dup["CONTENT"] = "同 CODE 却不同的正文!"
                return base + [dup]
            return base

    stats = export.build_batch(ConflictSource(), tmp_path)
    assert any("内容冲突" in w for w in stats["warnings"])


def test_source_failure_preserves_existing_batch(tmp_path):
    import pytest

    out = tmp_path / "batch"
    export.build_batch(FakeSource(), out)
    old_manifest = (out / "manifest.xlsx").read_bytes()

    class BoomSource(FakeSource):
        def iter_laws(self):
            raise RuntimeError("源读取失败")

    with pytest.raises(RuntimeError):
        export.build_batch(BoomSource(), out)
    # 原子替换:staging 构建失败绝不销毁已有有效批次(Codex 二轮 F4)
    assert (out / "manifest.xlsx").read_bytes() == old_manifest
