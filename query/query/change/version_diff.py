"""R2 条款级 diff(§6.2 变更内容):按 ``clause_path_norm`` 对齐两版本条款。纯函数、零 LLM。

仅到**条款级**:两侧 text 不等=changed、仅新=added、仅旧=removed、相等不计(字句级 diff 后续)。
若某条在新旧版本的路径不同、但正文严格相同且两侧正文都唯一，则记为 moved（位置调整）。
移动匹配先于同路径内容比较：第 4/5 条互换时两侧路径仍重叠，若先按路径比较会误报两个修改。
入参元素含 ``clause_path_norm`` / ``text`` / ``seq``(由 ``r2_change.fetch_clause_chunks`` 提供)。
**同 ``clause_path_norm`` 的多子块**(切块器拆超长条款/表格)按 ``seq`` 升序**聚合拼接**后比较——
后续子块差异不漏。输出按 path 字符串序(确定性)。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClauseChange:
    clause_path_norm: str
    kind: str  # added | removed | changed | moved
    old_text: str | None
    new_text: str | None
    old_clause_path_norm: str | None = None
    new_clause_path_norm: str | None = None


def _key(x) -> str:
    return x["clause_path_norm"] if isinstance(x, dict) else x.clause_path_norm


def _text(x) -> str:
    return (x["text"] if isinstance(x, dict) else x.text) or ""


def _seq(x) -> int:
    v = x["seq"] if isinstance(x, dict) else getattr(x, "seq", 0)
    return v if v is not None else 0


def _aggregate(chunks: list) -> dict[str, str]:
    """同 clause_path_norm 的多子块按 seq 升序拼接(覆盖切块器对超长条款/表格的拆分)。"""
    groups: dict[str, list] = {}
    for c in chunks:
        groups.setdefault(_key(c), []).append(c)
    return {
        path: "\n".join(_text(i) for i in sorted(items, key=_seq))
        for path, items in groups.items()
    }


def diff_clauses(old: list, new: list) -> list[ClauseChange]:
    """聚合子块 → 对齐 → 变更项(added/removed/changed/moved),按路径字符串稳定输出。"""
    old_map = _aggregate(old)
    new_map = _aggregate(new)

    changes: list[ClauseChange] = []
    # 全局先配对两侧唯一的完全相同正文。不能只看 old_only/new_only：条款互换位置时
    # 两个路径依旧同时存在，先做同路径比较会把纯位置调整误判成 changed。
    old_text_paths: dict[str, list[str]] = {}
    new_text_paths: dict[str, list[str]] = {}
    for path, text in old_map.items():
        old_text_paths.setdefault(text, []).append(path)
    for path, text in new_map.items():
        new_text_paths.setdefault(text, []).append(path)
    moved_pairs = {
        new_paths[0]: old_paths[0]
        for text, old_paths in old_text_paths.items()
        if len(old_paths) == 1
        and len(new_paths := new_text_paths.get(text, [])) == 1
        and old_paths[0] != new_paths[0]
    }
    moved_old_paths = set(moved_pairs.values())
    moved_new_paths = set(moved_pairs)

    old_only = {
        path: text
        for path, text in old_map.items()
        if path not in new_map and path not in moved_old_paths
    }
    new_only = {
        path: text
        for path, text in new_map.items()
        if path not in old_map and path not in moved_new_paths
    }

    for path in sorted(set(old_map) & set(new_map)):
        if path in moved_old_paths or path in moved_new_paths:
            continue
        if old_map[path] != new_map[path]:
            changes.append(ClauseChange(path, "changed", old_map[path], new_map[path]))
    for path in sorted(moved_pairs):
        old_path = moved_pairs[path]
        changes.append(
            ClauseChange(
                path,
                "moved",
                old_map[old_path],
                new_map[path],
                old_clause_path_norm=old_path,
                new_clause_path_norm=path,
            )
        )
    for path in sorted(new_only):
        changes.append(ClauseChange(path, "added", None, new_map[path]))
    for path in sorted(old_only):
        changes.append(ClauseChange(path, "removed", old_map[path], None))

    return sorted(changes, key=lambda change: (change.clause_path_norm, change.kind))
