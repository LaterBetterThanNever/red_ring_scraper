"""
环境变量加载器
从 .env 文件加载敏感凭证，config.json 只保留非敏感配置。
所有脚本都应 import env_loader 来获取凭证。
"""

import os
from pathlib import Path

# 项目根目录
_PROJECT_DIR = Path(__file__).parent


def _load_dotenv():
    """读取 .env 文件并设置环境变量（不覆盖已存在的）"""
    env_path = _PROJECT_DIR / ".env"
    if not env_path.exists():
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


# 首次 import 时自动加载
_load_dotenv()


def get(key: str, default: str = "") -> str:
    """获取环境变量"""
    return os.environ.get(key, default)


def require(key: str) -> str:
    """获取环境变量，不存在则抛出异常"""
    value = os.environ.get(key)
    if not value:
        raise ValueError(f"环境变量 {key} 未设置，请在 .env 文件中配置")
    return value
