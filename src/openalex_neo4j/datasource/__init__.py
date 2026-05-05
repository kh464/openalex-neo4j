"""Data source adapters for enriching data from multiple sources."""
from .base import DataSource, DataRecord, merge_record

_datasource_registry: dict[str, type[DataSource]] = {}


def register_datasource(cls: type[DataSource]) -> type[DataSource]:
    """Decorator: register a DataSource class."""
    instance = cls()  # instantiate to get .name
    _datasource_registry[instance.name] = cls
    return cls


def get_datasource(name: str, **config) -> DataSource:
    """Get a DataSource instance by name."""
    if name not in _datasource_registry:
        raise KeyError(f"Unknown datasource: '{name}'. Available: {list(_datasource_registry.keys())}")
    return _datasource_registry[name](**config)


def list_datasources() -> list[str]:
    """List all registered datasource names."""
    return list(_datasource_registry.keys())
