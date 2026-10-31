"""Create only fresh, isolated schemas; never truncate or seed application tables."""

from contextlib import contextmanager
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.checkpoints import setup_checkpoints
from app.seed import seed_demo


@contextmanager
def evaluation_workspace(url):
    schema = "eval_m6_" + uuid4().hex
    admin = create_engine(url)
    engine = None
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        engine = create_engine(
            make_url(url).update_query_dict({"options": f"-c search_path={schema},public"})
        )
        config = Config("alembic.ini")
        config.attributes["version_table_schema"] = schema
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        seed_demo(engine)
        setup_checkpoints(engine)
        yield engine
    finally:
        if engine:
            engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
