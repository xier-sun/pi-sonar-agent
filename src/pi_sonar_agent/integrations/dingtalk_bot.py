"""Bridge module for legacy `integrations.dingtalk_bot`."""

import sys as _sys
from importlib import import_module as _import_module

_sys.modules[__name__] = _import_module("integrations.dingtalk_bot")
