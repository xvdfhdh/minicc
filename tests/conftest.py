"""共享 pytest fixture。"""
from __future__ import annotations
import pytest
from pathlib import Path
from unittest.mock import MagicMock


@pytest.fixture
def temp_memory_dir(tmp_path):
    """临时记忆目录。"""
    d = tmp_path / "memory"
    d.mkdir()
    return d


@pytest.fixture
def temp_project_dir(tmp_path):
    """模拟项目目录，含 .git 和 CLAUDE.md。"""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    return project


@pytest.fixture
def mock_openai_client():
    """模拟 OpenAI 客户端。"""
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = MagicMock()
    return client


@pytest.fixture
def mock_anthropic_client():
    """模拟 Anthropic 客户端。"""
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = MagicMock()
    client.messages.stream = MagicMock()
    return client


@pytest.fixture
def sample_tool_definitions():
    """示例工具定义列表。"""
    return [
        {
            "name": "read_file",
            "description": "Read a file",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                },
                "required": ["file_path"],
            },
        },
        {
            "name": "write_file",
            "description": "Write a file",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file_path", "content"],
            },
        },
        {
            "name": "run_shell",
            "description": "Run shell command",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    ]
