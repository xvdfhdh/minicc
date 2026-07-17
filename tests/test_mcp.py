"""测试 MCP 系统：McpConnection + McpManager。"""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.mcp_server.mcp import McpConnection, McpManager


# ── helpers ──
def _mock_process():
    p = MagicMock()
    p.stdin = MagicMock()
    p.stdin.write = MagicMock()
    p.stdin.drain = AsyncMock()
    p.stdout = MagicMock()
    p.terminate = MagicMock()
    return p


def _connect_manual(conn: McpConnection):
    conn._process = _mock_process()


def _prep_future(result):
    """创建一个已完成的 future，返回 (loop_mock, future)"""
    mock_loop = MagicMock()
    future = asyncio.get_event_loop().create_future()
    future.set_result(result)
    mock_loop.create_future = MagicMock(return_value=future)
    return mock_loop, future


# ═══════════════════════════════════════════
# McpConnection 测试
# ═══════════════════════════════════════════
class TestMcpConnectionInit:
    def test_initial_state(self):
        conn = McpConnection("srv", {"command": "node"})
        assert conn.server_name == "srv"
        assert conn.config == {"command": "node"}
        assert conn._process is None
        assert conn._next_id == 1
        assert conn._pending == {}
        assert conn._reader_task is None


class TestMcpConnectionSendRequest:
    async def test_raises_when_not_connected(self):
        conn = McpConnection("srv", {"command": "node"})
        with pytest.raises(RuntimeError, match="not connected"):
            await conn.send_request("test", {})

    async def test_sends_jsonrpc_and_returns_result(self):
        conn = McpConnection("srv", {"command": "echo"})
        _connect_manual(conn)
        mock_loop, future = _prep_future({"tools": []})

        with patch("asyncio.get_event_loop", return_value=mock_loop):
            result = await conn.send_request("tools/list")

        assert result == {"tools": []}
        written = conn._process.stdin.write.call_args[0][0].decode("utf-8")
        msg = json.loads(written.strip())
        assert msg["jsonrpc"] == "2.0"
        assert msg["method"] == "tools/list"
        assert msg["id"] == 1

    async def test_propagates_runtime_errors(self):
        conn = McpConnection("srv", {"command": "node"})
        _connect_manual(conn)
        mock_loop = MagicMock()
        future = asyncio.get_event_loop().create_future()
        future.set_exception(RuntimeError("MCP error -32600"))
        mock_loop.create_future = MagicMock(return_value=future)

        with patch("asyncio.get_event_loop", return_value=mock_loop):
            with pytest.raises(RuntimeError, match="MCP error"):
                await conn.send_request("bad")


class TestMcpConnectionSendNotification:
    def test_silent_when_not_connected(self):
        conn = McpConnection("srv", {"command": "node"})
        conn.send_notification("test", {})

    def test_sends_without_id(self):
        conn = McpConnection("srv", {"command": "echo"})
        _connect_manual(conn)
        conn.send_notification("notifications/initialized")
        written = conn._process.stdin.write.call_args[0][0].decode("utf-8")
        msg = json.loads(written.strip())
        assert msg["method"] == "notifications/initialized"
        assert "id" not in msg


class TestMcpConnectionInitialize:
    async def test_sends_initialize_and_notification(self):
        conn = McpConnection("srv", {"command": "node"})
        _connect_manual(conn)
        mock_loop, _ = _prep_future({"result": {}})

        with patch("asyncio.get_event_loop", return_value=mock_loop):
            await conn.initialize()

        all_writes = [c[0][0].decode("utf-8") for c in conn._process.stdin.write.call_args_list]
        msgs = []
        for w in all_writes:
            for line in w.strip().split("\n"):
                if line:
                    msgs.append(json.loads(line))
        methods = [m.get("method") for m in msgs]
        assert "initialize" in methods
        assert "notifications/initialized" in methods


