"""Small SSE data-event reader following WHATWG event stream framing.

HTTP chunks are not events and events are not tokens. Token counts are taken
from the server's delta token_ids field by the caller.
"""

from collections.abc import AsyncIterator


async def data_events(content) -> AsyncIterator[str]:
    data = []
    first_line = True
    async for raw_line in content:
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if first_line:
            line = line.removeprefix("\ufeff")
            first_line = False
        if not line:
            if data:
                yield "\n".join(data)
                data.clear()
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if field == "data":
            data.append(value.removeprefix(" ") if separator else "")
    # An event must end with a blank line. Discard an incomplete EOF frame.
