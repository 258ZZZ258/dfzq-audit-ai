"""源系统「效力状态」→ version_status 四态映射器(CP-010,决策 D3 按通道分权威)。

仅 P-PRESEG 通道消费:映射命中 → 直写 version_status + ``version_status_source="source"``;
**未知值不猜**(SPEC §4 映射表草案,值域待真样例修订)→ 保持默认 effective、source 留空、
进 meta_confirm 队列人工定。自建通道(版本链推导)行为零改。

注:映射在 **S0 登记时**应用(而非 PLAN 原划的 S4)——manifest 原值只在 S0 可见,
S4 读 PG 拿不到;s5/finalize 对 ``version_status_source=="source"`` 的尊重在 T8 落地。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 源效力状态 → 四态。⚠ 值域是对甲方 UI 用词的工程假设,真导出到手须校订(Ask-first 增删)。
STATUS_MAP: dict[str, str] = {
    "现行有效": "effective",
    "有效": "effective",
    "已废止": "abolished",
    "废止": "abolished",
    "失效": "abolished",
    "已失效": "abolished",
    "被替代": "superseded",
    "已被替代": "superseded",
    "已修订": "superseded",
    "已被修改": "superseded",
    "尚未生效": "upcoming",
    "未生效": "upcoming",
}


@dataclass(frozen=True)
class MappedStatus:
    status: str | None  # 命中的 version_status;未知值 None(调用方保默认+入队)
    raw: str  # 源原值(入队 evidence / 留痕)
    needs_review: bool  # 未知值 → meta_confirm


def map_effective_status(raw: object) -> MappedStatus:
    """源效力状态原值 → MappedStatus。空值也视为未知(源字段本应必填,缺失须人工看)。"""
    s = str(raw or "").strip()
    mapped = STATUS_MAP.get(s)
    return MappedStatus(status=mapped, raw=s, needs_review=mapped is None)