class TestMcpConnectionListTools:
    async def test_returns_formatted_tools(self):
        conn = McpConnection("srv", {"command": "node"})
        _connect_manual(conn)
        mock_loop, _ = _prep_future({"tools": [
            {"name": "read", "description": "Read", "inputSchema": {"type": "object"}},
        ]})

        with patch("asyncio.get_event_loop", return_value=mock_loop):
            tools = await conn.list_tools()

        assert len(tools) == 1
        assert tools[0]["name"] == "read"
        assert tools[0]["serverName"] == "srv"
        assert tools[0]["inputSchema"] == {"type": "object"}

    async def test_empty_when_no_tools(self):
        conn = McpConnection("srv", {"command": "node"})
        _connect_manual(conn)
        mock_loop, _ = _prep_future({"result": {}})

        with patch("asyncio.get_event_loop", return_value=mock_loop):
            tools = await conn.list_tools()
        assert tools == []

    async def test_empty_when_result_none(self):
        conn = McpConnection("srv", {"command": "node"})
        _connect_manual(conn)
        mock_loop, _ = _prep_future({"result": None})

        with patch("asyncio.get_event_loop", return_value=mock_loop):
            tools = await conn.list_tools()
        assert tools == []


class TestMcpConnectionCallTool:
    async def test_extracts_text_content(self):
        conn = McpConnection("srv", {"command": "node"})
        _connect_manual(conn)
        mock_loop, _ = _prep_future({
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "text", "text": "World"},
            ]
        })

        with patch("asyncio.get_event_loop", return_value=mock_loop):
            result = await conn.call_tool("greet", {"name": "test"})
        assert result == "Hello\nWorld"

    async def test_falls_back_to_json(self):
        conn = McpConnection("srv", {"command": "node"})
        _connect_manual(conn)
        mock_loop, _ = _prep_future({"status": "ok"})

        with patch("asyncio.get_event_loop", return_value=mock_loop):
            result = await conn.call_tool("status", {})
        assert result == json.dumps({"status": "ok"})


class TestMcpConnectionClose:
    @pytest.mark.asyncio
    async def test_cleans_up_resources(self):
        conn = McpConnection("srv", {"command": "echo"})
        process = _mock_process()
        conn._process = process
        conn._pending[99] = asyncio.get_event_loop().create_future()

        await conn.close()

        assert conn._reader_task is None
        assert conn._pending == {}
        assert conn._process is None
        process.stdin.close.assert_called_once()
        process.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_safe_before_connect(self):
        conn = McpConnection("srv", {"command": "node"})
        await conn.close()
        assert conn._process is None


# ═══════════════════════════════════════════
# McpManager 测试
# ═══════════════════════════════════════════
class TestMcpManagerInit:
    def test_initial_state(self):
        mgr = McpManager()
        assert mgr._connections == {}
        assert mgr._tools == []
        assert mgr._connected is False


class TestMcpManagerIsValidConfig:
    def test_valid(self):
        assert McpManager._is_valid_config({"command": "node", "args": ["-e"]}) is True

    def test_missing_command(self):
        assert McpManager._is_valid_config({"args": ["-e"]}) is False

    def test_not_dict(self):
        assert McpManager._is_valid_config("string") is False
        assert McpManager._is_valid_config(None) is False


