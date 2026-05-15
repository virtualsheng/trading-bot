"""
check_env.py — Validates .env has all required keys for dual-account mode.
Called by start_bot.bat before launching. Exits with code 1 if anything missing.
"""
from dotenv import load_dotenv
import os, sys

load_dotenv()

required = [
    "ALPACA_API_KEY_ORB",
    "ALPACA_API_SECRET_ORB",
    "ALPACA_API_KEY_SWING",
    "ALPACA_API_SECRET_SWING",
]

missing = [k for k in required if not os.getenv(k)]

if missing:
    print("ERROR: Missing keys in .env:")
    for k in missing:
        print(f"   {k}")
    print()
    print("See .env.example for the correct format.")
    sys.exit(1)

print("  .env OK — both account keys present")
sys.exit(0)
