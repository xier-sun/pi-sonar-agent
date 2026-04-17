"""Bridge module for legacy `core.memory.issue_compaction` imports."""

from importlib import import_module as _import_module
import sys as _sys


_sys.modules[__name__] = _import_module("core.memory.issue_compaction")
