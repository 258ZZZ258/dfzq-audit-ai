"""预切块批次生产摄取入口(``python -m pipeline.preseg_ingest <batch_dir>``)。

甲方预切块语料(CP-010)的**生产/运维灌库入口**:登记一个预切块批次(接收契约见 SPEC-PRESEG §3——
``manifest.xlsx`` + ``blocks/<filename>.jsonl`` + 可选 ``cases.jsonl``)并驱动整批
``REGISTERED → … → INDEXED``。仓外转换脚本把源系统实际形态吸收成该批次目录后,调用本入口即可灌库。

**为何是 ``python -m`` 而非 demo CLI 子命令**:生产构建(``PIPELINE_BUILD_PROFILE=production``)剔除
demo/demo-web console_scripts 以减人操作面,但 ``python -m`` 模块执行不受影响——故预切块生产灌库
走本模块,给运维/转换脚本一个稳定入口,而非依赖测试里手接的私有函数。登记 + 驱动两步与 T11
端到端测试(``test_preseg_e2e``)完全一致。

**推进深度取决于配置**:B 模式(``[toggles] auto_confirm_meta_no_conflict`` 开,默认)用 worker 上下文——
无元数据冲突的新件当场过 s5 直达 INDEXED + finalize,故本入口在 B 模式下**需嵌入模型 + Milvus**;
A 模式用轻上下文,至多推进到 ``META_REVIEW`` 人工闸(到终态仍需 worker 上下文再驱动)。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select
from ulid import ULID

from common.pg_models import DocVersion
from pipeline.cli import _drive_batch, _drive_context, _print_status
from pipeline.config import load_config
from pipeline.stages.s0_register import register_preseg_batch


def run(
    batch_dir: Path, manifest: Path | None = None, *, batch_id: str | None = None
) -> int:
    """登记 + 驱动一个预切块批次;返回进程退出码(0=接收成功,1=整批拒收)。

    ``batch_id`` 省略→新 ULID;重跑同一批次填原 id(源幂等键 source_doc_id+content_hash 去重,
    死态可修复重提,见 ``s0_register._DEDUP_DEAD_STATES``)。
    """
    cfg = load_config()
    pg, ctx = _drive_context(cfg)  # B 模式→worker(过 s5+finalize);A 模式→轻上下文(至多 META_REVIEW)
    mpath = manifest or (batch_dir / "manifest.xlsx")
    bid = batch_id or str(ULID())
    report = register_preseg_batch(ctx, bid, batch_dir, mpath)
    if not report.accepted:
        print(f"✗ 整批拒收:{report.reject_reason}")
        return 1
    counts = report.counts()
    print(f"S0 登记 batch={bid}:" + "  ".join(f"{k}={v}" for k, v in counts.items()))
    for w in report.warnings:
        print(f"  ⚠ {w}")
    steps = _drive_batch(pg, ctx, bid)
    print(f"→ worker 推进 {steps} 步")
    # 精确桥接在案例 S4(META_REVIEW)当下查 chunks。**同批无需批后对账**:``run_until_idle`` 逐轮
    # lockstep 推进,同批法规 S3(建 chunk)在案例 S4 之前一轮完成,``resolve_exact`` 直接命中
    # (转换脚本把法规+案例放同一批,故走此路)。**边缘**:案例在更早批、被引法规在更晚批到,或引用
    # upcoming 后经 activate 转 effective 的法规 → 该案退 fuzzy(有效兜底);需精确时**重灌该案例**
    # (revise_replace 重跑 S4,此时法规已在)即升级 exact。见 SPEC-PRESEG §8.3。
    with pg.session() as s:
        docs = list(s.scalars(select(DocVersion).where(DocVersion.batch_id == bid)))
    _print_status(docs)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m pipeline.preseg_ingest",
        description="预切块批次生产摄取:登记 + 驱动整批(接收契约见 SPEC-PRESEG §3)。",
    )
    ap.add_argument(
        "batch_dir", type=Path, help="批次目录(含 manifest.xlsx + blocks/ + 可选 cases.jsonl)"
    )
    ap.add_argument(
        "-m", "--manifest", type=Path, default=None,
        help="manifest.xlsx(默认 <batch_dir>/manifest.xlsx)",
    )
    ap.add_argument(
        "--batch-id", default=None, help="批次 id(默认新 ULID;重跑同批填原 id 以走源幂等)"
    )
    args = ap.parse_args(argv)
    if not args.batch_dir.is_dir():
        ap.error(f"批次目录不存在:{args.batch_dir}")
    return run(args.batch_dir, args.manifest, batch_id=args.batch_id)


if __name__ == "__main__":
    sys.exit(main())
