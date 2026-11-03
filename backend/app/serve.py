"""Honor the platform port and allow bounded in-flight requests to finish on SIGTERM."""

import os

import uvicorn


def main():
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        timeout_graceful_shutdown=40,
        access_log=False,
    )


if __name__ == "__main__":
    main()
