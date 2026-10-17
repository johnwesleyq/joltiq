from .base import utcnow  # noqa: F401
from .enums import *  # noqa: F401,F403
from .identity import *  # noqa: F401,F403
from .operations import *  # noqa: F401,F403
from .platform import *  # noqa: F401,F403
from .support import *  # noqa: F401,F403


def tenant_tables() -> list[str]:
    from ..db import Base

    return sorted(
        m.class_.__tablename__ for m in Base.registry.mappers if getattr(m.class_, "__tenant__", False)
    )
