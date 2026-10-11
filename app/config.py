"""All settings live here. Values come from the .env file."""
import os
from dotenv import load_dotenv

load_dotenv()

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "").strip()
PAYPAL_SECRET = os.getenv("PAYPAL_SECRET", "").strip()
PAYPAL_BASE = os.getenv("PAYPAL_BASE", "https://api-m.sandbox.paypal.com").strip()
MERCHANT_EMAIL = os.getenv("MERCHANT_EMAIL", "").strip()
# Mock mode = fake PayPal that runs on your laptop. Used when no keys are set.
PAYPAL_MOCK = os.getenv("PAYPAL_MOCK", "0") == "1" or not PAYPAL_CLIENT_ID

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock").strip().lower()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5").strip()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini").strip()

DB_PATH = os.getenv("DB_PATH", "data/settle.db")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")


def llm_enabled() -> bool:
    if LLM_PROVIDER == "anthropic":
        return bool(ANTHROPIC_API_KEY)
    if LLM_PROVIDER == "openai":
        return bool(LLM_API_KEY)
    return False
