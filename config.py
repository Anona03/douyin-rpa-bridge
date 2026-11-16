import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Target Douyin Group & Mention Settings
DOUYIN_URL = os.getenv("DOUYIN_URL", "https://www.douyin.com/jingxuan")
TARGET_GROUP = os.getenv("TARGET_GROUP", "你的抖音群聊名称").strip()
_raw_aliases = os.getenv("BOT_MENTION_ALIASES", "吉米妮,gemini")
BOT_MENTION_ALIASES = [a.strip() for a in _raw_aliases.split(",") if a.strip()]

# Browser Settings
_raw_profile_dir = os.getenv("USER_DATA_DIR", "browser_profile")
USER_DATA_DIR = str(
    Path(_raw_profile_dir) if Path(_raw_profile_dir).is_absolute() else (BASE_DIR / _raw_profile_dir)
)
HEADLESS = os.getenv("HEADLESS", "false").lower() in ("true", "1", "yes")

# RPA Behavior
POLL_INTERVAL_MIN = float(os.getenv("POLL_INTERVAL_MIN", "1.2"))
POLL_INTERVAL_MAX = float(os.getenv("POLL_INTERVAL_MAX", "1.8"))

# LLM Settings (OpenAI-compatible, used in standalone mode)
LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# Persona & Prompt Settings
_raw_persona = os.getenv("PERSONA_FILE", "personas/persona-Gemini.md")
PERSONA_FILE = str(
    Path(_raw_persona) if Path(_raw_persona).is_absolute() else (BASE_DIR / _raw_persona)
)
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "8"))
