"""Bridge module for legacy `agent.claude_agent`."""

import sys as _sys
from importlib import import_module as _import_module

_sys.modules[__name__] = _import_module('agent.claude_agent')
