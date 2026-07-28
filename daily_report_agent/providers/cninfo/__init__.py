"""CNInfo CN Profile 的纯离线 Provider 契约。"""

from .constants import CNINFO_PROFILE_DESCRIPTOR
from .parser import parse_cninfo_profile_rows
from .profile import CninfoProfileProvider
from .transport import (
    CninfoProfileRow,
    CninfoProfileRows,
    CninfoProfileTransport,
    CninfoTransportBlockedError,
    CninfoTransportRateLimitError,
)

__all__ = [
    "CNINFO_PROFILE_DESCRIPTOR",
    "CninfoProfileProvider",
    "CninfoProfileRow",
    "CninfoProfileRows",
    "CninfoProfileTransport",
    "CninfoTransportBlockedError",
    "CninfoTransportRateLimitError",
    "parse_cninfo_profile_rows",
]
