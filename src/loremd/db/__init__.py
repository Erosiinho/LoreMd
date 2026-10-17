"""Index Postgres : pool de connexions, migrations, dépôts de données."""

from .engine import Database

__all__ = ["Database"]
