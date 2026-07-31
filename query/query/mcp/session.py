"""per-run 已检索 clause_id 白名单。

**这不是缓存,是权限边界。** T4 `get_clause_detail` / T5 `diff_policy_versions` 只允许取
本 run 检索过的 id —— 否则 agent 可以绕过带 scope 的检索路,直接按 id 从 PG 拉取任意条款
(dfzq-pi 规格 §2.5 的 widening 禁令)。

**按 run_id 分桶,不靠进程隔离。** 今天一 run 一 MCP 进程,一个进程内的全局集合看似够用;
但 S3 会按 specId 池化 MCP 进程(dfzq-pi 规格 §3.3),到那时一个进程服务多个并发 run,
全局集合会让它们互取对方的条款详情 —— 那是权限越界,而且不会有任何报错。
"""

from __future__ import annotations

from collections.abc import Iterable


class RunRegistry:
    """`run_id → 本 run 已检索到的 clause_id 集合`。

    生命周期跟随 MCP server 进程。一 run 一进程时进程退出即回收;池化后由调用方在 run
    结束时调 `forget()`。**没有 TTL 淘汰** —— 池化落地时要一并设计,否则长命进程会无界增长。
    """

    def __init__(self) -> None:
        self._by_run: dict[str, set[str]] = {}

    def record(self, run_id: str, clause_ids: Iterable[str]) -> None:
        """登记本次检索真正返回给模型的 id。

        累加而非覆盖:一个 run 会检索多次,后一次冲掉前一次会让模型引用早先命中的 id 时被拒。
        """
        self._by_run.setdefault(run_id, set()).update(clause_ids)

    def allowed(self, run_id: str) -> set[str]:
        """本 run 可取详情的 id。

        未知 run 返回空集(fail-closed),不是「随便取」。
        返回**副本** —— 调用方改它不得改到内部状态,那会让白名单被静默放宽。
        """
        return set(self._by_run.get(run_id, ()))

    def forget(self, run_id: str) -> None:
        self._by_run.pop(run_id, None)
