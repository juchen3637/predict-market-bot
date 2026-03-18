"""
generate_polymarket_creds.py — One-time script to generate Polymarket API credentials.

Run this ONCE to derive your API key, secret, and passphrase from your wallet private key.
Paste the output into your .env file.

Usage:
    pip install py-clob-client
    python scripts/generate_polymarket_creds.py
"""

import os
import sys
from pathlib import Path

# Load .env so POLYMARKET_WALLET_PRIVATE_KEY is available
env_path = Path(__file__).resolve().parents[1] / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            # Strip inline comments (e.g. "value  # comment" → "value")
            value = value.split(" #")[0].strip()
            os.environ.setdefault(key.strip(), value)

try:
    from py_clob_client.client import ClobClient
except ImportError:
    print("ERROR: py-clob-client not installed. Run: pip install py-clob-client")
    sys.exit(1)

private_key = os.environ.get("POLYMARKET_WALLET_PRIVATE_KEY", "")
if not private_key:
    print("ERROR: POLYMARKET_WALLET_PRIVATE_KEY is not set in your .env file.")
    sys.exit(1)

print("Connecting to Polymarket CLOB and deriving credentials...")

client = ClobClient(
    host="https://clob.polymarket.com",
    key=private_key,
    chain_id=137,  # Polygon mainnet
)

creds = client.create_or_derive_api_creds()

print("\n--- Paste these into your .env ---\n")
print(f"POLYMARKET_API_KEY={creds.api_key}")
print(f"POLYMARKET_API_SECRET={creds.api_secret}")
print(f"POLYMARKET_API_PASSPHRASE={creds.api_passphrase}")
print("\n----------------------------------")
print("\nDone. Keep these values secret — do not commit them to git.")
