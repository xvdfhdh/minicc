from __future__ import annotations
from anthropic import AsyncAnthropic
from openai import OpenAI
from src.tools.tools import *
from src.main.ui import *
from src.prompt.prompt import build_system_prompt
import asyncio
import time
import json
import uuid
import os
from datetime import datetime, timezone
from pathlib import Path
from src.memory.memory import *

# 将内部工具定义转换为 OpenAI function calling 格式
def _to_openai_tools(tools: list[ToolDef]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


# 带指数退避重试的异步执行器，处理 429/503/529 等可重试错误
async def _with_retry(fn, max_retries: int = 3):
    # 判断异常是否可重试（限流/过载/网络）
    def _is_retryable(error: Exception) -> bool:
        status = getattr(error, "status_code", None) or getattr(error, "status", None)
        if status in (429, 503, 529):
            return True
        msg = str(error)
        if "overloaded" in msg or "ECONNRESET" in msg or "ETIMEDOUT" in msg:
            return True
        return False

    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as error:
            if attempt >= max_retries or not _is_retryable(error):
                raise
            delay = (
                min(1000 * (2 ** attempt), 30000) / 1000
                + (hash(str(time.time())) % 1000) / 1000
            )
            reason = str(getattr(error, "status_code", "")) or str(error)[:60]
            print_retry(attempt + 1, max_retries, reason)
            await asyncio.sleep(delay)


# 超大工具结果落盘处理：>30KB 写入 ~/.minicc/tool-results/，返回预览摘要
def _persist_large_result(tool_name: str, result: str) -> str:
    THRESHOLD = 30 * 1024  # 30 KB
    if len(result.encode("utf-8")) <= THRESHOLD:
        return result

    dir_path = Path.home() / ".minicc" / "tool-results"
    dir_path.mkdir(parents=True, exist_ok=True)
    filename = f"{int(time.time() * 1000)}-{tool_name}.txt"
    filepath = dir_path / filename
    filepath.write_text(result, encoding="utf-8")

    lines = result.split("\n")
    preview = "\n".join(lines[:200])
    size_kb = len(result.encode("utf-8")) / 1024

    return (
        f"[Result too large ({size_kb:.1f} KB, {len(lines)} lines). "
        f"Full output saved to {filepath}. "
        f"You can use read_file to see the full result.]\n\n"
        f"Preview (first 200 lines):\n{preview}"
    )


SNIPPABLE_TOOLS = {"read_file", "grep_search", "list_files", "run_shell"}
SNIP_PLACEHOLDER = "[Content snipped - re-read if needed]"
KEEP_RECENT_RESULTS = 3
MICROCOMPACT_IDLE_S = 5 * 60

class Agent:

    def _emit_text(self, text: str) -> None:
        if self._output_buffer is not None:
            self._output_buffer.append(text)
        else:
            print_assistant_text(text)   

    @staticmethod
    def _block_to_dist(block) -> dict:
        """将 Anthropic SDK 内容块对象转为普通 dict"""
        if isinstance(block, dict):
            return block
        d = {"type": block.type}
        if hasattr(block, "text"):
            d["text"] = block.text
        if hasattr(block, "name"):
            d["name"] = block.name
        if hasattr(block, "input"):
            d["input"] = block.input
        if hasattr(block, "id"):
            d["id"] = block.id
        if hasattr(block, "tool_use_id"):
            d["tool_use_id"] = block.tool_use_id
        if hasattr(block, "content"):
            d["content"] = block.content
        if hasattr(block, "is_error"):
            d["is_error"] = block.is_error
        return d

    async def run_once(self, prompt: str) -> dict:
        self._output_buffer = []
        prev_in = self.total_input_tokens
        prev_out = self.total_output_tokens
        await self.chat(prompt)
        text = "".join(self._output_buffer)
        self._output_buffer = None
        return {
            "text": text,
            "tokens": {
                "input": self.total_input_tokens - prev_in,
                "output": self.total_output_tokens - prev_out,
            },
        }

    async def _execute_agent_tool(self, inp: dict) -> str:
        agent_type = inp.get("type", "general")
        description = inp.get("description", "sub-agent task")
        prompt = inp.get("prompt", "")

        print_sub_agent_start(agent_type, description)

        config = get_sub_agent_config(agent_type)
        sub_agent = Agent(
            model=self.model,
            api_key=self.api_key,
            custom_system_prompt=config["system_prompt"],
            custom_tools=config["tools"],
            is_sub_agent=True,
            permission_mode="plan" if self.permission_mode == "plan" else "bypassPermissions",
        )

        try:
            result = await sub_agent.run_once(prompt)
            self.total_input_tokens += result["tokens"]["input"]
            self.total_output_tokens += result["tokens"]["output"]
            print_sub_agent_end(agent_type, description)
            return result["text"] or "(Sub-agent produced no output)"
        except Exception as e:
            print_sub_agent_end(agent_type, description)
            return f"Sub-agent error: {e}"


    def __init__(
        self,
        *,
        model: str,
        permission_mode: str = "default",
        thinking: bool = False,
        max_cost_usd: float | None = None,
        max_turns: int | None = None,
        api_key: str,
        use_openai: bool = False,
        openai_base_url: str | None = None,
        custom_system_prompt: str | None = None,
        custom_tools: list[ToolDef] | None = None,
        is_sub_agent: bool = False,
    ):
        # --- 基本配置 ---
        self.model = model
        self.permission_mode = permission_mode
        self._permission_mode = permission_mode
        self.thinking = thinking
        self.max_cost_usd = max_cost_usd
        self.max_turns = max_turns
        self.api_key = api_key
        self.use_openai = use_openai
        self.is_sub_agent = is_sub_agent
        self.tools = custom_tools or tool_definitions

        # --- 提示词 ---
        self._base_system_prompt = custom_system_prompt or build_system_prompt()
        self._system_prompt = self._base_system_prompt

        # --- API 客户端 ---
        self._anthropic_client = AsyncAnthropic(
            api_key=api_key,
            base_url=os.environ.get("ANTHROPIC_BASE_URL") or None,
            max_retries=2,
            timeout=float(os.environ.get("ANTHROPIC_TIMEOUT", "180")),
        )
        self._openai_client = OpenAI(
            api_key=api_key,
            base_url=openai_base_url or os.environ.get("OPENAI_BASE_URL"),
        )

        # --- 消息历史 ---
        self._anthropic_messages: list[dict] = []
        self._openai_messages: list[dict] = [
            {"role": "system", "content": self._system_prompt}
        ]

        # --- Token 统计 ---
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.last_input_token_count: int = 0
        self.effective_window: int = 200000  # 默认上下文窗口大小

        # --- 权限与确认 ---
        self._confirmed_paths: set[str] = set()
        self.confirm_fn = None
        self._plan_approval_fn = None

        # --- Plan 模式 ---
        self._pre_plan_mode: str | None = None
        self._plan_file_path: str | None = None
        self._context_cleared: bool = False

        # --- 记忆 ---
        self._already_surfaced_memories: set[str] = set()
        self._session_memory_bytes: int = 0

        # --- 中断信号 ---
        self._aborted: bool = False
        self._abort_signal = None

        # --- 输出缓冲 ---
        self._output_buffer: list[str] | None = None

        # --- 会话 ---
        self.session_id = str(uuid.uuid4())
        self.session_start_time = datetime.now(timezone.utc).isoformat()

        # --- Thinking 模式 ---
        self._thinking_mode = self._resolve_thinking_mode()

        # --- 流控 ---
        self.last_api_call_time: float | None = None

    def _build_side_query(self):
        """构建记忆预取用的轻量级 side-query 函数。
        返回 async callable (system_prompt, user_query, signal) -> str，
        或 None（当记忆文件不存在时跳过预取）。
        """
        from src.memory.memory import get_memory_dir
        if not any(get_memory_dir().iterdir()):
            return None

        async def _side_query(system_prompt: str, user_query: str, signal) -> str:
            try:
                if self.use_openai:
                    resp = await asyncio.to_thread(
                        self._openai_client.chat.completions.create,
                        model=self.model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_query},
                        ],
                        max_tokens=512,
                        temperature=0,
                    )
                    return resp.choices[0].message.content or ""
                else:
                    resp = await self._anthropic_client.messages.create(
                        model=self.model,
                        max_tokens=512,
                        system=system_prompt,
                        messages=[{"role": "user", "content": user_query}],
                    )
                    block = next((b for b in resp.content if b.type == "text"), None)
                    return block.text if block else ""
            except Exception:
                return ""

        return _side_query

    def set_plan_approval_fn(self, fn) -> None:
        self._plan_approval_fn = fn

    def _generate_plan_file_path(self) -> str:
        d = Path.home() / ".minicc" / "plans"
        d.mkdir(parents=True, exist_ok=True)
        return str(d / f"plan-{self.session_id}.md")

    
    def _build_plan_mode_prompt(self) -> str:
        return f"""

# Plan Mode Active

Plan mode is active. You MUST NOT make any edits (except the plan file below),
run non-readonly tools, or make any changes to the system.

## Plan File: {self._plan_file_path}
Write your plan incrementally to this file using write_file or edit_file.
This is the ONLY file you are allowed to edit.

## Workflow
1. **Explore**: Read code to understand the task. Use read_file, list_files, grep_search.
2. **Design**: Design your implementation approach.
3. **Write Plan**: Write a structured plan to the plan file including:
   - **Context**: Why this change is needed
   - **Steps**: Implementation steps with critical file paths
   - **Verification**: How to test the changes
4. **Exit**: Call exit_plan_mode when your plan is ready for user review.

IMPORTANT: When your plan is complete, you MUST call exit_plan_mode.
Do NOT ask the user to approve — exit_plan_mode handles that."""



    def toggle_plan_mode(self) -> str:
        if self.permission_mode == "plan":
            self.permission_mode = self._pre_plan_mode or "default"
            self._pre_plan_mode = None
            self._plan_file_path = None
            self._system_prompt = self._base_system_prompt
            if self.use_openai and self._openai_messages:
                self._openai_messages[0]["content"] = self._system_prompt
            print_info(f"Exited plan mode → {self.permission_mode} mode")
            return self.permission_mode
        else:
            self._pre_plan_mode = self.permission_mode
            self.permission_mode = "plan"
            self._plan_file_path = self._generate_plan_file_path()
            self._system_prompt = self._base_system_prompt + self._build_plan_mode_prompt()
            if self.use_openai and self._openai_messages:
                self._openai_messages[0]["content"] = self._system_prompt
            print_info(f"Entered plan mode. Plan file: {self._plan_file_path}")
            return "plan"

    def _run_compression_pipeline(self) -> None:
        if self.use_openai:
            self._budget_tool_results_openai()
            self._snip_stale_results_openai()
            self._microcompact_openai()
        else:
            self._budget_tool_results_anthropic()
            self._snip_stale_results_anthropic()
            self._microcompact_anthropic()

    async def _check_and_compact(self) -> None:
        if self.last_input_token_count > self.effective_window * 0.85:
            print_info("Context window filling up, compacting conversation...")
            await self._compact_conversation()

    
    async def _compact_anthropic(self) -> None:
        if len(self._anthropic_messages) <4:
            return

        last_user_msg=self._anthropic_messages[-1]
        
        summary_resp = await self._anthropic_client.messages.create(
            model=self.model,
            max_tokens=2048,
            system="You are a conversation summarizer. Be concise but preserve important details.",
            messages=[
                *self._anthropic_messages[:-1],
                {"role": "user", "content": "Summarize the conversation so far in a concise paragraph, "
                "preserving key decisions, file paths, and context needed to continue the work."},
            ],
        )
        summary_text=(summary_resp.content[0].text if summary_resp.content and summary_resp.content[0].type=="text" else "No summary available") 

        self._anthropic_messages = [
            {"role": "user", "content": f"[Previous conversation summary]\n{summary_text}"},
            {"role": "assistant", "content": "Understood. I have the context from our previous conversation. How can I continue helping?"},
        ]

        if last_user_msg.get("role") == "user":
            self._anthropic_messages.append(last_user_msg)
        self.last_input_token_count = 0

    @staticmethod
    def _strip_orphaned_tool_messages(messages: list[dict]) -> list[dict]:
        """移除孤立的 tool 结果和 assistant tool_calls（缺少配对的消息）。"""
        # 收集 assistant 中引用的所有 tool_call_id
        tc_ids_from_assistant: set[str] = set()
        for m in messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if isinstance(tc, dict):
                        tc_ids_from_assistant.add(tc["id"])
        # 收集 tool 消息中的 tool_call_id
        tc_ids_from_tools: set[str] = set()
        for m in messages:
            if m.get("role") == "tool":
                tc_ids_from_tools.add(m.get("tool_call_id", ""))
        # 只有双方都存在的 id 才是配对的
        paired_ids = tc_ids_from_assistant & tc_ids_from_tools

        result: list[dict] = []
        for m in messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                tc_ids = {tc["id"] for tc in m["tool_calls"] if isinstance(tc, dict)}
                if tc_ids.issubset(paired_ids):
                    result.append(m)
                # 否则跳过：孤立的 assistant tool_calls
            elif m.get("role") == "tool":
                if m.get("tool_call_id", "") in paired_ids:
                    result.append(m)
                # 否则跳过：孤立的 tool 结果
            else:
                result.append(m)
        return result

    async def _compact_openai(self)->None:
        if len(self._openai_messages) <5:
            return
        
        system_msg=self._openai_messages[0]
        last_user_msg=self._openai_messages[-1]
        # 构建 compact 切片并清理孤立的 tool_calls/tool 消息对
        compact_msgs = self._strip_orphaned_tool_messages(list(self._openai_messages[1:-1]))
        summary_resp = await asyncio.to_thread(
            self._openai_client.chat.completions.create,
            model=self.model,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": "You are a conversation summarizer. Be concise but preserve important details."},
                *compact_msgs,
                {"role": "user", "content": "Summarize the conversation so far..."},
            ],
        )
        summary_text = summary_resp.choices[0].message.content or "No summary available."

        self._openai_messages = [
            system_msg,
            {"role": "user", "content": f"[Previous conversation summary]\n{summary_text}"},
            {"role": "assistant", "content": "Understood. I have the context..."},
        ]

        if last_user_msg.get("role") == "user":
            self._openai_messages.append(last_user_msg)
        self.last_input_token_count = 0

    def _microcompact_anthropic(self) -> None:
        if not self.last_api_call_time or (time.time() - self.last_api_call_time) < MICROCOMPACT_IDLE_S:
            return


    # 当上下文利用率较高时，对历史中的大工具结果进行截断以节省 token
    def _budget_tool_results_anthropic(self) -> None:
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
        if utilization < 0.5:
            return
        budget = 15000 if utilization > 0.7 else 30000
        for msg in self._anthropic_messages:
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for block in msg["content"]:
                if (isinstance(block, dict)
                        and block.get("type") == "tool_result"
                        and isinstance(block.get("content"), str)
                        and len(block["content"]) > budget):
                    keep = (budget - 80) // 2
                    block["content"] = (
                        block["content"][:keep]
                        + f"\n\n[... budgeted: {len(block['content']) - keep * 2} chars truncated ...]\n\n"
                        + block["content"][-keep:]
                    )

    def _snip_stale_results_anthropic(self) -> None:
        """移除已被后续消息覆盖的旧工具结果（减少冗余 token）"""
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
        if utilization < 0.7:
            return
        # 收集所有当前有效的 tool_use id
        alive_ids: set[str] = set()
        for msg in self._anthropic_messages:
            if msg.get("role") != "assistant" or not isinstance(msg.get("content"), list):
                continue
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    alive_ids.add(block.get("id", ""))
        # 移除不在 alive_ids 中的 tool_result
        for msg in self._anthropic_messages:
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            msg["content"] = [
                b for b in msg["content"]
                if not (isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id", "") not in alive_ids)
            ]

    def _budget_tool_results_openai(self) -> None:
        """OpenAI 版工具结果截断"""
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
        if utilization < 0.5:
            return
        budget = 15000 if utilization > 0.7 else 30000
        for msg in self._openai_messages:
            if msg.get("role") == "tool" and isinstance(msg.get("content"), str) and len(msg["content"]) > budget:
                keep = (budget - 80) // 2
                msg["content"] = (
                    msg["content"][:keep]
                    + f"\n\n[... budgeted: {len(msg['content']) - keep * 2} chars truncated ...]\n\n"
                    + msg["content"][-keep:]
                )

    def _snip_stale_results_openai(self) -> None:
        """移除 OpenAI 消息中已过时的 tool 结果"""
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
        if utilization < 0.7:
            return
        alive_ids = set()
        for msg in self._openai_messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                alive_ids.update(tc["id"] for tc in msg["tool_calls"] if isinstance(tc, dict))
        self._openai_messages = [
            m for m in self._openai_messages
            if not (m.get("role") == "tool" and m.get("tool_call_id", "") not in alive_ids)
        ]

    def _microcompact_openai(self) -> None:
        """OpenAI 微型压缩：空闲时移除多余的 assistant 消息"""
        if not self.last_api_call_time or (time.time() - self.last_api_call_time) < MICROCOMPACT_IDLE_S:
            return
        # 保留最后一条 assistant 消息（可能有 tool_calls），删除中间纯文本 assistant 消息
        kept: list[dict] = []
        last_assistant_idx = -1
        for i, m in enumerate(self._openai_messages):
            if m.get("role") == "assistant":
                last_assistant_idx = i
        for i, m in enumerate(self._openai_messages):
            if m.get("role") == "assistant" and i != last_assistant_idx and not m.get("tool_calls"):
                continue  # 删除过期的纯文本 assistant 消息
            kept.append(m)
        self._openai_messages = kept

    async def _compact_conversation(self) -> None:
        """根据后端路由到对应的压缩方法"""
        if self.use_openai:
            await self._compact_openai()
        else:
            await self._compact_anthropic()

    # 向用户确认危险操作（调用注入的 confirm_fn 或终端交互询问）
    async def _confirm_dangerous(self, command: str) -> bool:
        print_confirmation(command)
        if self.confirm_fn:
            return await self.confirm_fn(command)
        try:
            answer = input("  Allow? (y/n): ")
            return answer.lower().startswith("y")
        except EOFError:
            return False

    # Anthropic 主对话循环：流式调用 → 权限检查 → 工具执行 → 结果回传
    async def _chat_anthropic(self, user_message: str) -> None:
        self._anthropic_messages.append({"role": "user", "content": user_message})
        memory_prefetch:MemoryPrefetch|None=None
        if not self.is_sub_agent:
            sq=self._build_side_query()
            if sq:
                memory_prefetch=start_memory_prefetch(
                    user_message,sq,
                    self._already_surfaced_memories,self._session_memory_bytes,
                    self._abort_signal if hasattr(self, '_abort_signal') else None,
                )
        


        await self._check_and_compact()

        while True:
            if self._aborted:
                break
            self._run_compression_pipeline()
            early_executions: dict[str, asyncio.Task] = {}

            # 流式过程中提前启动并发安全工具的执行
            async def on_tool_block_complete(block):
                if block["name"] in CONCURRENCY_SAFE_TOOLS:
                    perm = check_permission(block["name"], block["input"], self._permission_mode)
                    if perm["action"] == "allow":
                        task = asyncio.create_task(self._execute_tool_call(block["name"], block["input"]))
                        early_executions[block["id"]] = task
            
            if memory_prefetch and memory_prefetch.settled and not memory_prefetch.consumed:
                memory_prefetch.consumed = True
                memories = await memory_prefetch.promise
                if len(memories) > 0:
                    injection_text = format_memories_for_injection(memories)
                    self._anthropic_messages.append({"role": "user", "content": injection_text})
                    # 跟踪已展示的记忆和会话预算
                    for m in memories:
                        self._already_surfaced_memories.add(m["path"])
                        self._session_memory_bytes += len(m["content"].encode("utf-8"))


            
            response = await self._call_anthropic_stream(on_tool_block_complete=on_tool_block_complete)

            self.total_input_tokens += response.usage.input_tokens
            self.total_output_tokens += response.usage.output_tokens
            self.last_input_token_count = response.usage.input_tokens

            tool_uses = [b for b in response.content if b["type"] == "tool_use"]

            self._anthropic_messages.append({
                "role": "assistant",
                "content": [self._block_to_dist(b) for b in response.content],
            })

            if not tool_uses:
                if not self.is_sub_agent:
                    print_cost(self.total_input_tokens, self.total_output_tokens)
                break

            tool_results = []
            for tu in tool_uses:
                # 如果流式阶段已提前启动执行，直接取结果
                early_task = early_executions.get(tu.id)
                if early_task:
                    raw = await early_task  # 已完成或即将完成
                    result = _persist_large_result(tu.name, raw)
                    print_tool_result(tu.name, result)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": result,
                    })
                    continue
                if self._aborted:
                    break
                inp = dict(tu.input) if hasattr(tu.input, 'items') else tu.input
                print_tool_call(tu.name, inp)

                perm = check_permission(tu.name, inp, self.permission_mode, self._plan_file_path)
                if perm["action"] == "deny":
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": f"Action denied: {perm.get('message', '')}",
                    })
                    continue
                if (perm["action"] == "confirm"
                        and perm.get("message")
                        and perm["message"] not in self._confirmed_paths):
                    confirmed = await self._confirm_dangerous(perm["message"])
                    if not confirmed:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": "User denied this action.",
                        })
                        continue
                    self._confirmed_paths.add(perm["message"])

                result = await self._execute_tool_call(tu.name, inp)
                result = _persist_large_result(tu.name, result)
                print_tool_result(tu.name, result)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result,
                })

            self._anthropic_messages.append({"role": "user", "content": tool_results})

    # OpenAI 主对话循环：流式调用 → 权限预检 → 分批并发执行 → 结果回传
    async def _chat_openai(self, user_message: str) -> None:
        self._openai_messages.append({"role": "user", "content": user_message})
        # 启动记忆预取（异步筛选相关记忆，非阻塞）
        memory_prefetch: MemoryPrefetch | None = None
        if not self.is_sub_agent:
            sq = self._build_side_query()
            if sq:
                memory_prefetch = start_memory_prefetch(
                    user_message, sq,
                    self._already_surfaced_memories, self._session_memory_bytes,
                    self._abort_signal if hasattr(self, '_abort_signal') else None,
                )

        await self._check_and_compact()

        while True:
            if self._aborted:
                break
            self._run_compression_pipeline()

            # 非阻塞轮询：如果预取已完成，注入相关记忆到消息列表
            if memory_prefetch and memory_prefetch.settled and not memory_prefetch.consumed:
                memory_prefetch.consumed = True
                memories = await memory_prefetch.promise
                if len(memories) > 0:
                    injection_text = format_memories_for_injection(memories)
                    self._openai_messages.append({"role": "user", "content": injection_text})
                    for m in memories:
                        self._already_surfaced_memories.add(m["path"])
                        self._session_memory_bytes += len(m["content"].encode("utf-8"))

            response = await self._call_openai_stream()

            usage = response["usage"]
            self.total_input_tokens += usage.get("prompt_tokens", 0)
            self.total_output_tokens += usage.get("completion_tokens", 0)
            self.last_input_token_count = usage.get("prompt_tokens", 0)

            message = response["choices"][0]["message"]
            content = message.get("content")
            raw_tool_calls = message.get("tool_calls")

            assistant_msg: dict = {"role": "assistant"}
            if content:
                assistant_msg["content"] = content
            if raw_tool_calls:
                assistant_msg["tool_calls"] = raw_tool_calls
            self._openai_messages.append(assistant_msg)

            if not raw_tool_calls:
                if not self.is_sub_agent:
                    print_cost(self.total_input_tokens, self.total_output_tokens)
                break

            # Phase 1: 所有工具调用的权限预检
            oai_checked = []
            for tc in raw_tool_calls:
                fn_name = tc["function"]["name"]
                inp = json.loads(tc["function"]["arguments"])
                print_tool_call(fn_name, inp)

                perm = check_permission(fn_name, inp, self.permission_mode, self._plan_file_path)
                if perm["action"] == "deny":
                    oai_checked.append({
                        "allowed": False,
                        "fn_name": fn_name,
                        "input": inp,
                        "id": tc["id"],
                        "deny_reason": perm.get("message", ""),
                    })
                    continue
                if (perm["action"] == "confirm"
                        and perm.get("message")
                        and perm["message"] not in self._confirmed_paths):
                    confirmed = await self._confirm_dangerous(perm["message"])
                    if not confirmed:
                        oai_checked.append({
                            "allowed": False,
                            "fn_name": fn_name,
                            "input": inp,
                            "id": tc["id"],
                            "deny_reason": "User denied this action.",
                        })
                        continue
                    self._confirmed_paths.add(perm["message"])
                oai_checked.append({
                    "allowed": True,
                    "fn_name": fn_name,
                    "input": inp,
                    "id": tc["id"],
                })

            # Phase 2: 连续的并发安全工具合并为一个批次
            oai_batches: list[dict] = []
            for ct in oai_checked:
                safe = ct["allowed"] and ct["fn_name"] in CONCURRENCY_SAFE_TOOLS
                if safe and oai_batches and oai_batches[-1]["concurrent"]:
                    oai_batches[-1]["items"].append(ct)
                else:
                    oai_batches.append({"concurrent": safe, "items": [ct]})

            # Phase 3: 执行批次（并发用 asyncio.gather，其他顺序执行）
            tool_results = []
            for batch in oai_batches:
                if self._aborted:
                    break
                if batch["concurrent"]:
                    async def _exec(ct):
                        raw = await self._execute_tool_call(ct["fn_name"], ct["input"])
                        return {"ct": ct, "res": _persist_large_result(ct["fn_name"], raw)}

                    results = await asyncio.gather(*[_exec(ct) for ct in batch["items"]])
                    for r in results:
                        ct = r["ct"]
                        print_tool_result(ct["fn_name"], r["res"])
                        tool_results.append({
                            "role": "tool",
                            "tool_call_id": ct["id"],
                            "content": r["res"],
                        })
                else:
                    for ct in batch["items"]:
                        if self._aborted:
                            break
                        if not ct["allowed"]:
                            tool_results.append({
                                "role": "tool",
                                "tool_call_id": ct["id"],
                                "content": f"Action denied: {ct.get('deny_reason', '')}",
                            })
                        else:
                            result = await self._execute_tool_call(ct["fn_name"], ct["input"])
                            result = _persist_large_result(ct["fn_name"], result)
                            print_tool_result(ct["fn_name"], result)
                            tool_results.append({
                                "role": "tool",
                                "tool_call_id": ct["id"],
                                "content": result,
                            })

            self._openai_messages.extend(tool_results)

    # 公开入口：根据 use_openai 标志分发到对应后端
    async def chat(self, user_message: str) -> None:

        if not hasattr(self, "_confirmed_paths"):
            self._confirmed_paths: set[str] = set()

        self._aborted = False
        try:
            if self.use_openai:
                await self._chat_openai(user_message)
            else:
                await self._chat_anthropic(user_message)
        finally:
            pass
        if not self.is_sub_agent:
            print_divider()
            self.auto_save()

    # 中断当前对话循环
    def abort(self) -> None:
        self._aborted = True

    # 自动保存当前会话到磁盘
    def auto_save(self) -> None:
        try:
            save_session(self.session_id, {
                "metadata": {
                    "id": self.session_id,
                    "model": self.model,
                    "cwd": str(Path.cwd()),
                    "startTime": self.session_start_time,
                    "messageCount": self._get_message_count(),
                },
                "anthropicMessages": self._anthropic_messages if not self.use_openai else None,
                "openaiMessages": self._openai_messages if self.use_openai else None,
            })
        except Exception:
            pass

    def _get_message_count(self) -> int:
        """返回当前会话消息数量（排除 system 消息）"""
        msgs = self._openai_messages if self.use_openai else self._anthropic_messages
        return sum(1 for m in msgs if m.get("role") != "system")

    def _clear_history_keep_system(self) -> None:
        """清空消息历史，保留 system prompt"""
        if self.use_openai:
            self._openai_messages = [m for m in self._openai_messages if m.get("role") == "system"]
        else:
            self._anthropic_messages = []

    def clear_history(self) -> None:
        """公开接口：清空会话历史"""
        self._clear_history_keep_system()
        print_info("Conversation cleared.")

    def show_cost(self) -> None:
        """公开接口：显示 token 用量和费用估算"""
        print_cost(self.total_input_tokens, self.total_output_tokens)
        # 粗略费用估算（按通用定价：输入 $0.15/M, 输出 $0.60/M）
        in_cost = self.total_input_tokens / 1_000_000 * 0.15
        out_cost = self.total_output_tokens / 1_000_000 * 0.60
        total = in_cost + out_cost
        print_info(f"Estimated cost: ${total:.4f} USD")

    async def compact(self) -> None:
        """公开接口：压缩会话上下文"""
        print_info("Compacting conversation...")
        await self._compact_conversation()
        print_info("Compaction complete.")

    # 从保存的会话数据恢复消息历史
    def restore_session(self, data: dict) -> None:
        if data.get("anthropicMessages"):
            self._anthropic_messages = data["anthropicMessages"]
        if data.get("openaiMessages"):
            self._openai_messages = data["openaiMessages"]
        print_info(f"Session restored ({self._get_message_count()} messages).")

    # 流式调用 Anthropic API，支持 on_tool_block_complete 回调实现提前执行
    async def _call_anthropic_stream(self, on_tool_block_complete=None):
        async def _do():
            create_params: dict[str, Any] = {
                "model": self.model,
                "max_tokens": (
                    _get_max_output_tokens(self.model)
                    if self._thinking_mode != "disabled"
                    else 16384
                ),
                "system": self._system_prompt,
                "tools": self.tools,
                "messages": self._anthropic_messages,
            }

            if self._thinking_mode in ("adaptive", "enabled"):
                create_params["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": _get_max_output_tokens(self.model) - 1,
                }

            first_text = True

            # 跟踪流式过程中的 tool_use 块（用于拼接 input_json）
            tool_blocks_by_index: dict[int, dict] = {}

            async with self._anthropic_client.messages.stream(**create_params) as stream:
                async for event in stream:
                    if hasattr(event, 'type'):
                        if event.type == "content_block_start" and getattr(event, 'content_block', None):
                            cb = event.content_block
                            if cb.type == "tool_use":
                                tool_blocks_by_index[event.index] = {
                                    "id": cb.id, "name": cb.name, "input_json": ""
                                }
                        elif event.type == "content_block_delta" and hasattr(event.delta, 'partial_json'):
                            tracked = tool_blocks_by_index.get(event.index)
                            if tracked:
                                tracked["input_json"] += event.delta.partial_json
                        elif event.type == "content_block_stop" and on_tool_block_complete:
                            tracked = tool_blocks_by_index.get(event.index)
                            if tracked:
                                try:
                                    inp = json.loads(tracked["input_json"])
                                    await on_tool_block_complete({
                                        "type": "tool_use", "id": tracked["id"],
                                        "name": tracked["name"], "input": inp
                                    })
                                except json.JSONDecodeError:
                                    pass

                final_message = await stream.get_final_message()

            final_message.content = [b for b in final_message.content if b.type != "thinking"]
            return final_message

        return await _with_retry(_do)

    # 流式调用 OpenAI API，拼装分片传输的 tool_calls 为统一响应格式
    async def _call_openai_stream(self) -> dict:
        async def _do():
            stream = self._openai_client.chat.completions.create(
                model=self.model,
                max_tokens=16384,
                tools=_to_openai_tools(self.tools),
                messages=self._openai_messages,
                stream=True,
                stream_options={"include_usage": True},
            )

            content = ""
            first_text = True
            tool_calls: dict[int, dict] = {}
            finish_reason = ""
            usage = None

            for chunk in stream:
                if chunk.usage:
                    usage = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": chunk.usage.completion_tokens,
                    }

                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta and delta.content:
                    if first_text:
                        stop_spinner()
                        self._emit_text("\n")
                        first_text = False
                    self._emit_text(delta.content)
                    content += delta.content

                if delta and delta.tool_calls:
                    for tc in delta.tool_calls:
                        existing = tool_calls.get(tc.index)
                        if existing:
                            if tc.function and tc.function.arguments:
                                existing["arguments"] += tc.function.arguments
                        else:
                            tool_calls[tc.index] = {
                                "id": tc.id or "",
                                "name": (tc.function.name if tc.function else "") or "",
                                "arguments": (tc.function.arguments if tc.function else "") or "",
                            }

                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason

            assembled = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                }
                for _, tc in sorted(tool_calls.items())
            ] if tool_calls else None

            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": content or None,
                            "tool_calls": assembled,
                        },
                        "finish_reason": finish_reason or "stop",
                    }
                ],
                "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0},
            }

        return await _with_retry(_do)

    # 解析当前模型是否支持 thinking 模式（adaptive/enabled/disabled）
    def _resolve_thinking_mode(self) -> str:
        if not self.thinking or not _model_supports_thinking(self.model):
            return "disabled"
        if _model_supports_adaptive_thinking(self.model):
            return "adaptive"
        return "disabled"
    
    async def _execute_skill_tool(self, inp: dict) -> str:
        result = execute_skill(inp.get("skill_name", ""), inp.get("args", ""))
        if not result:
            return f"Unknown skill: {inp.get('skill_name', '')}"

        if result["context"] == "fork":
            tools = (
                [t for t in self.tools if t["name"] in result["allowed_tools"]]
                if result.get("allowed_tools")
                else [t for t in self.tools if t["name"] != "agent"]
            )
            sub_agent = Agent(
                model=self.model,
                api_key=self.api_key,
                custom_system_prompt=result["prompt"],
                custom_tools=tools,
                is_sub_agent=True,
                permission_mode="bypassPermissions",
            )
            sub_result = await sub_agent.run_once(inp.get("args") or "Execute this skill task.")
            return sub_result["text"] or "(Skill produced no output)"

        return f'[Skill "{inp.get("skill_name", "")}" activated]\n\n{result["prompt"]}'

    # 工具调用路由：plan 模式工具走自处理，普通工具委托给 tools.execute_tool
    async def _execute_tool_call(self, name: str, inp: dict) -> str:
        if name in ("enter_plan_mode", "exit_plan_mode"):
            return await self._execute_plan_mode_tool(name)
        if name == "agent":
            return await self._execute_agent_tool(inp)
        if name == "skill":
            return await self._execute_skill_tool(inp)
        return await execute_tool(name, inp)

    async def _execute_plan_mode_tool(self,name:str)->str:
        if name=="enter_plan_mode":
            if self.permission_mode=="plan":
                return "Already in plan mode."
            self.permission_mode="plan"
            self._plan_file_path=self._generate_plan_file_path()
            self._system_prompt=self._base_system_prompt + self._build_plan_mode_prompt()
            if self.use_openai and self._openai_messages:
                self._openai_messages[0]["content"]=self._system_prompt
            print_info("Entered plan mode (read-only). Plan file: " + self._plan_file_path)
            return (
                f"Entered plan mode. You are now in read-only mode.\n\n"
                f"Your plan file: {self._plan_file_path}\n"
                f"Write your plan to this file. This is the only file you can edit.\n\n"
                f"When your plan is complete, call exit_plan_mode."
            )

        if name=="exit_plan_mode":
            if self.permission_mode != "plan":
                return "Not in plan mode."
            plan_content = "(No plan file found)"
            if self._plan_file_path and Path(self._plan_file_path).exists():
                plan_content = Path(self._plan_file_path).read_text(encoding="utf-8")

            if self._plan_approval_fn:
                result = await self._plan_approval_fn(plan_content)
                choice = result.get("choice", "manual-execute")

                if choice == "keep-planning":
                    feedback = result.get("feedback") or "Please revise the plan."
                    return (
                        f"User rejected the plan and wants to keep planning.\n\n"
                        f"User feedback: {feedback}\n\n"
                        f"Please revise your plan based on this feedback. "
                        f"When done, call exit_plan_mode again."
                    )

                if choice in ("clear-and-execute", "execute"):
                    target_mode = "acceptEdits"
                else:
                    target_mode = self._pre_plan_mode or "default"

                self.permission_mode = target_mode
                self._pre_plan_mode = None
                saved_plan_path = self._plan_file_path
                self._plan_file_path = None
                self._system_prompt = self._base_system_prompt

                if choice == "clear-and-execute":
                    self._clear_history_keep_system()
                    self._context_cleared = True
                    print_info(f"Plan approved. Context cleared, executing in {target_mode} mode.")
                    return (
                        f"User approved the plan. Context was cleared. "
                        f"Permission mode: {target_mode}\n\n"
                        f"Plan file: {saved_plan_path}\n\n"
                        f"## Approved Plan:\n{plan_content}\n\n"
                        f"Proceed with implementation."
                    )

                print_info(f"Plan approved. Executing in {target_mode} mode.")
                return (
                    f"User approved the plan. Permission mode: {target_mode}\n\n"
                    f"## Approved Plan:\n{plan_content}\n\n"
                    f"Proceed with implementation."
                )

            # Fallback: no approval function
            self.permission_mode = self._pre_plan_mode or "default"
            self._pre_plan_mode = None
            self._plan_file_path = None
            self._system_prompt = self._base_system_prompt
            print_info("Exited plan mode. Restored to " + self.permission_mode + " mode.")
            return (
                f"Exited plan mode. Permission mode restored to: {self.permission_mode}\n\n"
                f"## Your Plan:\n{plan_content}"
            )

        return f"Unknown plan mode tool: {name}"

            