"""Read-only access to sealed pre-cutover records."""

from .migration import legacy_inventory, legacy_records

__all__ = ["legacy_inventory", "legacy_records"]
