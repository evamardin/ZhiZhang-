"""CLI 入口

交互式命令行对话，支持自然语言和斜杠命令。
"""
import sys
from pathlib import Path
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown

# 确保能导入同目录模块
sys.path.insert(0, str(Path(__file__).parent))

from config import load_config, get_data_dir, get_llm_config
from data_loader import load_all
from analyzer import (
    get_monthly_summary,
    get_category_breakdown,
    get_pending_categories,
    get_spending_trend,
)
from tools import execute_tool
import json

console = Console()


def print_welcome():
    """打印欢迎信息"""
    console.print(Panel.fit(
        "[bold cyan]财务助手 Agent[/bold cyan]\n"
        "自然语言对话 + 斜杠命令\n"
        "输入 [yellow]/help[/yellow] 查看命令 | 输入 [yellow]/exit[/yellow] 退出",
        border_style="cyan",
    ))


def print_help():
    """打印帮助"""
    help_table = Table(title="可用命令", show_header=True)
    help_table.add_column("命令", style="cyan")
    help_table.add_column("说明", style="white")
    help_table.add_column("示例", style="dim")
    help_table.add_row("/summary", "月度收支汇总", "/summary 2026-07")
    help_table.add_row("/category", "分类支出明细", "/category 2026-07")
    help_table.add_row("/trend", "消费趋势", "/trend 6")
    help_table.add_row("/pending", "待分类交易", "/pending")
    help_table.add_row("/exit", "退出", "")
    help_table.add_row("", "", "")
    help_table.add_row("自然语言", "直接对话", "这个月花了多少？")
    console.print(help_table)


def cmd_summary(transactions, args):
    """月度收支汇总"""
    ym = args.strip() or datetime.now().strftime("%Y-%m")
    result = json.loads(execute_tool("get_monthly_summary", {"year_month": ym}, transactions))
    if "error" in result:
        console.print(f"[red]错误: {result['error']}[/red]")
        return

    table = Table(title=f"{ym} 月度收支汇总", show_header=True, header_style="bold")
    table.add_column("项目", style="cyan")
    table.add_column("金额", justify="right")
    table.add_row("总收入", f"[green]{result['total_income']:,.2f}[/green]")
    table.add_row("总支出", f"[red]{result['total_expense']:,.2f}[/red]")
    table.add_row("净结余", f"[{'green' if result['net'] >= 0 else 'red'}]{result['net']:,.2f}[/{'green' if result['net'] >= 0 else 'red'}]")
    table.add_row("收入笔数", str(result["income_count"]))
    table.add_row("支出笔数", str(result["expense_count"]))
    table.add_row("日均支出", f"{result['avg_daily_expense']:,.2f}")
    if result.get("largest_expense"):
        le = result["largest_expense"]
        table.add_row("最大支出", f"{le['amount']:,.2f} ({le.get('merchant', '')} - {le.get('category', '')})")
    console.print(table)


def cmd_category(transactions, args):
    """分类支出明细"""
    ym = args.strip() or datetime.now().strftime("%Y-%m")
    result = json.loads(execute_tool("get_category_breakdown", {"year_month": ym}, transactions))
    if isinstance(result, dict) and "error" in result:
        console.print(f"[red]错误: {result['error']}[/red]")
        return

    table = Table(title=f"{ym} 分类支出明细", show_header=True, header_style="bold")
    table.add_column("分类", style="cyan")
    table.add_column("金额", justify="right", style="red")
    table.add_column("笔数", justify="right")
    table.add_column("占比", justify="right", style="yellow")
    for item in result:
        table.add_row(
            item["category"],
            f"{item['amount']:,.2f}",
            str(item["count"]),
            f"{item['percentage']:.1f}%",
        )
    console.print(table)


