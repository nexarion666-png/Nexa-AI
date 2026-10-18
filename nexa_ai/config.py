import os
from dotenv import load_dotenv

load_dotenv()

def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_OWNER_ID = os.getenv("TELEGRAM_OWNER_ID", "").strip()
DATABASE_PATH = os.getenv("DATABASE_PATH", "nexa_ai.sqlite3")
MAX_ATTACHMENT_MB = max(1, _int_env("MAX_ATTACHMENT_MB", 15))
MAX_ZIP_TEXT_CHARS = max(1000, _int_env("MAX_ZIP_TEXT_CHARS", 60000))
MAX_HISTORY_MESSAGES = max(4, _int_env("MAX_HISTORY_MESSAGES", 20))

CLEANAPI_API_KEY = os.getenv("CLEANAPI_API_KEY", "").strip()
CLEANAPI_BASE_URL = os.getenv("CLEANAPI_BASE_URL", "https://cleanapis.com/v1").rstrip("/")
CLEANAPI_MODEL = os.getenv("CLEANAPI_MODEL", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()
