# Platform API Reference

## Polymarket

**Type**: Decentralized prediction market on Polygon blockchain
**API**: Central Limit Order Book (CLOB) — REST + WebSocket
**Base URL**: `https://clob.polymarket.com`
**Auth**: EIP-712 signing with Polygon wallet private key
**Settlement**: On-chain (Polygon), USDC

### Key Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/markets` | GET | List active markets |
| `/order-book/{token_id}` | GET | Live orderbook for a market |
| `/orders` | POST | Place a new order |
| `/orders/{order_id}` | DELETE | Cancel an order |
| `/trades` | GET | Your trade history |

### Auth Pattern
```python
# EIP-712 signing — see ryanfrigo/kalshi-ai-trading-bot for implementation
from eth_account import Account
from eth_account.messages import encode_defunct

private_key = os.environ["POLYMARKET_WALLET_PRIVATE_KEY"]
account = Account.from_key(private_key)
# Sign each order with this account
```

### Rate Limits
- REST: 10 requests/second
- WebSocket: 1 connection, 100 subscriptions

### Geo-Restrictions
- Check current allowed jurisdictions before trading
- US users: verify current regulatory status

### Reference
- Docs: https://docs.polymarket.com
- Repo: github.com/CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot

---

## Kalshi

**Type**: US-regulated event contract exchange
**API**: REST
**Base URL (live)**: `https://trading-api.kalshi.com/trade-api/v2`
**Base URL (demo)**: `https://demo-api.kalshi.co/trade-api/v2`
**Auth**: API key + HMAC-SHA256 header signing
**Settlement**: USD (fiat)

### Key Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/markets` | GET | List active markets |
| `/markets/{ticker}/orderbook` | GET | Live orderbook |
| `/portfolio/orders` | POST | Place order |
| `/portfolio/orders/{order_id}` | DELETE | Cancel order |
| `/portfolio/fills` | GET | Fill history |
| `/portfolio/balance` | GET | Account balance |

### Auth Pattern
```python
import hashlib
import hmac
import time

def kalshi_headers(method: str, path: str, body: str = "") -> dict:
    ts = str(int(time.time() * 1000))
    msg = ts + method.upper() + path + body
    sig = hmac.new(
        os.environ["KALSHI_API_SECRET"].encode(),
        msg.encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "KALSHI-ACCESS-KEY": os.environ["KALSHI_API_KEY"],
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": sig,
    }
```

### Demo Environment
- Use `KALSHI_DEMO_URL` with `KALSHI_USE_DEMO=true` in `.env`
- Demo accounts have $10,000 mock USD
- Orders do not affect real markets
- **Use this for all paper trading in Phase 3**

### Rate Limits
- 10 requests/second
- Developer Agreement must be accepted

### Reference
- Docs: https://trading-api.readme.io
- Repo: github.com/suislanchez/polymarket-kalshi-weather-bot

---

## pmxt Library (Unified Wrapper)

A CCXT-inspired unified API wrapper for prediction markets.

**Evaluate in Phase 0**: Clone and run tests. If stable, use as primary client.
If not stable, use direct platform clients above.

```python
# If pmxt is viable:
import pmxt
client = pmxt.Polymarket(config={"apiKey": os.environ["POLYMARKET_API_KEY"]})
markets = client.fetch_markets()
```