class TestMcpManagerMergeConfigFile:
    @pytest.fixture
    def manager(self):
        return McpManager()

    def test_merges_valid_servers(self, manager, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({
            "mcpServers": {
                "fs": {"command": "npx", "args": ["-y", "server-filesystem"]},
            }
        }))
        target: dict = {}
        manager._merge_config_file(path, target)
        assert "fs" in target
        assert target["fs"]["command"] == "npx"

    def test_skips_missing_file(self, manager, tmp_path):
        target: dict = {}
        manager._merge_config_file(tmp_path / "nope.json", target)
        assert target == {}

    def test_skips_invalid_json(self, manager, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json {{{")
        target: dict = {}
        manager._merge_config_file(path, target)
        assert target == {}

    def test_direct_mapping(self, manager, tmp_path):
        path = tmp_path / ".mcp.json"
        path.write_text(json.dumps({"my-srv": {"command": "python"}}))
        target: dict = {}
        manager._merge_config_file(path, target)
        assert "my-srv" in target

    def test_project_overrides_global(self, manager, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        global_path = tmp_path / "home" / ".claude" / "settings.json"
        global_path.parent.mkdir(parents=True, exist_ok=True)
        global_path.write_text(json.dumps({
            "mcpServers": {
                "shared": {"command": "old-cmd"},
                "global-only": {"command": "global-cmd"},
            }
        }))

        project_path = tmp_path / ".claude" / "settings.json"
        project_path.parent.mkdir(parents=True, exist_ok=True)
        project_path.write_text(json.dumps({
            "mcpServers": {
                "shared": {"command": "new-cmd", "args": ["--verbose"]},
            }
        }))

        monkeypatch.chdir(tmp_path)
        configs = manager._load_configs()
        assert configs["shared"]["command"] == "new-cmd"
        assert configs["shared"]["args"] == ["--verbose"]
        assert configs["global-only"]["command"] == "global-cmd"


class TestMcpManagerGetToolDefinitions:
    def test_prefixed_names(self):
        mgr = McpManager()
        mgr._tools = [
            {"name": "read", "description": "Read", "inputSchema": {}, "serverName": "fs"},
            {"name": "write", "description": "", "inputSchema": {"type": "object"}, "serverName": "fs"},
        ]
        defs = mgr.get_tool_definitions()
        assert defs[0]["name"] == "mcp__fs__read"
        assert defs[1]["name"] == "mcp__fs__write"
        assert defs[0]["input_schema"] == {"type": "object", "properties": {}}  # {} is falsy → default

    def test_fallback_description(self):
        mgr = McpManager()
        mgr._tools = [{"name": "cmd", "description": "", "inputSchema": {}, "serverName": "s"}]
        defs = mgr.get_tool_definitions()
        assert "MCP tool cmd from s" in defs[0]["description"]


class TestMcpManagerIsMcpTool:
    @pytest.mark.parametrize("name,expected", [
        ("mcp__fs__read", True),
        ("mcp__", True),
        ("read_file", False),
        ("mcp", False),
        ("", False),
    ])
    def test_is_mcp_tool(self, name, expected):
        assert McpManager().is_mcp_tool(name) is expected


class TestMcpManagerCallTool:
    @pytest.mark.asyncio
    async def test_routes_to_connection(self):
        mgr = McpManager()
        mock_conn = MagicMock()
        mock_conn.call_tool = AsyncMock(return_value="hello")
        mgr._connections["fs"] = mock_conn

        result = await mgr.call_tool("mcp__fs__read_file", {"path": "/a"})
        assert result == "hello"
        mock_conn.call_tool.assert_called_once_with("read_file", {"path": "/a"})

    @pytest.mark.asyncio
    async def test_double_underscore_in_tool_name(self):
        mgr = McpManager()
        mock_conn = MagicMock()
        mock_conn.call_tool = AsyncMock(return_value="ok")
        mgr._connections["db"] = mock_conn

        result = await mgr.call_tool("mcp__db__schema__list", {})
        assert result == "ok"
        mock_conn.call_tool.assert_called_once_with("schema__list", {})

    def test_invalid_tool_name_raises(self):
        mgr = McpManager()
        with pytest.raises(ValueError, match="Invalid MCP tool name"):
            asyncio.get_event_loop().run_until_complete(
                mgr.call_tool("no_prefix", {})
            )

    @pytest.mark.asyncio
    async def test_unknown_server_raises(self):
        mgr = McpManager()
        with pytest.raises(ValueError, match="not connected"):
            await mgr.call_tool("mcp__unknown__tool", {})


class TestMcpManagerLoadAndConnect:
    @pytest.mark.asyncio
    async def test_idempotent(self):
        mgr = McpManager()
        mgr._connected = True
        await mgr.load_and_connect()
        assert mgr._connections == {}

    @pytest.mark.asyncio
    async def test_no_configs_early_return(self, tmp_path, monkeypatch):
        mgr = McpManager()
        monkeypatch.chdir(tmp_path)
        await mgr.load_and_connect()
        assert mgr._connections == {}

    @pytest.mark.asyncio
    async def test_failure_is_handled(self, tmp_path, monkeypatch):
        mgr = McpManager()
        monkeypatch.chdir(tmp_path)
        project_path = tmp_path / ".claude" / "settings.json"
        project_path.parent.mkdir(parents=True, exist_ok=True)
        project_path.write_text(json.dumps({
            "mcpServers": {
                "bad": {"command": "nonexistent"},
            }
        }))

        with patch.object(McpConnection, "connect", AsyncMock(side_effect=FileNotFoundError)):
            await mgr.load_and_connect()
        assert mgr._connections == {}
