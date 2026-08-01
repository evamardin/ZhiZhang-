"""配置管理"""
import json
import os
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
AGENT_DIR = Path(__file__).parent

def load_config() -> dict:
    """加载配置文件"""
    config_path = AGENT_DIR / "config.json"
    if not config_path.exists():
        return _default_config()
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(config: dict):
    """保存配置文件"""
    config_path = AGENT_DIR / "config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)

def get_data_dir() -> Path:
    """获取数据目录的绝对路径"""
    config = load_config()
    data_dir = config.get("data", {}).get("dir", "../解析结果")
    p = Path(data_dir)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p

def _load_env():
    """从 .env 文件加载环境变量"""
    env_path = PROJECT_ROOT / "工具" / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

def get_llm_config() -> dict:
    """获取 LLM 配置，.env 中的 DEEPSEEK_API_KEY 优先级最高"""
    _load_env()
    config = load_config()
    cfg = config.get("llm", {})
    # 环境变量覆盖 config.json
    env_key = os.environ.get("DEEPSEEK_API_KEY") or ""
    if env_key:
        cfg["api_key"] = env_key
    return cfg

def _default_config() -> dict:
    return {
        "llm": {
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "",
            "model": "deepseek-chat"
        },
        "data": {
            "dir": "../解析结果"
        },
        "user": {
            "name": "王皓宇",
            "current_balance": 5000,
            "monthly_fixed_expense": 0
        }
    }
