"""
generate_polymarket_creds.py — One-time script to generate Polymarket API credentials.

Run this ONCE to derive your API key, secret, and passphrase from your wallet private key.
Paste the output into your .env file.

Usage:
    # Mainnet (Polygon, chain_id=137):
    python scripts/generate_polymarket_creds.py

    # Testnet (Amoy, chain_id=80002):
    python scripts/generate_polymarket_creds.py --demo
"""

import argparse
import os
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--demo", action="store_true", help="Derive credentials for Amoy testnet (chain_id=80002)")
args = parser.parse_args()

# Load .env
env_path = Path(__file__).resolve().parents[1] / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            value = value.split(" #")[0].strip()
            os.environ.setdefault(key.strip(), value)

try:
    from py_clob_client.client import ClobClient
except ImportError:
    print("ERROR: py-clob-client not installed. Run: pip install py-clob-client")
    sys.exit(1)

if args.demo:
    # Amoy testnet — reads POLYMARKET_DEMO_WALLET_PRIVATE_KEY, falls back to mainnet key
    private_key = (
        os.environ.get("POLYMARKET_DEMO_WALLET_PRIVATE_KEY", "")
        or os.environ.get("POLYMARKET_WALLET_PRIVATE_KEY", "")
    )
    chain_id = 80002
    env_prefix = "POLYMARKET_DEMO"
    label = "Amoy testnet (chain_id=80002)"
else:
    private_key = os.environ.get("POLYMARKET_WALLET_PRIVATE_KEY", "")
    chain_id = 137
    env_prefix = "POLYMARKET"
    label = "Polygon mainnet (chain_id=137)"

if not private_key:
    key_var = "POLYMARKET_DEMO_WALLET_PRIVATE_KEY" if args.demo else "POLYMARKET_WALLET_PRIVATE_KEY"
    print(f"ERROR: {key_var} is not set in your .env file.")
    sys.exit(1)

print(f"Deriving credentials for {label}...")

# Credential derivation always uses chain_id=137 (the CLOB API server runs on Polygon mainnet).
# The chain_id in the ClobClient used for *order signing* differs (80002 for Amoy),
# but the L1 auth endpoint always expects a Polygon-signed EIP-712 message.
client = ClobClient(
    host="https://clob.polymarket.com",
    key=private_key,
    chain_id=137,
)

print(f"Wallet address derived from private key: {client.get_address()}")
print("(Verify this matches your MetaMask address before continuing)")
input("Press Enter to continue...")

creds = client.create_or_derive_api_creds()

print(f"\n--- Paste these into your .env ---\n")
print(f"{env_prefix}_API_KEY={creds.api_key}")
print(f"{env_prefix}_API_SECRET={creds.api_secret}")
print(f"{env_prefix}_API_PASSPHRASE={creds.api_passphrase}")
print("\n----------------------------------")
print("\nDone. Keep these values secret — do not commit them to git.")
