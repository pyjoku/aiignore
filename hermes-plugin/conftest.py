"""Pytest config — keep collection scoped to tests/ only.

The plugin's ``__init__.py`` uses a relative import (``from .matcher import …``)
which is valid when Hermes loads the folder as a package but breaks pytest's
default collection. Skip it.
"""

collect_ignore = ["__init__.py"]
