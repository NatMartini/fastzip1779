from logging.config import fileConfig

from alembic import context
from fastzip.config import settings
from fastzip.db import engine
from fastzip.models import Base

fileConfig(context.config.config_file_name)
if context.is_offline_mode():
    context.configure(
        url=settings().database_url, target_metadata=Base.metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine().connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
