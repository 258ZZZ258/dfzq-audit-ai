"""policy-query-mcp 的 stdio 入口。

跑法:``python -m query.mcp.server``(cwd 为本仓根;PG/Milvus 连接按 audit-ai 既有 config 读)。

定位:audit-ai 边界的**第三个消费方**(前两个是前端会话式 ``/api/query/v1/*`` 与
``POST /v1/query``)。与 ``api/routes_boundary.py`` 同层 —— 薄壳 + scope 注入,不改 AI 内核。

⚠ 协议版本是跨仓耦合点:消费方 dfzq-pi 的 MCP client 硬编码 ``protocolVersion``
``"2024-11-05"``,而它正是本 SDK ``HANDSHAKE_PROTOCOL_VERSIONS`` 里最老的一个。
dfzq-pi 侧有 ``test/cross-repo-handshake.test.ts`` 钉住这条。
"""

from __future__ import annotations

import json
from typing import Any

import anyio
import mcp.types as t
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from query.mcp import scope as scope_mod
from query.mcp.session import RunRegistry
from query.mcp.tools import search_policy

_TOOL_MODULES = (search_policy,)
_BY_NAME = {m.TOOL.name: m for m in _TOOL_MODULES}

_DEPS: dict[str, Any] | None = None


def _deps() -> dict[str, Any]:
    """进程级依赖,惰性建一次。

    照抄 ``query/query/api/service.py:204-228`` 的 ``QueryService.from_config()``,
    不自己发明连接串。

    这里**只放无 per-run 状态的东西**:``Retriever`` / ``PgIO`` 跨 run 复用是安全的,
    per-run 的只有 scope(每次 tools/call 解析)与 ``RunRegistry``(按 run_id 分桶)。
    """
    global _DEPS
    if _DEPS is None:
        from pipeline.config import load_config
        from pipeline.index.pg_io import PgIO
        from query.config import load_query_config
        from query.retrieve.hybrid import Retriever

        qcfg = load_query_config()
        _DEPS = {
            "retriever": Retriever.from_config(qcfg),
            "pg": PgIO.from_config(load_config()),
            "registry": RunRegistry(),
            "qcfg": qcfg,
        }
    return _DEPS


def _error(code: int, message: str) -> t.CallToolResult:
    """业务级错误。

    ⚠ **绝不携带可引用的条款正文**(dfzq-pi 规格 §6-1)。消费方采集 clause_id 时过滤掉
    ``isError: true`` 的工具结果 —— 带正文的错误响应会让模型看见并引用一个不在 clauseIds
    里的 id,输出契约的反幻觉校验随即把一次**真实检索到、真实引用**的答案判成幻觉。
    """
    return t.CallToolResult(
        content=[
            t.TextContent(
                type="text",
                text=json.dumps({"code": code, "message": message}, ensure_ascii=False),
            )
        ],
        is_error=True,
    )


async def _on_list_tools(ctx, params) -> t.ListToolsResult:  # noqa: ARG001
    return t.ListToolsResult(tools=[m.TOOL for m in _TOOL_MODULES])


async def _on_call_tool(ctx, params: t.CallToolRequestParams) -> t.CallToolResult:  # noqa: ARG001
    """工具分发。

    ``call()`` 是同步的:被包的 ``Retriever`` / PG 客户端全是同步客户端,``async def``
    只会包一层什么都不 await 的壳。stdio 传输本身也是串行的,当前一个进程服务一个 run。
    (S3 池化后若一进程服务多个并发 run,这会把它们串行化 —— 那时再议。)
    """
    arguments = params.arguments or {}
    module = _BY_NAME.get(params.name)
    if module is None:
        return _error(-32602, f"unknown tool: {params.name}")
    try:
        # fail-closed:授权层先判,任何一项缺失都不进业务逻辑。
        auth = scope_mod.parse_auth(arguments)
        result = module.call(auth, arguments, _deps())
    except scope_mod.ScopeError as exc:
        return _error(exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001
        # 下游(PG / Milvus / embedding)不可用:message 只含组件名与异常类型,
        # 不回显任何可能含条款正文的内容(同上 §6-1)。
        return _error(-32000, f"upstream failure in {params.name}: {type(exc).__name__}")
    return t.CallToolResult(
        content=[t.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
    )


def build_server() -> Server:
    return Server(
        "policy-query",
        version="0.1.0",
        instructions="制度查询:带授权范围的条款检索与权威详情回查。",
        on_list_tools=_on_list_tools,
        on_call_tool=_on_call_tool,
    )


async def _main() -> None:
    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    anyio.run(_main)


if __name__ == "__main__":
    main()
