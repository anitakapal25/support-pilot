from alembic import context
from app.checkpoints import CHECKPOINT_TABLES
from app.config import Settings
from app.database import make_engine
from app.models import Base


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "table" and name in CHECKPOINT_TABLES)


settings = Settings()
if context.is_offline_mode():
    context.configure(
        url=settings.database_url.get_secret_value(),
        target_metadata=Base.metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    provided = context.config.attributes.get("connection")

    def migrate(connection):
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            compare_type=True,
            version_table_schema=context.config.attributes.get("version_table_schema"),
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()

    if provided is not None:
        migrate(provided)
    else:
        engine = make_engine(settings)
        with engine.connect() as connection:
            migrate(connection)
        engine.dispose()
