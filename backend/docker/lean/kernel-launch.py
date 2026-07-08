"""Single-process kernel launcher: migrate + serve in ONE interpreter.

The run script used to spawn ``python -m alembic upgrade head`` and THEN
``python -m uvicorn`` — two interpreters, each paying the sqlalchemy/alembic
import bill (~0.5-1s measured). Running the migration through the alembic API
and then ``uvicorn.run()`` in the same process imports everything once, cutting
cold-boot latency. Invoked by the s6 valuz-kernel run script (non-root).
"""

import os

os.chdir("/app")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

command.upgrade(Config("alembic/kernel/alembic.ini"), "head")

import uvicorn  # noqa: E402

uvicorn.run(
    "app.main:app",
    host="0.0.0.0",
    port=int(os.environ.get("KERNEL_PORT", "8000")),
    log_level=os.environ.get("LOG_LEVEL", "info"),
)
