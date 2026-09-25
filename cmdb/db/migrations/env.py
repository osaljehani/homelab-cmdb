from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

from cmdb.domain.models import Base
from cmdb.config import settings

config = context.config
config.set_main_option("sqlalchemy.url", settings.db_url)

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, and alembic.ini's [loggers]
    # lists only root/sqlalchemy/alembic. run_migrations() runs inside the web
    # app's lifespan, i.e. AFTER uvicorn has created `uvicorn.access` -- so the
    # default silently switched the access log off for the whole process on
    # every boot. Startup lines printed, request lines never did.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
