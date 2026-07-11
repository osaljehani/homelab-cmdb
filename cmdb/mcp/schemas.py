"""Re-export shim: the models moved to cmdb.domain.schemas so the web API can
share them. Import from cmdb.domain.schemas in new code."""

from cmdb.domain.schemas import *  # noqa: F401,F403
