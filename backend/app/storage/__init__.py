"""Storage backend abstraction (Sprint P4).

app/services/asset_service.py depends on app.storage.base.StorageBackend
(the interface) and app.storage.factory.get_storage_backend() (which
picks local.py or s3.py based on settings.storage_backend) - it never
imports app.storage.local or app.storage.s3 directly.
"""
