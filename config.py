"""All settings come from environment variables (or a .env file next to this one)."""
import os
from pathlib import Path


def _load_dotenv() -> None:
    env = Path(__file__).with_name(".env")
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _get(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name, "").strip()
    return v or default


def _float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except ValueError:
        return default


TELEGRAM_TOKEN = _get("TELEGRAM_TOKEN")
BOT_NAME = _get("BOT_NAME", "דילוש")
ADMIN_IDS = {int(x) for x in (_get("ADMIN_IDS", "") or "").replace(" ", "").split(",") if x.isdigit()}
DB_PATH = _get("DB_PATH", str(Path(__file__).with_name("bot.db")))

# Comparison rules
CHEAPER_THRESHOLD = _float("CHEAPER_THRESHOLD", 0.05)    # say "cheaper" only above a 5% gap
WATCH_INTERVAL_HOURS = _float("WATCH_INTERVAL_HOURS", 6)  # price-alert check frequency

# Screenshot reading - fill ONE of these. Without a key the bot asks for a link/text instead.
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = _get("ANTHROPIC_MODEL", "claude-haiku-5-5")
GEMINI_API_KEY = _get("GEMINI_API_KEY")
GEMINI_MODEL = _get("GEMINI_MODEL", "gemini-2.5-flash")

# Affiliate settings - leave empty until the accounts are approved. The bot works without them.
TP_MARKER = _get("TP_MARKER")
TP_REDIRECT_TEMPLATE = _get("TP_REDIRECT_TEMPLATE")
TP_PROGRAM_AGODA = _get("TP_PROGRAM_AGODA")
TP_PROGRAM_TRIP = _get("TP_PROGRAM_TRIP")
TP_PROGRAM_BOOKING = _get("TP_PROGRAM_BOOKING")
TP_PROGRAM_EXPEDIA = _get("TP_PROGRAM_EXPEDIA")
AGODA_CID = _get("AGODA_CID")
# Trip.com direct affiliate (approved). trip_sub1 tags the traffic source.
TRIP_ALLIANCE_ID = _get("TRIP_ALLIANCE_ID")
TRIP_SID = _get("TRIP_SID")
BOOKING_AID = _get("BOOKING_AID")
