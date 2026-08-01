"""POLICY_MCP_AUDIT_LOG 未配置即启动失败。

理由不是洁癖:未配置时 _audit 只写 stderr,而消费方 McpClient **不转发子进程 stderr**
(只读走以防管道堵塞、留一份有界诊断尾巴)⇒ 审计日志到不了服务日志,A6「pi trajectory
与 MCP 侧日志逐条一致」**无从对账**。静默降级成「日志写了但没人看得见」比启动失败糟得多。
"""

from __future__ import annotations

import pytest

from query.mcp import server as server_mod


def test_missing_audit_log_env_fails_startup(monkeypatch):
    monkeypatch.delenv("POLICY_MCP_AUDIT_LOG", raising=False)
    with pytest.raises(RuntimeError) as e:
        server_mod.require_audit_log_path()
    assert "POLICY_MCP_AUDIT_LOG" in str(e.value)


def test_blank_audit_log_env_fails_startup(monkeypatch):
    # 空串与未设置同样致命 —— open("") 抛的是 FileNotFoundError,
    # 而 _audit 把 OSError 吞掉了,于是空串会静默退化成「只写 stderr」。
    monkeypatch.setenv("POLICY_MCP_AUDIT_LOG", "")
    with pytest.raises(RuntimeError):
        server_mod.require_audit_log_path()


def test_configured_audit_log_env_passes(monkeypatch, tmp_path):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("POLICY_MCP_AUDIT_LOG", str(path))
    assert server_mod.require_audit_log_path() == str(path)
