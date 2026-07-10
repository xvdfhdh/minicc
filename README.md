# Mini Claude Code

> 轻量级 AI 编程助手 CLI，OpenAI 兼容 / Anthropic 双后端，无需 Node.js，pip 一键安装。

```
    /)  /)
   (˶• •˶)
   (  🥕 )
    "" ""
```

## 特性

- **双后端支持** — OpenAI / DeepSeek / 智谱 / 通义千问 / Ollama + Anthropic Claude
- **首次配置向导** — 交互式选择 API 类型，输入 Key/URL/Model，保存全局配置
- **Plan 模式** — 先规划后执行，规划阶段锁定写操作，用户审批后自动实施
- **记忆系统** — 文件持久化 + LLM 语义检索，跨会话保留上下文
- **子代理** — 搜索代理独立探索代码库，降低主对话上下文消耗
- **技能系统** — 用户自定义 `/skill-name` 快捷命令，YAML 配置零代码
- **命令行工具** — 完整的 CLI 参数，支持 `--yolo` 自动批准、`--thinking` 思考链等

## 安装

### 从 wheel 包安装

```bash
pip install zahxi-minicc
```

### 从源码安装（开发模式）

```bash
git clone https://github.com/xvdfhdh/minicc.git
cd minicc
pip install -e .
```

### 依赖

- Python >= 3.8
- anthropic >= 0.39
- openai >= 1.0
- rich >= 13.0
- requests >= 2.28

## 快速开始

```bash
mini-claude
```

首次启动无 API Key 时自动弹出配置向导：

```
Choose your API provider:
  1) OpenAI 兼容（DeepSeek / 智谱 / 通义千问 等）
  2) Anthropic（Claude 官方）
  3) Custom (manual input)
```

选择模板后输入 API Key / URL / Model，配置保存到 `~/.minicc/.env`，全局复用。

### 单次问答

```bash
mini-claude "解释这段代码的作用"
```

### 指定模型

```bash
mini-claude -m gpt-4o "用 Python 写一个快速排序"
```

## 权限模式

| 模式 | 参数 | 行为 |
|------|------|------|
| 默认 | (无) | 每次工具调用需确认 |
| YOLO | `-y` / `--yolo` | 自动批准所有操作 |
| 探索 | `--explore` | 只读模式，禁止写入 |
| 计划 | `--plan` | 先规划后执行 |
| 接受编辑 | `--accept-edits` | 自动接受文件修改 |

## 环境变量

| 变量 | 说明 |
|------|------|
| `OPENAI_API_KEY` | OpenAI 兼容 API Key |
| `OPENAI_BASE_URL` | OpenAI 兼容 API 地址 |
| `ANTHROPIC_API_KEY` | Anthropic API Key |
| `ANTHROPIC_BASE_URL` | Anthropic API 地址（可选） |
| `MINI_CLAUDE_MODEL` | 默认模型 ID（如 `deepseek-chat`） |

配置文件搜索路径（优先级从高到低）：

1. `./.env` — 当前工作目录
2. `~/.minicc/.env` — 用户全局配置（推荐）
3. 项目根目录 `.env`

## 内置命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示完整帮助 |
| `/clear` | 清空会话历史 |
| `/cost` | Token 用量 & 费用估算 |
| `/compact` | 压缩对话上下文（节省 Token） |
| `/plan` | 切换计划模式 |
| `/memory` | 查看已保存的记忆 |
| `exit` / `quit` | 退出 |
| `Ctrl+C` | 中断当前响应（连按两次强制退出） |

## 内置工具

| 工具 | 说明 |
|------|------|
| `read_file` | 读取文件内容 |
| `write_file` | 写入文件 |
| `edit_file` | 精准文本替换 |
| `list_files` | 列出目录结构 |
| `grep_search` | 正则搜索代码 |
| `run_shell` | 执行 Shell 命令 |
| `web_fetch` | 抓取网页内容 |
| `skill` | 调用自定义技能 |
| `enter_plan_mode` | 进入计划模式 |
| `exit_plan_mode` | 提交计划供审批 |
| `agent` | 启动子代理 |
| `tool_search` | 搜索可用工具 |

## 技能系统

在 `~/.minicc/skills/`（全局）或 `.minicc/skills/`（项目级）下创建 `.md` 文件：

```markdown
---
name: code-review
description: 审查代码质量和安全性
when_to_use: 用户提到"审查"或"review"时
user_invocable: true
allowed_tools: read_file, grep_search
---

请对以下代码进行全面审查，关注：
1. 潜在 Bug
2. 安全问题
3. 性能优化建议

$ARGUMENTS
```

用户输入 `/code-review src/main.py` 即可触发，`$ARGUMENTS` 会被替换为 `src/main.py`。

## 记忆系统

Agent 自动通过 `write_file` 工具保存记忆到 `~/.minicc/memory/`，格式为 YAML frontmatter + Markdown：

```markdown
---
name: 用户偏好
description: 用户偏好回复风格
type: user
---

用户希望回复简洁、直接，少用套话。
```

支持四种记忆类型：`user`、`feedback`、`project`、`reference`。写入后自动更新索引，下次对话自动语义检索相关记忆注入上下文。

## CLI 参数

```
mini-claude [prompt] [options]

Options:
  -y, --yolo           自动批准所有操作
  --explore            探索模式（只读）
  --plan               计划模式
  --accept-edits       自动接受编辑
  --dont-ask           跳过确认
  --thinking           展示思考过程
  -m, --model MODEL    指定模型 ID
  --api-base URL       OpenAI 兼容 API 地址
  --resume             恢复最近一次会话
  --max-cost COST      单次会话费用上限（USD）
  --max-turns N        最大对话轮数
  -h, --help           显示帮助
```

## 项目结构

```
minicc/
├── src/
│   ├── main/
│   │   ├── agent.py          # Agent 核心循环（对话、工具调用、压缩）
│   │   ├── _main_.py          # 入口：参数解析、REPL、配置加载
│   │   ├── ui.py              # 终端 UI（欢迎界面、帮助、工具输出）
│   │   ├── config_wizard.py   # 首次配置向导
│   │   ├── session.py         # 会话持久化
│   │   └── subagent.py        # 子代理实现
│   ├── memory/
│   │   ├── memory.py          # 记忆存取、语义检索、索引
│   │   └── frontmatter.py     # YAML frontmatter 解析
│   ├── prompt/
│   │   ├── prompt.py          # 系统提示词构建
│   │   └── skills/
│   │       └── skills.py      # 技能发现与调度
│   ├── tools/
│   │   └── tools.py           # 所有工具实现
│   └── mini_claude.py         # 包入口
├── pyproject.toml
├── dist/                      # 构建产物
└── README.md
```

## License

MIT
