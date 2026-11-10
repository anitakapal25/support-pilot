"""Honor the platform port and allow bounded in-flight requests to finish on SIGTERM."""

import os

import uvicorn


def server_options() -> dict:
    # Behind a platform proxy (Render), request.client is the proxy unless its
    # X-Forwarded-For header is trusted. Login throttling keys on the client IP,
    # so trust is configured explicitly. Only set "*" when the container is
    # reachable solely through that proxy; the default trusts loopback only.
    return {
        "factory": True,
        "host": "0.0.0.0",
        "port": int(os.environ.get("PORT", "8000")),
        "proxy_headers": True,
        "forwarded_allow_ips": os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        "timeout_graceful_shutdown": 40,
        "access_log": False,
    }


def prepare() -> None:
    """Free hosting has no pre-deploy step: migrate and activate the corpus before serving.

    Both are idempotent. Ingest re-embeds only articles whose fingerprint changed, so an
    unchanged corpus costs nothing; a brand-new database is rebuilt without manual steps.
    """
    from pathlib import Path

    from app import migrate
    from app.config import Settings
    from app.database import make_engine
    from app.embeddings import make_embedder
    from app.ingest import ingest

    migrate.main()
    settings = Settings()
    engine = make_engine(settings)
    try:
        corpus = Path(__file__).resolve().parents[2] / "knowledge"
        ingest(engine, corpus, make_embedder(settings, engine), settings)
    finally:
        engine.dispose()


def main():
    if os.environ.get("RUN_MIGRATIONS_ON_START", "").lower() in {"1", "true", "yes"}:
        prepare()
    uvicorn.run("app.main:create_app", **server_options())


if __name__ == "__main__":
    main()
