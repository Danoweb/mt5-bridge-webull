# mt5-bridge-webull

A bridge connector that lets a **MetaTrader 5 (MT5)** Expert Advisor use **Webull** as its actual broker/execution backend. MT5 stays your charting and strategy engine; this service receives trade signals from an MT5 EA over HTTP and places the matching orders on Webull.

It ships as a small Python (FastAPI) service in a Docker container, so it runs the same way on a laptop, a home server, or a cheap cloud VM.

```
┌────────────────────┐        HTTPS + API key         ┌──────────────────────┐        Webull OpenAPI (official)     ┌─────────┐
│   MT5 terminal      │  ───────────────────────────▶  │  mt5-bridge-webull    │  ──────────────────────────────▶   │ Webull  │
│  (Expert Advisor)   │  ◀───────────────────────────  │  (this Docker service)│  ◀──────────────────────────────   │ account │
└────────────────────┘        JSON responses           └──────────────────────┘                                     └─────────┘
```

This bridge talks to Webull through their **official OpenAPI** ([developer.webull.com](https://developer.webull.com/apis/docs), package `webull-openapi-python-sdk`) — not a reverse-engineered private API. You'll need to apply for an app key/secret (free, ~1-2 business day review) before you can trade live; see [Setting up Webull](#setting-up-webull).

## ⚠️ Read this before you connect real money

- **This is not investment advice and comes with no warranty.** You are responsible for what it trades. Bugs in strategy logic, symbol mapping, or connectivity can lose you real money.
- **The official OpenAPI is still a young, sparsely-documented SDK.** It's Webull-sanctioned (unlike older reverse-engineered Webull Python packages), but expect rough edges — some response field names in this codebase are inferred defensively rather than pinned to confirmed documentation (see comments in `src/bridge/broker/webull_client.py`). Run with `LOG_LEVEL=DEBUG` and compare raw responses if something looks off, and consider testing against Webull's **sandbox environment** first (no approval needed — see [Setting up Webull](#setting-up-webull)).
- **The bridge has two independent safety layers, and both default to the safe setting.** `WEBULL_MODE` picks which Webull environment you trade against (`paper` = simulated account, real order flow, fake money; `live` = your real brokerage account) and defaults to `paper`. `DRY_RUN` (default `true`) stops the bridge from calling Webull *at all* — it just logs what it would have sent. See [Setting up Webull](#setting-up-webull) for the recommended progression through both before going live.
- **MT5 lots and Webull shares are different units.** You must configure the conversion (`LotToShareFactor` in the EA) and, for most symbols, a manual MT5-symbol → Webull-ticker mapping (see [Symbol Mapping](#symbol-mapping)). There is no universal default that's correct for every broker/symbol.
- **This bridge only places equity orders.** Options/crypto/futures use a different order payload the MT5 EA doesn't produce; out of scope for v1.

## How it works

1. You attach the included Expert Advisor (`mt5/MT5WebullBridgeEA.mq5`) to a chart in MT5.
2. Whenever your MT5 strategy (or manual trading, if you attach it that way) opens, adds to, or closes a position, the EA computes the change and sends an HTTP request to this bridge.
3. The bridge validates the request, applies your symbol mapping, and (unless in dry-run mode) places the equivalent order on Webull using your logged-in Webull session.
4. The bridge exposes `/account`, `/positions`, and `/orders` endpoints so the EA (or you, via `curl`) can check state and manage orders.

MT5's `WebRequest()` is synchronous HTTP, so this is a signal/mirroring integration, not a shared order book — Webull fills happen independently and are not reflected back onto the MT5 chart itself.

## Repository layout

```
src/
  main.py                    # process entrypoint: wires config -> logging -> broker -> API
  bridge/
    api.py                   # FastAPI routes exposed to the MT5 EA
    order_manager.py         # business logic: dry-run, idempotency, position closing
    security.py               # API key authentication
    config.py                 # environment-variable driven settings
    logging_config.py         # logging setup (incl. secret redaction)
    models.py                  # request/response schemas
    broker/
      base.py                 # abstract broker interface
      webull_client.py        # Webull OpenAPI implementation
scripts/
  webull_login.py             # one-time session bootstrap (mobile-app approval)
mt5/
  MT5WebullBridgeEA.mq5        # the MT5 Expert Advisor
tests/                         # pytest suite
```

## Prerequisites

- Docker and Docker Compose (v2, i.e. `docker compose`, not the old `docker-compose`).
- An MT5 terminal (Windows, or Wine/a VPS) with a strategy or manual setup you want mirrored.
- A Webull account with an approved OpenAPI app key/secret (or use the sandbox to start without one — see [Setting up Webull](#setting-up-webull)), and the Webull mobile app installed (used to approve the login).

## Quick start (local / home machine)

```bash
git clone <this-repo-url>
cd mt5-bridge-webull
cp .env.example .env
# Edit .env: set BRIDGE_API_KEY (generate one, see below) and
# WEBULL_PAPER_APP_KEY / WEBULL_PAPER_APP_SECRET (from developer.webull.com,
# see below). Leave WEBULL_MODE=paper and DRY_RUN=true for now.

# 1. One-time session bootstrap for paper mode. Have your phone with the
#    Webull app handy -- this will prompt you to approve a login request there.
docker compose run --rm bridge-login

# 2. Start the bridge
docker compose up -d

# 3. Confirm it's alive
curl http://localhost:5000/health
```

You should see `{"status":"ok","webull_connected":true,"dry_run":true,"mode":"paper"}`. If `webull_connected` is `false`, re-run step 1 and check `docker compose logs bridge`.

### Generating an API key

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```
Put the result in `.env` as `BRIDGE_API_KEY`, and use the same value for `ApiKey` in the MT5 EA's inputs.

## Configuration reference

All configuration is via environment variables (`.env`, loaded by Docker Compose). See `.env.example` for the full list with inline comments. Key ones:

| Variable | Purpose |
|---|---|
| `BRIDGE_API_KEY` | Required. Shared secret the MT5 EA must send as `X-API-Key`. |
| `WEBULL_MODE` | `paper` (default) or `live`. Picks which credential set below is used. |
| `WEBULL_PAPER_APP_KEY`, `WEBULL_PAPER_APP_SECRET` | Required for paper mode. From developer.webull.com (see below). |
| `WEBULL_LIVE_APP_KEY`, `WEBULL_LIVE_APP_SECRET` | Required for live mode. Separate app key from the paper one. |
| `WEBULL_PAPER_ACCOUNT_ID`, `WEBULL_LIVE_ACCOUNT_ID` | Optional per mode. Auto-discovered if unset; only needed with multiple accounts on one app key. |
| `WEBULL_REGION_ID` | Default `us`. Must match the region your app keys were issued for. Shared by both modes. |
| `DRY_RUN` | `true` (default) logs orders instead of sending them to Webull, regardless of mode. |
| `SYMBOL_MAP_FILE` | Optional JSON file mapping MT5 symbols to Webull tickers. |
| `LOG_LEVEL`, `LOG_FILE` | Logging verbosity and optional log file (under `./data`). |

### Symbol mapping

Most MT5 brokers use symbol names that don't exist on Webull as-is (index CFDs like `US500`, suffixed equities like `AAPL.US`, forex pairs Webull doesn't offer, etc.). Create a JSON file such as:

```json
{
  "US500": "SPY",
  "AAPL.US": "AAPL"
}
```

Mount it into the container (add a line under `bridge.volumes` in `docker-compose.yml`, e.g. `./symbol_map.json:/app/data/symbol_map.json:ro`) and set `SYMBOL_MAP_FILE=/app/data/symbol_map.json` in `.env`. Symbols not listed are passed through unchanged, which is only correct for plain US equity tickers.

## Setting up Webull

This bridge uses Webull's **official OpenAPI**, which supports both a **paper** (simulated) and a **live** (real money) trading environment, each with its own app key/secret and its own session. `WEBULL_MODE` in `.env` picks which one the bridge is currently using.

### Paper mode (start here)

1. Go to **[webull.com/center#openApiManagement](https://www.webull.com/center#openApiManagement)** (or the equivalent for your Webull region — see the [SDK README](https://github.com/webull-inc/webull-openapi-python-sdk#requirements) for other regions) and apply for OpenAPI access, choosing **paper/simulated trading** access. This tends to be approved faster than live access, and some Webull docs indicate a shared sandbox app key/secret may be usable immediately without waiting — check the ["Getting Started" docs](https://developer.webull.com/apis/docs/getting-started/).
2. Put the resulting key/secret in `.env` as `WEBULL_PAPER_APP_KEY` / `WEBULL_PAPER_APP_SECRET`. Leave `WEBULL_PAPER_API_ENDPOINT` at its default (`api.sandbox.webull.com`).
3. With `WEBULL_MODE=paper` (the default) in `.env`, run `docker compose run --rm bridge-login` with your phone nearby. Unlike a password login, this doesn't ask you to type anything — it sends an approval request to your Webull mobile app, which you approve there (you have about 5 minutes). Once approved, the SDK saves its own verified session under `./data/webull_token/paper` and reuses it on every future bridge start.
4. Start the bridge (`docker compose up -d`) and wire up MT5 (see [Setting up MT5](#setting-up-mt5)). With `DRY_RUN=false` and `WEBULL_MODE=paper`, orders actually go through Webull's real order pipeline and get simulated fills — this is the best way to validate the whole system end-to-end without risking money.

### Going live

Once paper trading looks correct end-to-end:

1. Repeat the application process above, this time requesting **live** OpenAPI access (approval typically takes 1-2 business days).
2. Put the new key/secret in `.env` as `WEBULL_LIVE_APP_KEY` / `WEBULL_LIVE_APP_SECRET`, and make sure your account has trading permissions for whatever you plan to trade (margin/options/etc. require separate approval from Webull).
3. Bootstrap the live session the same way, but pointed at live mode: `docker compose run --rm -e WEBULL_MODE=live bridge-login`. This creates a separate session under `./data/webull_token/live` — it doesn't touch or invalidate your paper session.
4. In `.env`, set `WEBULL_MODE=live` but **leave `DRY_RUN=true`** for one more restart. Confirm `/health` shows `{"mode":"live", ...}` and that a test signal from MT5 produces the log line you expect, without anything reaching Webull yet.
5. Only then set `DRY_RUN=false` and restart. You are now trading with real money.

If a session later expires or is revoked (`webull_connected: false` in `/health`, or errors in `docker compose logs bridge`), re-run the bootstrap step for whichever mode is affected.

## Setting up MT5

1. Copy `mt5/MT5WebullBridgeEA.mq5` into your MT5 terminal's `MQL5/Experts/` folder (in MetaEditor: **File → Open Data Folder → MQL5 → Experts**), then compile it (F7 in MetaEditor).
2. In the MT5 terminal: **Tools → Options → Expert Advisors** → check **"Allow WebRequest for listed URL"** and add your bridge's base URL (e.g. `https://your-tunnel-domain.com` or `http://<home-ip>:5000` for a purely local setup). MT5 refuses any `WebRequest()` call to a non-whitelisted URL.
3. Drag the EA onto a chart for each symbol/strategy you want mirrored. Set the inputs:
   - `BridgeUrl` — must match what you whitelisted in step 2, no trailing slash.
   - `ApiKey` — same value as `BRIDGE_API_KEY` in `.env`.
   - `LotToShareFactor` — shares sent to Webull per 1.0 MT5 lot for that symbol. Work this out from your broker's contract size (e.g. if 1.0 lot = 100 shares equivalent, use `100`).
   - `MagicNumber` — only positions opened with this magic number are mirrored; use a distinct value per strategy if you run several.
4. Check the **Experts** tab in MT5 for a `bridge reachable at ...` log line confirming connectivity, and check the bridge's own logs (`docker compose logs -f bridge`) for the corresponding request.
5. Trade on a demo MT5 account with `DRY_RUN=true` first and confirm the bridge logs show the orders you expect, then work through the paper → live progression in [Setting up Webull](#setting-up-webull).

## Exposing the bridge publicly

You only need this if MT5 and the bridge run on *different* machines/networks (e.g. MT5 on a Windows VPS, bridge on your home server). If both run on the same machine or LAN, `http://<local-ip>:5000` and a LAN-only MT5 WebRequest whitelist entry is enough — skip this section.

### Recommended: Cloudflare Tunnel (free, works from a home machine)

Cloudflare Tunnel gives you a stable HTTPS URL for a service running behind NAT/no public IP, without opening any ports on your router. It's free for this use case.

1. Get a domain onto Cloudflare (free plan is fine) if you don't already have one there.
2. Install `cloudflared` on the machine running the bridge (or run it as a sidecar container — see [Cloudflare's Docker instructions](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-remote-tunnel/)).
3. `cloudflared tunnel login`, then `cloudflared tunnel create mt5-bridge`.
4. Route a hostname to it: `cloudflared tunnel route dns mt5-bridge bridge.yourdomain.com`.
5. Point the tunnel at the container: `cloudflared tunnel --url http://localhost:5000 run mt5-bridge` (or configure it via a `config.yml` for a persistent service).
6. Use `https://bridge.yourdomain.com` as the `BridgeUrl` EA input and in the MT5 WebRequest whitelist.

This also means your Webull session/trade PIN never has to leave your own machine — only the bridge's HTTP endpoints are exposed, and they're gated by `BRIDGE_API_KEY`.

### Alternative: GCP free tier (e2-micro)

GCP's [Always Free tier](https://cloud.google.com/free) includes one `e2-micro` VM in specific US regions — enough to run this container 24/7 at no cost.

1. Create an `e2-micro` instance (region `us-west1`, `us-central1`, or `us-east1` for free-tier eligibility) with a small persistent disk.
2. Install Docker: `curl -fsSL https://get.docker.com | sh`.
3. `git clone` this repo onto the VM, follow the Quick Start steps above.
4. Open port 5000 in the VPC firewall (or, better, put it behind a reverse proxy with TLS, e.g. Caddy, and only open 443) restricted to MT5's source IP if it's static.

### Alternative: AWS free tier

AWS's 12-month Free Tier includes a `t2.micro`/`t3.micro` EC2 instance. Setup is the same as GCP above (Docker + this repo + a security group rule opening the port you need). After 12 months this stops being free — a small **Lightsail** instance (~$3.50–5/mo) is the cheapest steady-state AWS option if you want to stay on AWS.

Whichever cloud option you use, still put `BRIDGE_API_KEY` behind HTTPS (a reverse proxy like Caddy/nginx with Let's Encrypt, or Cloudflare in front of the VM) rather than exposing plain HTTP with a key over the open internet.

## API reference

All endpoints except `/health` require header `X-API-Key: <BRIDGE_API_KEY>`.

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness + connection/dry-run/mode status. No auth required. |
| GET | `/account` | Webull account balance/buying power. |
| GET | `/positions` | All open Webull positions. |
| GET | `/positions/{symbol}` | Single position, 404 if flat. |
| POST | `/positions/{symbol}/close` | Market-close the full position in `symbol`. |
| POST | `/orders` | Place an order. Body: `{symbol, side, quantity, order_type, limit_price?, stop_price?, time_in_force?, client_order_id?}`. |
| GET | `/orders/{order_id}` | Order status. |
| DELETE | `/orders/{order_id}` | Cancel an order. |

Interactive docs (Swagger UI) are available at `http://<bridge-host>:5000/docs` — useful for manually testing requests with `curl`/Postman before wiring up the EA.

## Running the tests

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export BRIDGE_API_KEY=test   # only needed if you also import src/main.py directly
pytest -v
```

The suite mocks both the HTTP layer (FastAPI `TestClient`) and the Webull SDK, so it needs no real credentials or network access, and runs in about a second. It covers: API auth, request validation, dry-run vs. live order routing, idempotent duplicate-order handling, position-close logic, and the Webull client's account discovery/response parsing/symbol mapping.

## Troubleshooting

- **MT5 Experts log shows `WebRequest failed, error 4060`** — the URL isn't whitelisted (Tools → Options → Expert Advisors). It must match exactly, including `https://` vs `http://`.
- **`/health` shows `webull_connected: false`** — no valid session for the *current* `WEBULL_MODE`; run `docker compose run --rm bridge-login` (add `-e WEBULL_MODE=live` if you're bootstrapping live, not paper).
- **`bridge-login` hangs / times out after ~5 minutes** — you didn't approve the login request in the Webull mobile app in time, or the app is signed into a different account than the one your app key belongs to. Just re-run it.
- **`/health` shows the wrong `mode`** — check `WEBULL_MODE` in `.env` and that you restarted the container after changing it (`docker compose up -d` picks up `.env` changes on restart, not automatically).
- **Orders return `REJECTED`** — check `docker compose logs bridge`; the response from Webull (e.g. insufficient buying power, market closed, invalid symbol) is logged at ERROR level.
- **`/account` or `/positions` return zeroes/empty when you expect real data** — Webull's exact response field names aren't fully pinned down in this codebase (see the module docstring in `src/bridge/broker/webull_client.py`); set `LOG_LEVEL=DEBUG` and compare the logged raw response against the field names in `_first_present(...)` calls there, then adjust.
- **Duplicate trades on Webull for one MT5 trade** — make sure your EA is sending a `client_order_id` derived from something stable per trade (the shipped EA already does this); the bridge deduplicates on that field.

## Security notes

- `BRIDGE_API_KEY` is compared using a constant-time comparison to resist timing attacks, and is redacted from logs, along with both the paper and live Webull app secrets.
- The long-running service only ever holds the app key/secret and a verified session token — never an account password (the official OpenAPI doesn't use one).
- Paper and live credentials, sessions, and token directories are entirely separate (`./data/webull_token/paper` vs `./data/webull_token/live`), so a bug or leak in one mode can't reach the other's session.
- Session tokens, logs, and any symbol map live under `./data`, which is gitignored — do not commit it. Note the Webull SDK also writes its own `webull_trade_sdk.log` inside the container's working directory (not under `./data`), independent of this bridge's own logging.
- Put the bridge behind HTTPS whenever it's reachable over the public internet (Cloudflare Tunnel does this for you automatically).
