"""Constants for the Zorar integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "zorar"

CONF_API_TOKEN: Final = "api_token"
CONF_BASE_URL: Final = "base_url"

DEFAULT_BASE_URL: Final = "https://zorarapp.com/api/v1"

SCAN_INTERVAL: Final = timedelta(seconds=30)

# The server queues outlet commands; the strip applies them asynchronously.
REFRESH_DELAY_AFTER_COMMAND: Final = 3

# How long a commanded state is shown while polls still report the old one
# (a poll already in flight, or the strip not having applied it yet).
OPTIMISTIC_TIMEOUT: Final = 15

OUTLET_CHANNELS: Final = (1, 2, 3, 4)

MANUFACTURER: Final = "Zorar"
MODEL: Final = "MTTL-W01"
