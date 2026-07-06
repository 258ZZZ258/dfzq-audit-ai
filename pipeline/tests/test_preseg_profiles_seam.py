"""T1 配置缝对拍:profiles.yaml 承载 QC 启用集/阈值覆盖/case_ref_source 后,
四个既有 profile 行为与硬编码基线逐项一致(零变更回归保证,SPEC-PRESEG §6)。"""

from __future__ import annotations

import pytest

from pipeline.config import ProfileConfig, load_config
from pipeline.qc import indicators as ind
from pipeline.qc.gate import evaluate

# ── 改造前行为快照(硬编码基线,来自 indicators.py:222-232 @ c0ba851)──
LEGACY_SETS = {
    "P-INT": [
        "clause_coverage", "clause_continuity", "hierarchy_legality",
        "page_anchor_complete", "table_consistency", "text_quality",
        "extraction_sufficiency",
    ],
    "P-EXT": [
        "clause_coverage", "clause_continuity", "hierarchy_legality",
        "page_anchor_complete", "table_consistency", "text_quality",
        "extraction_sufficiency",
    ],
    "P-QA": ["qa_pair_completeness", "page_anchor_complete", "text_quality"],
    "P-CASE": ["page_anchor_complete", "text_quality"],
}
LEGACY_SAMPLING = {"P-INT": 1.0, "P-EXT": 0.0, "P-QA": 0.0, "P-CASE": 0.0}


def _names(fns: list) -> list[str]:
    return [f.__name__ for f in fns]


class TestParityWithLegacy:
    """对拍:yaml 驱动的启用集 == 改造前硬编码集,逐 profile 逐指标。"""

    @pytest.mark.parametrize("ct", ["P-INT", "P-EXT", "P-QA", "P-CASE"])
    def test_yaml_driven_set_equals_legacy(self, ct):
        cfg = load_config()
        assert _names(ind.indicators_for(ct, cfg.profiles[ct])) == LEGACY_SETS[ct]

    @pytest.mark.parametrize("ct", ["P-INT", "P-EXT", "P-QA", "P-CASE"])
    def test_sampling_rate_unchanged(self, ct):
        assert load_config().profiles[ct].sampling_rate == LEGACY_SAMPLING[ct]

    def test_unknown_corpus_no_profile_falls_back_all_seven(self):
        assert _names(ind.indicators_for("P-UNKNOWN", None)) == LEGACY_SETS["P-INT"]
        # 旧签名调用(不带 profile)不破——既有调用点/测试零改动
        assert _names(ind.indicators_for("P-QA")) == LEGACY_SETS["P-QA"]

    def test_profile_without_qc_indicators_falls_back_legacy(self):
        # 只有 sampling_rate 的旧形态 ProfileConfig(向后兼容)→ 回退硬编码默认
        legacy_shape = ProfileConfig(sampling_rate=0.5)
        assert _names(ind.indicators_for("P-QA", legacy_shape)) == LEGACY_SETS["P-QA"]


class TestConfigSeam:
    """新配置能力:启用集/阈值覆盖/case_ref_source。"""

    def test_registry_covers_all_indicators(self):
        assert set(ind.INDICATOR_REGISTRY) == {
            *LEGACY_SETS["P-INT"], "qa_pair_completeness",
        }

    def test_custom_indicator_list_respected(self):
        p = ProfileConfig(sampling_rate=0.0, qc_indicators=["text_quality"])
        assert _names(ind.indicators_for("P-ANY", p)) == ["text_quality"]

    def test_unknown_indicator_name_fails_fast(self):
        p = ProfileConfig(sampling_rate=0.0, qc_indicators=["no_such_indicator"])
        with pytest.raises(ValueError, match="no_such_indicator"):
            ind.indicators_for("P-ANY", p)

    def test_threshold_override_applied(self, minimal_ir):
        cfg = load_config()
        p = ProfileConfig(
            sampling_rate=0.0,
            qc_indicators=["page_anchor_complete"],
            qc_threshold_overrides={"page_anchor_complete_min": 0.0},
        )
        report = evaluate(minimal_ir, cfg.qc, "P-ANY", profile=p)
        (r,) = report.indicators
        assert r.threshold == 0.0 and r.passed  # 阈值被覆盖 → 无页码也过

    def test_threshold_override_does_not_mutate_global(self, minimal_ir):
        cfg = load_config()
        base = cfg.qc.page_anchor_complete_min
        p = ProfileConfig(
            sampling_rate=0.0,
            qc_indicators=["page_anchor_complete"],
            qc_threshold_overrides={"page_anchor_complete_min": 0.0},
        )
        evaluate(minimal_ir, cfg.qc, "P-ANY", profile=p)
        assert cfg.qc.page_anchor_complete_min == base  # 副本覆盖,不改全局

    def test_case_ref_source_defaults(self):
        cfg = load_config()
        assert ProfileConfig(sampling_rate=0.0).case_ref_source == "llm"
        assert cfg.profiles["P-CASE"].case_ref_source == "llm"  # 现状:LLM 通道

    def test_evaluate_without_profile_is_legacy(self, minimal_ir):
        cfg = load_config()
        report = evaluate(minimal_ir, cfg.qc, "P-CASE")
        assert [i.key for i in report.indicators] == LEGACY_SETS["P-CASE"]


@pytest.fixture
def minimal_ir():
    """最小 IR:一个无页码文本块(page_anchor_complete=0.0 的确定性输入)。"""
    from common.ir import Block, BlockType, IRDocument

    return IRDocument(
        doc_version_id="dv-preseg-t1", source_format="docx",
        blocks=[Block(index=0, type=BlockType.PARAGRAPH, text="测试文本块", page=None)],
    )
