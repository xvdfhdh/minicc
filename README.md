# Mini Claude Code

轻量级 AI 编程助手 CLI 工具，支持 OpenAI 兼容 / Anthropic 双后端，无需 Node.js。

## 安装

```bash
pip install minicc-0.1.0-py3-none-any.whl
```

## 快速开始

```bash
mini-claude
```

首次启动无 API Key 时会自动弹出配置向导，支持预设模板或自定义输入，保存后永久生效。

## 命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/clear` | 清空会话历史 |
| `/cost` | 查看 Token 用量和费用估算 |
| `/compact` | 压缩会话上下文 |
| `/plan` | 切换计划模式（只读规划） |
| `/memory` | 查看已保存的记忆 |
| `exit` / `quit` | 退出 |
| `Ctrl+C` | 中断当前响应（连按两次退出） |

## CLI 参数

```
mini-claude [prompt] [options]

Options:
  -y, --yolo           自动批准所有操作
  --explore             探索模式（只读）
  --plan                计划模式
  --accept-edits        自动接受编辑
  --thinking            启用思考过程展示
  -m, --model MODEL     指定模型
  --api-base URL        OpenAI 兼容 API 地址
  --resume              恢复上次会话
  --max-cost COST       费用上限（USD）
  --max-turns N         最大对话轮数
```

## 支持的后端

- **OpenAI 兼容** — OpenAI / DeepSeek / 智谱 / 通义千问 / Ollama 等
- **Anthropic** — Claude 官方 API

## 内置工具

| 工具 | 说明 |
|------|------|
| `read_file` | 读取文件 |
| `write_file` | 写入文件 |
| `edit_file` | 精准编辑文件 |
| `list_files` | 列出目录 |
| `grep_search` | 代码搜索 |
| `run_shell` | 执行命令 |
| `web_fetch` | 获取网页内容 |
| `skill` | 调用自定义技能 |

## 技能系统

在 `~/.minicc/skills/`（全局）或 `.minicc/skills/`（项目级）下创建 `.md` 文件：

```markdown
---
name: code-review
description: 执行代码审查
type: user
---

请审查以下代码：$ARGUMENTS
```

用户输入 `/code-review src/main.py` 即可触发。

## 项目结构

```
minicc/
├── src/
│   ├── main/           # Agent 核心、UI、REPL、配置向导
│   ├── memory/         # 记忆系统（文件持久化 + 语义检索）
│   ├── prompt/         # 系统提示词 & 技能系统
│   └── tools/          # 工具实现
├── pyproject.toml
└── dist/
```

## License

MIT
