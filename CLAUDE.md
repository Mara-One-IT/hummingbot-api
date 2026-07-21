# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Hummingbot API** is a FastAPI REST service for managing trading bots across multiple cryptocurrency exchanges, with MCP (Model Context Protocol) integration for AI assistants and secure Gateway (DEX) trading support.

## Architecture

The application follows a **service-driven architecture** with clear separation of concerns:

```
main.py (FastAPI app + lifespan management)
├── routers/         (API endpoints)
├── models/          (Pydantic request/response schemas)
├── services/        (Business logic)
├── database/        (SQLAlchemy models + async connection)
├── utils/           (Helpers: file system, security, MQTT, etc.)
├── config.py        (Pydantic Settings from environment)
└── deps.py          (FastAPI dependency injection)
```

### Core Services

- **UnifiedConnectorService**: Single source of truth for all exchange connectors (Binance, Coinbase, etc.)
- **MarketDataService**: Candle feeds, order books, tickers, cross-rate pricing; manages feed lifecycle
- **TradingService**: Order placement, position management, trading interfaces
- **AccountsService**: Account management, balance tracking, portfolio health
- **ExecutorService**: Manages strategy executors with periodic updates and retries
- **BotsOrchestrator**: MQTT broker communication with deployed bots
- **GatewayService**: DEX trading via mTLS-secured Gateway (auto-generates certs)
- **TradingHistoryService**: Read-only persistence queries for orders, trades, funding rates

### Dependency Flow

Services are initialized in `main.py` lifespan in dependency order:
1. Infrastructure (Gateway, secrets, database)
2. UnifiedConnectorService (central connector management)
3. MarketDataService, TradingService (depend on connectors)
4. AccountsService, TradingHistoryService
5. ExecutorService (depends on trading)
6. Other services (BotsOrchestrator, GatewayService, DockerService, etc.)

This order prevents circular dependencies. Routes access services via FastAPI dependency injection (`deps.py`).

## Quick Start

### Development Setup

```bash
# Create conda environment with all dependencies
make install

# Activate the environment
conda activate hummingbot-api

# Run API locally with hot-reload (auto-starts PostgreSQL and EMQX)
make run

# API available at http://localhost:8000 (or http://127.0.0.1:8000)
# Swagger UI: http://localhost:8000/docs
```

### Docker Deployment

```bash
# Create .env file with configuration (username, password, etc.)
make setup

# Start all services (API, PostgreSQL, EMQX) in Docker
make deploy

# Stop all services
make stop

# Check Tailscale connection status (if enabled)
make tailscale-status

# Reset to near-origin state (wipe .env, credentials, volumes)
make reset
```

## Development Commands

### Running and Testing

```bash
# Run in dev mode with hot-reload
make run

# Run tests
pytest test/

# Run a single test
pytest test/test_portfolio_analytics.py::test_specific_function

# Format and lint code
isort .
black .
flake8 --max-line-length=130
```

The Makefile integrates with pre-commit hooks automatically—see **Pre-commit Hooks** below.

### Database

Database initialization is automatic on app startup (`db_manager.create_tables()` in `main.py` lifespan). To reset to a clean database state:

```bash
# Stop Docker containers and wipe volumes
docker compose down -v

# Then restart
make deploy
```

The schema is defined in `database/models.py` using SQLAlchemy ORM; changes to ORM models are reflected automatically on next startup.

### Environment Configuration

All runtime settings come from the `.env` file (created by `make setup`) or environment variables:

