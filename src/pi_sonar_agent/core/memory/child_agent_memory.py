"""Bridge module for legacy `core.memory.child_agent_memory` imports."""

import sys as _sys
from importlib import import_module as _import_module

_sys.modules[__name__] = _import_module("core.memory.child_agent_memory")
