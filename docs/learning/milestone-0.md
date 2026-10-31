# Milestone 0: understand the foundation

## Goal

Explain how an HTTP request reaches Python code, how a database connection is checked,
and how the result becomes validated JSON. Run the API and PostgreSQL locally.

## Concepts through your PHP/JavaScript experience

| Python concept | Familiar comparison | What to notice |
|---|---|---|
| Virtual environment | Project-local node_modules | Isolates installed Python dependencies |
| Type annotation (`status: str`) | TypeScript annotation | Usually a development aid, not runtime enforcement |
| Pydantic model | Runtime DTO/schema validation | Validates values at runtime and describes OpenAPI schemas |
| FastAPI route decorator | Router registration | Maps HTTP method/path to a Python function |
| `with` context manager | try/finally | Releases a connection even when a query raises |
| App factory | Application bootstrap function | Lets tests create isolated app instances |
| SQLAlchemy engine | Database connection pool | Connections are borrowed per operation, not opened per import |
| Lifespan | Startup/shutdown hooks | Creates the engine at startup and disposes it at shutdown |

## Walk the code in this order

1. `backend/app/config.py`: `Settings` loads environment variables; `SecretStr`
   masks accidental representations. It does not encrypt the secret.
2. `backend/app/database.py`: `make_engine` builds a bounded pool. `check_database`
   borrows a connection, sends `SELECT 1`, and returns the connection to the pool.
3. `backend/app/main.py`: `create_app` registers endpoints and a lifespan manager.
   `/health/live` returns without querying PostgreSQL. `/health/ready` checks PostgreSQL
   and returns HTTP 503 if unavailable. FastAPI validates against `HealthStatus`.
4. `backend/app/schemas.py`: `OrderStatus` defines the next milestone's response
   vocabulary. `Literal` rejects unknown states; `extra='forbid'` rejects unexpected
   fields. This schema alone does not enforce ownership.
5. `tests/test_foundation.py`: test the real route, schema, database-outage behavior,
   and optionally PostgreSQL itself. SQLite here only checks the simple health query;
   it does not substitute for PostgreSQL security or transaction tests.

Current request path:

`HTTP GET /health/ready → ready() → check_database() → SELECT 1 → HealthStatus → JSON`

The authenticated order route/service/repository path belongs to milestone 1. We do
not expose an order endpoint before ownership enforcement exists.

## Async without mystery

`async def` is useful when operations can yield control while waiting for network I/O.
It does not automatically make a synchronous driver nonblocking. Current database
handlers use `def`, so FastAPI can run them outside the event-loop thread. The lifespan
function uses `async def` because that is the application lifecycle interface.

## Hands-on exercises

1. Run Compose, visit `/docs`, and execute both health endpoints.
2. Run `docker compose stop db`. Observe that liveness remains 200 and readiness
   becomes 503. Then run `docker compose start db` and verify recovery.
3. In a Python shell, construct an `OrderStatus` with a valid status and timestamps.
   Try `status='refunded'` and explain the validation error.
4. Add a small Pydantic contract for a future shipping estimate as a practice exercise;
   explain required versus optional fields. Do not wire it to business actions yet.

## Learning checkpoint — answer before milestone 1

- Why are liveness and readiness separate?
- Which line sends SQL, and which construct releases the connection?
- How do type hints differ from Pydantic validation?
- Why does `OrderStatus` reject `customer_id`, and why is that not authorization?
- Why does the readiness endpoint use `def` rather than `async def`?
- What evidence tells us PostgreSQL really worked rather than only a mock?

## Interview practice

Explain the request path in two minutes. Describe one tradeoff: a modular monolith
reduces operational moving parts while preserving domain boundaries that can later
be extracted. Describe one safety property: a database outage produces an honest
503 without leaking connection credentials.

## References

- https://fastapi.tiangolo.com/tutorial/response-model/
- https://fastapi.tiangolo.com/advanced/events/
- https://docs.sqlalchemy.org/en/20/core/connections.html