```bash
# API credentials
USERNAME=admin              # HTTP Basic Auth username
PASSWORD=admin              # HTTP Basic Auth password
CONFIG_PASSWORD=admin       # Encrypts bot credentials and secrets

# Database
DATABASE_URL=postgresql+asyncpg://hbot:hummingbot-api@localhost:5432/hummingbot_api

# MQTT Broker (bot communication)
BROKER_HOST=localhost
BROKER_PORT=1883
BROKER_USERNAME=admin
BROKER_PASSWORD=password

# Gateway (DEX trading)
GATEWAY_URL=https://localhost:15888

# Market data
MARKET_DATA_CLEANUP_INTERVAL=300        # Feed cleanup frequency (seconds)
MARKET_DATA_FEED_TIMEOUT=600            # Unused feed TTL (seconds)
MARKET_DATA_TICKER_UPDATE_INTERVAL=30   # Ticker refresh rate (seconds)

# Tailscale (for secure VPS deployments)
TAILSCALE_ENABLED=false
TAILSCALE_AUTH_KEY=tskey-auth-...
TAILSCALE_HOSTNAME=hummingbot-api

# AWS S3 (bot archival)
AWS_API_KEY=...
AWS_SECRET_KEY=...
AWS_S3_DEFAULT_BUCKET_NAME=...
```

Edit `.env` and restart with `make deploy` to apply changes.

## Code Structure by Feature

### Connectors & Market Data

- `services/unified_connector_service.py`: Manages connectors (Binance, Coinbase, Kraken, etc.)
- `services/market_data_service.py`: Orchestrates candle feeds and tickers
- `services/candle_feeds.py`: Per-connector candle feed implementations
- `services/ticker_sources.py`: Per-connector ticker data sources
- `routers/market_data.py`: Market data REST endpoints
- `models/market_data.py`: Market data request/response schemas

### Trading

- `services/trading_service.py`: Order placement and position management
- `routers/trading.py`: Trading REST endpoints
- `models/trading.py`: Trading request/response schemas
- `services/orders_recorder.py`: Persists order/trade events to PostgreSQL

### Accounts & Portfolio

- `services/accounts_service.py`: Account management, balance tracking, portfolio health checks
- `routers/accounts.py`: Account REST endpoints
- `models/accounts.py`: Account request/response schemas
- `database/repositories/`: Repositories for account/balance queries

### Strategy Execution

- `services/executor_service.py`: Lifecycle management for deployed strategy executors
- `routers/executors.py`: Executor control REST endpoints
- `models/executors.py`: Executor request/response schemas
- `services/executor_ws_manager.py`: WebSocket manager for executor status updates

### Gateway (DEX Trading)

- `services/gateway_service.py`: Gateway lifecycle (start/stop), cert reconciliation
- `services/gateway_client.py`: HTTP client to Gateway
- `services/gateway_wallet_service.py`: DEX wallet operations
- `services/gateway_transaction_poller.py`: Polls completed DEX transactions
- `routers/gateway.py`: Gateway control REST endpoints
- `routers/gateway_swap.py`: DEX swap endpoints
- `routers/gateway_clmm.py`: Concentrated liquidity market-maker endpoints
- `utils/gateway_certs.py`: mTLS certificate generation and sync

### Bots & Orchestration

- `services/bots_orchestrator.py`: MQTT client for deployed bot communication
- `routers/bot_orchestration.py`: Bot deployment and control
- `utils/mqtt_manager.py`: MQTT message handling

### Database & Persistence

- `database/connection.py`: Async PostgreSQL connection and session management
- `database/models.py`: SQLAlchemy ORM models (accounts, orders, trades, funding, etc.)
- `database/repositories/`: Specialized query repositories
- `services/trading_history_service.py`: Read-only history queries

### Configuration & Security

- `config.py`: Pydantic Settings for all configuration
- `utils/security.py`: Password verification and credential encryption
- `utils/bot_archiver.py`: Archive executors to AWS S3

## Pre-commit Hooks

Pre-commit hooks are automatically installed by `make install` and run before each commit:

- **detect-private-key**: Blocks commits of private keys
- **detect-wallet-private-key**: Blocks commits of wallet private keys
- **isort**: Sorts imports (configured in `pyproject.toml`)
- **flake8**: Lints Python (max line length 130)

Run hooks manually:

```bash
pre-commit run --all-files
```

## Key Security Considerations

1. **Default Credentials (SEC-018)**: The `.env` file ships with `USERNAME=admin`, `PASSWORD=admin`, and `CONFIG_PASSWORD=a`. The app warns loudly on startup if these are still in use. Always change them in production.

2. **Gateway mTLS**: When Gateway runs in production, it requires TLS + mutual-cert auth. The API auto-generates certificates on first start and stores them in `bots/gateway-files/certs/`. There is no development/insecure mode—a Gateway holding wallet keys must never be served over plain HTTP.

3. **Secrets Encryption**: The `CONFIG_PASSWORD` encrypts all stored credentials (exchange API keys, etc.) using `ETHKeyFileSecretManger`. Rotate it only if necessary (requires re-encryption of all secrets).

4. **Tailscale for Production**: The README recommends Tailscale for VPS deployments to avoid exposing port 8000 publicly. Port forwarding and firewall rules are less secure.

## Testing

Tests are in the `/test` directory and use pytest. Some require Docker and PostgreSQL running.

```bash
# Run all tests
pytest test/

# Run with verbose output
pytest -v test/

# Run a specific test file
pytest test/test_portfolio_analytics.py

# Run tests matching a pattern
pytest test/ -k "portfolio"

# Show print statements during test runs
pytest test/ -s
```

Key test files:
- `test_cors_settings.py`: CORS middleware configuration
- `test_portfolio_analytics.py`: Portfolio health calculations
- `test_portfolio_state.py`: Portfolio state tracking
- `test_gateway_lp_executor.py`: Liquidity provider strategy execution

When adding tests, ensure they run with the same dependencies available during development (connectors, market data service, etc.).

## Versioning & Releases

The version is defined in `main.py` as a simple string:

```python
VERSION = "1.0.1"
```

When a PR is merged to `main`, GitHub Actions (`docker_buildx_workflow.yml`) automatically:
1. Extracts the VERSION from `main.py`
2. Builds Docker images for Linux amd64 and arm64
3. Pushes to Docker Hub with tags `latest` and `{VERSION}`

Update the VERSION string in `main.py` before merging a release PR.

## Debugging Tips

### API won't start?
```bash
docker compose logs hummingbot-api
```

### Database issues?
```bash
docker compose down -v    # Wipe volumes
make deploy               # Fresh start
```

### Check service status:
```bash
docker ps | grep hummingbot
```

### Check logs for a specific service:
```bash
docker compose logs postgres     # Database
docker compose logs emqx         # MQTT broker
docker compose logs hummingbot-api  # API
```

### Inspect the running database:
```bash
# Connect to PostgreSQL
psql -h localhost -U hbot -d hummingbot_api

# List tables
\dt

# Exit
\q
```

### Tailscale debugging:
```bash
# Check Tailscale status
make tailscale-status

# Ensure MagicDNS is enabled in Tailscale admin console
# Verify the node appears in `tailscale status`
```

## Useful References

- **API Docs**: http://localhost:8000/docs (Swagger UI)
- **Project Docs**: https://hummingbot.org/hummingbot-api/
- **Tailscale Guide**: https://hummingbot.org/hummingbot-api/tailscale/
- **Issues**: https://github.com/hummingbot/hummingbot-api/issues
- **Hummingbot Docs**: https://hummingbot.org/

## Notes on Development Workflow

- **Environment Setup**: Always run `make install` once after cloning to set up conda, pre-commit, and `.env`.
- **Hot Reload**: `make run` enables hot-reload—changes to Python files restart the API automatically.
- **Database Migrations**: The app creates/updates tables automatically on startup; no manual migration step needed.
- **Commit Style**: Recent commits follow `<type>(<scope>): <message>` format (e.g., `fix(rates): tolerate both names of the renamed token-symbol helper`).
- **Docker Images**: The Docker image is built and pushed on every merged PR; no need to build manually unless testing locally.
