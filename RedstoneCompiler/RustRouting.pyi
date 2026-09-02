"""Editor-facing fallback declarations for the compiled PyO3 routing extension.

The implementation is RustRouting.cpython-*-*.so.  PyO3 does not emit Python
type information, so this stub tells static analyzers that exported bindings
are provided dynamically by the native module without imposing inaccurate
signatures on the routing API.
"""

from typing import Any


def __getattr__(Name: str) -> Any: ...