def cmd_trend(transactions, args):
    """消费趋势"""
    n = int(args.strip()) if args.strip() else 6
    result = json.loads(execute_tool("get_spending_trend", {"num_months": n}, transactions))
    if isinstance(result, dict) and "error" in result:
        console.print(f"[red]错误: {result['error']}[/red]")
        return

    table = Table(title=f"最近 {n} 个月消费趋势", show_header=True, header_style="bold")
    table.add_column("月份", style="cyan")
    table.add_column("支出", justify="right", style="red")
    table.add_column("收入", justify="right", style="green")
    table.add_column("结余", justify="right")
    for item in result:
        net = item["net"]
        net_str = f"[{'green' if net >= 0 else 'red'}]{net:,.2f}[/{'green' if net >= 0 else 'red'}]"
        table.add_row(
            item["year_month"],
            f"{item['total_expense']:,.2f}",
            f"{item['total_income']:,.2f}",
            net_str,
        )
    console.print(table)


def cmd_pending(transactions, args):
    """待分类交易"""
    result = json.loads(execute_tool("get_pending_categories", {}, transactions))
    if isinstance(result, dict) and "error" in result:
        console.print(f"[red]错误: {result['error']}[/red]")
        return

    if not result:
        console.print("[green]没有待分类的交易[/green]")
        return

    console.print(f"[yellow]共 {len(result)} 笔待分类交易（显示前 20 笔）[/yellow]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("时间", style="dim")
    table.add_column("商户", style="cyan")
    table.add_column("金额", justify="right", style="red")
    table.add_column("来源")
    table.add_column("描述", style="dim")
    for item in result[:20]:
        table.add_row(
            item["datetime"][:16],
            item["merchant"][:20],
            f"{item['amount']:,.2f}",
            item["source"],
            item.get("description", "")[:30],
        )
    console.print(table)


def main():
    """主入口"""
    print_welcome()

    # 加载数据
    console.print("[dim]正在加载交易数据...[/dim]")
    data_dir = get_data_dir()
    transactions = load_all(data_dir)
    expense_count = len([t for t in transactions if t.direction == "expense" and not t.is_duplicate])
    income_count = len([t for t in transactions if t.direction == "income" and not t.is_duplicate])
    pending_count = len([t for t in transactions if t.category == "待分类" and not t.is_duplicate])
    console.print(f"[green]已加载 {len(transactions)} 笔交易（支出 {expense_count} / 收入 {income_count} / 待分类 {pending_count}）[/green]\n")

    # 检查 API Key
    llm_config = get_llm_config()
    api_key = llm_config.get("api_key") or os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        console.print("[yellow]⚠ 未配置 DeepSeek API Key，仅支持斜杠命令模式[/yellow]")
        console.print("[dim]请设置环境变量 DEEPSEEK_API_KEY 后使用自然语言对话[/dim]\n")
        agent = None
    else:
        from agent import FinancialAgent
        agent = FinancialAgent(transactions, llm_config)
        console.print("[green]✓ Agent 已就绪，支持自然语言对话[/green]\n")

    # 命令处理
    commands = {
        "/summary": cmd_summary,
        "/category": cmd_category,
        "/trend": cmd_trend,
        "/pending": cmd_pending,
    }

    # 交互循环
    while True:
        try:
            user_input = console.input("[bold cyan]你>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]再见[/dim]")
            break

        if not user_input:
            continue

        if user_input in ("/exit", "/quit", "/q"):
            console.print("[dim]再见[/dim]")
            break

        if user_input == "/help":
            print_help()
            continue

        # 斜杠命令
        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            cmd = parts[0]
            args = parts[1] if len(parts) > 1 else ""
            handler = commands.get(cmd)
            if handler:
                handler(transactions, args)
            else:
                console.print(f"[red]未知命令: {cmd}，输入 /help 查看可用命令[/red]")
            continue

        # 自然语言对话
        if agent:
            try:
                response = agent.chat(user_input)
                console.print()
                console.print(Markdown(response))
                console.print()
            except Exception as e:
                console.print(f"[red]Agent 错误: {e}[/red]")
        else:
            console.print("[yellow]请先配置 API Key，或使用 /help 查看斜杠命令[/yellow]")


if __name__ == "__main__":
    main()
