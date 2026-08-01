"""Agent 核心层

LLM 交互 + 工具调用循环。
使用 OpenAI SDK（兼容 DeepSeek/Qwen/GLM 等国内 API）。
"""
import json
import os
from openai import OpenAI
from data_loader import Transaction
from tools import TOOL_SCHEMAS, execute_tool

SYSTEM_PROMPT = """你是「财务助手」，一个帮助用户管理日常收支的个人财务 Agent。

## 你的能力（查询工具）
你可以调用以下工具查询和分析财务数据：
- get_monthly_summary: 月度收支汇总
- get_category_breakdown: 分类支出占比
- query_transactions: 按条件查询交易记录
- get_spending_trend: 消费趋势
- get_top_merchants: 消费商户排行
- project_cashflow: 未来现金流预测
- get_pending_categories: 待分类交易列表

## 你的能力（执行工具）
你也可以执行以下操作来帮用户管理账目：
- batch_classify_merchant: 将某商户的所有交易设为指定分类，并创建分类规则
- batch_tag_merchant: 给某商户的所有交易添加指定标签

## 行为准则
1. 基于工具返回的真实数据回答，绝不编造数据
2. 如果用户问题不明确，先调用工具获取数据再回答
3. 数据不足时诚实说明，不猜测
4. 回答简洁实用，用中文，金额保留两位小数
5. 对于财务建议，给出具体数字和依据
6. 如果有未分类的交易，主动提醒用户
7. 当用户提出执行操作时（如"把所有X设为Y""给X加标签"），先通过 query_transactions 确认商户名和交易笔数，确认后再调用写工具执行

## 数据说明
- 交易数据来自微信、支付宝和兴业银行（2026年4-7月）
- 银行侧的重复记录已过滤，不会重复计算
- 部分交易可能未分类，需要用户确认"""


class FinancialAgent:
    """财务助手 Agent"""

    def __init__(self, transactions: list[Transaction], llm_config: dict, db=None):
        self.transactions = transactions
        self.llm_config = llm_config
        self.db = db
        self.client = OpenAI(
            base_url=llm_config.get("base_url", "https://api.deepseek.com/v1"),
            api_key=llm_config.get("api_key") or os.getenv("DEEPSEEK_API_KEY", ""),
        )
        self.model = llm_config.get("model", "deepseek-chat")
        self.messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

    def chat(self, user_input: str) -> str:
        """处理用户输入，返回回复文本"""
        self.messages.append({"role": "user", "content": user_input})

        # 最多 5 轮工具调用
        for _ in range(5):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
            )
            msg = response.choices[0].message

            # 如果没有工具调用，直接返回文本
            if not msg.tool_calls:
                self.messages.append({"role": "assistant", "content": msg.content})
                return msg.content or ""

            # 记录 assistant 的工具调用消息
            self.messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })

            # 执行每个工具调用
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}

                result = execute_tool(tool_name, tool_args, self.transactions, db=self.db)

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        # 超过 5 轮，让 LLM 基于已有数据总结
        response = self.client.chat.completions.create(
            model=self.model,
            messages=self.messages,
        )
        content = response.choices[0].message.content
        self.messages.append({"role": "assistant", "content": content})
        return content or ""

    def reset(self):
        """重置对话"""
        self.messages = [self.messages[0]]

    def get_history(self) -> list[dict]:
        """获取对话历史（只保留用户和助手的消息，过滤掉 tool_call 等中间消息）"""
        history = []
        for m in self.messages:
            role = m.get("role")
            if role in ("user", "assistant") and m.get("content"):
                history.append({"role": role, "content": m["content"]})
        return history[1:]  # 去掉第一条 system prompt
