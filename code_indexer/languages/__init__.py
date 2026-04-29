"""Language adapters for different programming languages."""

from code_indexer.languages.python import PythonAdapter

_ADAPTERS = {
    "python": PythonAdapter,
}

_EXTENSION_MAP = {
    ".py": "python",
    ".pyw": "python",
}


def get_adapter(language: str):
    """Get a language adapter by name."""
    if language not in _ADAPTERS:
        raise ValueError(f"Unknown language: {language}")
    adapter_class = _ADAPTERS[language]
    return adapter_class()


def language_for_file(path):
    """Infer language from file extension."""
    from pathlib import Path
    p = Path(path) if not isinstance(path, Path) else path
    ext = p.suffix.lower()
    if ext not in _EXTENSION_MAP:
        raise ValueError(f"Unknown file type: {ext}")
    return _EXTENSION_MAP[ext]


def supported_extensions():
    """List all supported file extensions."""
    return list(_EXTENSION_MAP.keys())
