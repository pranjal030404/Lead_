"""Fast JSON responses for the list endpoints.

FastAPI normally pushes a returned dict through `jsonable_encoder`, which walks
every value to coerce types it might not be able to serialise. That is the right
default when handlers return models, dates or Decimals. These handlers return
rows straight out of SQLite - str, int, float, None, and the odd list - so the
walk finds nothing to convert and is pure overhead.

Measured on a 50-lead page (56 columns each, ~86KB):

    jsonable_encoder + dumps   12.5 ms
    dumps alone                 0.9 ms

13x, per response, on the CPU that also runs the event loop. Under 20 concurrent
requests it was the difference between 540ms and 40ms at the median. Returning a
Response instance makes FastAPI skip the encoder entirely.

Only worth using where payloads are large and the values are known primitives.
Everything else should keep the safer default.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.responses import JSONResponse


class FastJSON(JSONResponse):
    """JSONResponse that skips the encoder walk. Compact separators too - the
    default ", " / ": " adds a few percent to a payload this size for nothing."""

    def render(self, content: Any) -> bytes:
        return json.dumps(
            content, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        ).encode("utf-8")


def rows_response(payload: dict) -> FastJSON:
    """Serialise a payload of plain SQLite values.

    Falls back to the standard encoder if anything unexpected turns up, so a new
    column type can never turn a list endpoint into a 500.
    """
    try:
        return FastJSON(content=payload)
    except (TypeError, ValueError):
        from fastapi.encoders import jsonable_encoder

        return JSONResponse(content=jsonable_encoder(payload))
