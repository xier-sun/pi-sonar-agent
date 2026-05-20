"""Bridge module for legacy `dingtalk_worker`."""

from importlib import import_module as _import_module

_legacy_module = _import_module("dingtalk_worker")
_public_names = getattr(
    _legacy_module,
    "__all__",
    tuple(name for name in dir(_legacy_module) if not name.startswith("_")),
)

globals().update({name: getattr(_legacy_module, name) for name in _public_names})
__all__ = tuple(_public_names)


if __name__ == "__main__":
    raise SystemExit(_legacy_module.main())
