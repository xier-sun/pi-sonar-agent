"""Bridge module for legacy `sonar_mcp.tools`."""

import sys as _sys
from importlib import import_module as _import_module

_sys.modules[__name__] = _import_module('sonar_mcp.tools')
