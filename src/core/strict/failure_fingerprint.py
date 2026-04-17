"""Strict-mode compatibility wrapper for `core.failure_fingerprint`."""

import sys as _sys
from importlib import import_module as _import_module

_sys.modules[__name__] = _import_module("core.failure_fingerprint")
