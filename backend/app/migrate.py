"""Apply application and framework-owned migrations before starting the API."""

from alembic import command
from alembic.config import Config

from app.checkpoints import setup_checkpoints
from app.config import Settings
from app.database import make_engine


def main() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    engine = make_engine(Settings())
    try:
        setup_checkpoints(engine)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
