"""
交互式配置向导：首次启动时引导用户配置 API Key / URL / Model，
写入项目根目录的 .env 文件永久保存。
"""
from __future__ import annotations
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.text import Text

console = Console()

# ── 预设的 API 提供商模板 ──
PROVIDERS = [
    {
        "name": "OpenAI 兼容（DeepSeek / 智谱 / 通义千问 等）",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "env_key": "OPENAI_API_KEY",
        "env_url": "OPENAI_BASE_URL",
    },
    {
        "name": "Anthropic（Claude 官方）",
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-20250514",
        "env_key": "ANTHROPIC_API_KEY",
        "env_url": "ANTHROPIC_BASE_URL",
    },
]

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def _get_dotenv_path() -> Path:
    return ENV_PATH


def run_config_wizard() -> bool:
    """运行配置向导，返回 True 表示配置成功"""
    console.print()
    console.print(Panel.fit(
        "[bold cyan]Welcome to Mini Claude Code — First Time Setup[/bold cyan]\n\n"
        "[dim]No API key found. Let's configure your LLM provider.[/dim]",
        border_style="cyan",
    ))

    # ── Step 1: 选择 API 类型 ──
    console.print("\n[bold]Choose your API provider:[/bold]")
    for i, p in enumerate(PROVIDERS, 1):
        console.print(f"  [yellow]{i})[/yellow] {p['name']}")
    console.print(f"  [yellow]3)[/yellow] Custom (manual input)")

    while True:
        choice = Prompt.ask("  Enter choice", default="1").strip()
        if choice in ("1", "2"):
            provider = PROVIDERS[int(choice) - 1]
            break
        elif choice == "3":
            provider = None
            break
        console.print("  [red]Invalid choice, enter 1, 2, or 3[/red]")

    # ── Step 2: API Key ──
    console.print()
    if provider:
        console.print(f"[bold]API Key[/bold] [dim](for {provider['name']})[/dim]")
    else:
        console.print("[bold]API Key[/bold]")
    api_key = Prompt.ask("  API Key", password=True).strip()
    if not api_key:
        console.print("  [red]API Key cannot be empty. Setup cancelled.[/red]")
        return False

    # ── Step 3: Base URL ──
    console.print()
    if provider:
        default_url = provider["base_url"]
        console.print(f"[bold]Base URL[/bold] [dim](default: {default_url})[/dim]")
        base_url = Prompt.ask("  Base URL", default=default_url).strip()
    else:
        console.print("[bold]Base URL[/bold] [dim](e.g. https://api.openai.com/v1)[/dim]")
        base_url = Prompt.ask("  Base URL").strip()

    # ── Step 4: Model ──
    console.print()
    if provider:
        default_model = provider["default_model"]
        model = Prompt.ask("  Model ID", default=default_model).strip()
    else:
        model = Prompt.ask("  Model ID", default="gpt-4o").strip()

    # ── Step 5: 确认 ──
    console.print()
    summary = Text()
    summary.append("\n  API Key:    ", style="dim")
    summary.append(f"{api_key[:8]}{'*' * (len(api_key) - 8)}" if len(api_key) > 8 else "***")
    summary.append(f"\n  Base URL:   {base_url}", style="dim")
    summary.append(f"\n  Model:      {model}", style="dim")
    if provider:
        summary.append(f"\n  Provider:   {provider['name']}", style="dim")
    summary.append("\n  Save to:    ", style="dim")
    summary.append(str(ENV_PATH))

    console.print(Panel(summary, title="Configuration Summary", border_style="yellow"))

    confirmed = Confirm.ask("  [bold]Save and continue?[/bold]", default=True)
    if not confirmed:
        console.print("  [yellow]Setup cancelled.[/yellow]")
        return False

    # ── Step 6: 写入 .env ──
    _write_env(api_key, base_url, model, provider)
    console.print("  [green]Configuration saved successfully![/green]")
    console.print()
    return True


def _write_env(api_key: str, base_url: str, model: str, provider: dict | None) -> None:
    """将配置写入 .env 文件"""
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 读取现有 .env（如果存在），保留不相关的行
    existing_lines: list[str] = []
    existing_keys: set[str] = set()
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                existing_lines.append(line)
                continue
            if "=" in stripped:
                key = stripped.split("=")[0].strip()
                if key not in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
                               "OPENAI_API_KEY", "OPENAI_BASE_URL",
                               "MINI_CLAUDE_MODEL"):
                    existing_lines.append(line)
                    existing_keys.add(key)

    # 构建新的配置行
    new_lines: list[str] = []
    new_lines.append("# Mini Claude Code 环境配置")
    new_lines.append("")

    if provider and provider["env_key"] == "ANTHROPIC_API_KEY":
        # Anthropic 模式
        new_lines.append(f"ANTHROPIC_API_KEY={api_key}")
        if base_url and base_url != "https://api.anthropic.com":
            new_lines.append(f"ANTHROPIC_BASE_URL={base_url}")
        new_lines.append(f"MINI_CLAUDE_MODEL={model}")
    elif provider and provider["env_key"] == "OPENAI_API_KEY":
        # OpenAI 兼容模式
        new_lines.append(f"ANTHROPIC_API_KEY={api_key}")
        new_lines.append(f"ANTHROPIC_BASE_URL={base_url}")
        new_lines.append(f"OPENAI_API_KEY={api_key}")
        new_lines.append(f"OPENAI_BASE_URL={base_url}")
        new_lines.append(f"MINI_CLAUDE_MODEL={model}")
    else:
        # 自定义模式，尝试判断
        new_lines.append(f"OPENAI_API_KEY={api_key}")
        new_lines.append(f"OPENAI_BASE_URL={base_url}")
        new_lines.append(f"MINI_CLAUDE_MODEL={model}")

    # 如果有旧的非配置行，保留它们
    if existing_lines:
        new_lines.insert(0, "")
        for line in reversed(existing_lines):
            new_lines.insert(0, line)

    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
