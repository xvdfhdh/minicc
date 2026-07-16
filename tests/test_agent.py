"""测试 Agent 核心逻辑（使用 mock 客户端）。"""
from __future__ import annotations
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


class TestAgentStripOrphaned:
    """_strip_orphaned_tool_messages() 测试"""

    def test_no_orphan_clean(self):
        from src.main.agent import Agent
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = Agent._strip_orphaned_tool_messages(msgs)
        assert len(result) == 2

    def test_paired_tool_calls_preserved(self):
        from src.main.agent import Agent
        msgs = [
            {"role": "user", "content": "do X"},
            {"role": "assistant", "tool_calls": [{"id": "call_1", "function": {"name": "read_file"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        ]
        result = Agent._strip_orphaned_tool_messages(msgs)
        assert len(result) == 3

    def test_orphaned_tool_call_removed(self):
        from src.main.agent import Agent
        msgs = [
            {"role": "user", "content": "do X"},
            {"role": "assistant", "tool_calls": [{"id": "orphan_call", "function": {"name": "read_file"}}]},
            {"role": "user", "content": "next Q"},
        ]
        result = Agent._strip_orphaned_tool_messages(msgs)
        roles = [m["role"] for m in result]
        assert "assistant" not in roles or all(
            m.get("tool_calls") is None for m in result if m["role"] == "assistant"
        )

    def test_orphaned_tool_result_removed(self):
        from src.main.agent import Agent
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "orphan_call", "content": "orphan result"},
        ]
        result = Agent._strip_orphaned_tool_messages(msgs)
        roles = [m.get("role") for m in result]
        assert "tool" not in roles

    def test_mixed_scenario(self):
        from src.main.agent import Agent
        msgs = [
            {"role": "system", "content": "..."},
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "tool_calls": [{"id": "call_1"}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "result1"},
            {"role": "assistant", "tool_calls": [{"id": "orphan"}]},
            {"role": "user", "content": "Q2"},
        ]
        result = Agent._strip_orphaned_tool_messages(msgs)
        tools_present = [m for m in result if m.get("role") == "tool"]
        assert len(tools_present) == 1
        assert tools_present[0]["tool_call_id"] == "call_1"


class TestAgentInitialization:
    """Agent 初始化测试"""

    def test_agent_init_openai(self, monkeypatch, tmp_path):
        # 避免读取真实的 MEMORY.md
        monkeypatch.setattr("src.memory.memory.MEMORY_DIR", tmp_path / "fake_mem")
        from src.main.agent import Agent
        agent = Agent(model="gpt-4o", api_key="test-key", use_openai=True)
        assert agent.model == "gpt-4o"
        assert agent.use_openai is True
        assert agent.effective_window == 200000
        assert len(agent._openai_messages) >= 1
        assert agent._openai_messages[0]["role"] == "system"

    def test_agent_init_anthropic(self, monkeypatch, tmp_path):
        monkeypatch.setattr("src.memory.memory.MEMORY_DIR", tmp_path / "fake_mem")
        from src.main.agent import Agent
        agent = Agent(model="claude-sonnet-4-20250514", api_key="test-key", use_openai=False)
        assert agent.use_openai is False
        assert len(agent._anthropic_messages) == 0

    def test_agent_clear_history(self, monkeypatch, tmp_path):
        monkeypatch.setattr("src.memory.memory.MEMORY_DIR", tmp_path / "fake_mem")
        from src.main.agent import Agent
        agent = Agent(model="gpt-4o", api_key="test-key", use_openai=True)
        agent._openai_messages.append({"role": "user", "content": "hi"})
        agent._openai_messages.append({"role": "assistant", "content": "hello"})
        agent.clear_history()
        assert len(agent._openai_messages) == 1
        assert agent._openai_messages[0]["role"] == "system"


class TestAgentCompact:
    """Agent compact 测试"""

    def test_compact_too_few_messages(self, monkeypatch, tmp_path):
        monkeypatch.setattr("src.memory.memory.MEMORY_DIR", tmp_path / "fake_mem")
        from src.main.agent import Agent
        agent = Agent(model="gpt-4o", api_key="test-key", use_openai=True)
        agent._openai_messages.append({"role": "user", "content": "hi"})
        agent._openai_messages.append({"role": "assistant", "content": "hello"})

        import asyncio
        result = asyncio.new_event_loop().run_until_complete(agent._compact_openai())
        assert result is None


class TestAgentSession:
    """Agent 会话管理测试"""

    def test_restore_session_openai(self, monkeypatch, tmp_path):
        monkeypatch.setattr("src.memory.memory.MEMORY_DIR", tmp_path / "fake_mem")
        from src.main.agent import Agent
        agent = Agent(model="gpt-4o", api_key="test-key", use_openai=True)
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        agent.restore_session({"openaiMessages": msgs})
        assert len(agent._openai_messages) == 3

    def test_message_count(self, monkeypatch, tmp_path):
        monkeypatch.setattr("src.memory.memory.MEMORY_DIR", tmp_path / "fake_mem")
        from src.main.agent import Agent
        agent = Agent(model="gpt-4o", api_key="test-key", use_openai=True)
        agent._openai_messages.append({"role": "user", "content": "hi"})
        agent._openai_messages.append({"role": "assistant", "content": "hello"})
        count = agent._get_message_count()
        assert count == 2
