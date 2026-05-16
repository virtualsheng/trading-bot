"""
check_env.py — Validates .env has all required keys.
Called by start_bot.bat before launching.
Exits with code 1 if anything is missing.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

REQUIRED = [
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
]

OPTIONAL = [
    ("ALPACA_IS_PAPER",       "true"),
    ("EMAIL_SENDER",          "(not set — email notifications disabled)"),
    ("EMAIL_PASSWORD",        "(not set — email notifications disabled)"),
    ("EMAIL_RECIPIENT",       "(not set — email notifications disabled)"),
    ("DISCORD_WEBHOOK_URL",   "(not set — Discord notifications disabled)"),
    ("TELEGRAM_BOT_TOKEN",    "(not set — Telegram notifications disabled)"),
    ("TELEGRAM_CHAT_ID",      "(not set — Telegram notifications disabled)"),
]

missing = [k for k in REQUIRED if not os.getenv(k)]

if missing:
    print()
    print("  ERROR: Missing required keys in .env:")
    for k in missing:
        print(f"    {k}")
    print()
    print("  See .env.example for the correct format.")
    print()
    sys.exit(1)

# Show status
is_paper = os.getenv("ALPACA_IS_PAPER", "true").lower() == "true"
mode_str = "📄 PAPER" if is_paper else "💰 LIVE ⚠️"

print()
print(f"  .env OK — Alpaca credentials present")
print(f"  Mode: {mode_str}")
print()

# Warn about missing optional keys
missing_optional = [k for k, _ in OPTIONAL if not os.getenv(k)]
if missing_optional:
    print("  Optional (notifications):")
    for k, default in OPTIONAL:
        val = os.getenv(k)
        if val:
            # Mask secrets
            display = val if k in ("ALPACA_IS_PAPER",) else val[:4] + "..." + val[-4:] if len(val) > 8 else "***"
            print(f"    ✅ {k} = {display}")
        else:
            print(f"    ⚠️  {k} = {default}")
    print()

sys.exit(0)