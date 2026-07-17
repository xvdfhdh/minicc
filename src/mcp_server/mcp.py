from __future__ import annotations  # type: ignore[BSK-PARSE]
import asyncio
from pathlib import Path
import subprocess
from typing import Any, Callable, Optional, TypedDict
import json
import os


class McpToolInfo(TypedDict, total=False):
    name: str
    description: str
    inputSchema: dict[str, Any]
    serverName: str


class McpServerConfig(TypedDict, total=False):
    command: str
    args: list[str]
    env: dict[str, str]


class McpConnection:
    def __init__(self, server_name: str, config: McpServerConfig) -> None:
        self.server_name = server_name
        self.config = config

        self._process: Optional[asyncio.subprocess.Process] = None
        self._next_id: int = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None

    async def connect(self)->None:
        env  = {**os.environ, **(self.config.get("env") or {})}
        command =self.config["command"]
        args=self.config.get("args") or []

        self._process = await asyncio.create_subprocess_exec(
            command, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        async def _read_lines():
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                line = line.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    msg=json.loads(line)
                    if "id" in msg and msg["id"] in self._pending:
                        future=self._pending.pop(msg["id"])
                        if "error" in msg:
                            error =msg["error"]
                            future.set_exception(
                                RuntimeError(f"MCP error {error.get('code')} {error.get('message')}")

                            )
                        else:
                            future.set_result(msg.get("result"))
                except(json.JSONDecodeError,RuntimeError):
                    pass
                    

        self._reader_task = asyncio.create_task(_read_lines())
    
    async def send_request(self,method:str,params:dict|None=None)->Any:

        if params is None:
            params={}
        
        if not self._process or self._process.stdin is None:
            raise RuntimeError(f"MCP server '{self.server_name}' is not connected")
        
        id=self._next_id
        self._next_id+=1

        future=asyncio.get_event_loop().create_future()
        self._pending[id]=future

        msg= json.dumps({"jsonrpc":"2.0","id":id,"method":method,"params":params})+"\n"

        self._process.stdin.write(msg.encode("utf-8"))
        await self._process.stdin.drain()

        return await future
    
    def send_notification(self,method:str,params:dict|None=None)->None:

        if params is None:
            params={}
        
        if not self._process or self._process.stdin is None:
            return
        
        msg= json.dumps({"jsonrpc":"2.0","method":method,"params":params})+"\n"
        self._process.stdin.write(msg.encode("utf-8"))

    async def initialize(self)->None:
        """MCP 初始化握手"""
        await self.send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mini-claude", "version": "1.0.0"},
        })
        # 握手成功后发通知确认
        self.send_notification("notifications/initialized")

    async def list_tools(self)->list[dict]:
        """MCP 列出工具"""
        result = await self.send_request("tools/list")
        if not result or not isinstance(result.get("tools"), list):
            return []
        return [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "inputSchema": t["inputSchema"],
                "serverName": self.server_name,
            }
            for t in result["tools"]
        ]
    
    async def call_tool(self,name:str,args:dict)->str:
        """调用工具，返回文本结果"""
        result = await self.send_request("tools/call", {"name": name, "arguments": args})
        content = result.get("content")
        if content and isinstance(content, list):
            text_parts = [
                c["text"] for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            ]
            if text_parts:
                return "\n".join(text_parts)
        return json.dumps(result)

    async def close(self) -> None:
        """关闭连接，清理资源"""
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        if self._process:
            try:
                self._process.stdin.close()
            except Exception:
                pass
            try:
                self._process.terminate()
            except Exception:
                pass
            self._process = None

    
class McpManager:
    def __init__(self) -> None:
        self._connections: dict[str, McpConnection] = {}
        self._tools: list[McpToolInfo] = []
        self._connected: bool = False

    def _load_configs(self)->dict[str,McpServerConfig]:

        merged:dict[str,McpServerConfig]={}

        # 1. 用户级：~/.claude/settings.json
        global_path = Path.home() / ".claude" / "settings.json"
        self._merge_config_file(global_path, merged)

        # 2. 项目级：.claude/settings.json
        project_path = Path.cwd() / ".claude" / "settings.json"
        self._merge_config_file(project_path, merged)

        # 3. MCP 专用：.mcp.json
        mcp_json_path = Path.cwd() / ".mcp.json"
        self._merge_config_file(mcp_json_path, merged)

        return merged

    def _merge_config_file(
        self, file_path: Path, target: dict[str, McpServerConfig]
    ) -> None:
        """读取一个配置文件，合并到 target 字典"""
        if not file_path.exists():
            return
        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
            servers = raw.get("mcpServers") or raw  # .mcp.json 可能直接是服务器映射
            for name, config in servers.items():
                if self._is_valid_config(config):
                    target[name] = config
        except (json.JSONDecodeError, OSError, TypeError):
            # 静默跳过格式错误或无法读取的配置文件
            pass

    @staticmethod
    def _is_valid_config(config: Any) -> bool:
        """检查配置是否包含必要字段"""
        return isinstance(config, dict) and "command" in config
    
    async def load_and_connect(self) -> None:
        """连接所有 MCP 服务器（幂等：多次调用只连一次）"""
        if self._connected:
            return
        self._connected = True

        configs = self._load_configs()
        if not configs:
            return

        TIMEOUT_S = 15  # 15 秒超时

        for name, config in configs.items():
            conn = McpConnection(name, config)
            try:
                await conn.connect()
                # 握手和工具发现都有 15 秒超时
                await asyncio.wait_for(conn.initialize(), timeout=TIMEOUT_S)
                server_tools = await asyncio.wait_for(conn.list_tools(), timeout=TIMEOUT_S)
                self._connections[name] = conn
                self._tools.extend(server_tools)
                print(f"[mcp] Connected to '{name}' — {len(server_tools)} tools")
            except (asyncio.TimeoutError, Exception) as err:
                print(f"[mcp] Failed to connect to '{name}': {err}")
                await conn.close()  # 失败的连接立即清理，不影响其他服务器

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """生成 Agent 可用的工具定义列表"""
        return [
            {
                "name": f"mcp__{t['serverName']}__{t['name']}",
                "description": t.get("description") or f"MCP tool {t['name']} from {t['serverName']}",
                "input_schema": t.get("inputSchema") or {"type": "object", "properties": {}},
            }
            for t in self._tools
        ]

    def is_mcp_tool(self, name: str) -> bool:
        """判断是否是 MCP 工具（以 mcp__ 开头）"""
        return name.startswith("mcp__")

    async def call_tool(self, prefixed_name: str, args: dict) -> str:
        """调用 MCP 工具：mcp__serverName__toolName → 找到对应连接并调用"""
        parts = prefixed_name.split("__")
        if len(parts) < 3:
            raise ValueError(f"Invalid MCP tool name: {prefixed_name}")

        server_name = parts[1]
        tool_name = "__".join(parts[2:])  # 工具名可能包含 __

        conn = self._connections.get(server_name)
        if conn is None:
            raise ValueError(f"MCP server '{server_name}' not connected")

        return await conn.call_tool(tool_name, args)

                
                        

                    
