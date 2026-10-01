"""Storage metrics and observability.

Provides metrics collection for storage operations using OpenTelemetry.
Tracks file uploads, downloads, storage usage, and operation latencies.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from opentelemetry import metrics

if TYPE_CHECKING:
    from opentelemetry.metrics import Counter, Histogram, UpDownCounter

# Initialize meter
meter = metrics.get_meter("storage", version="1.0.0")

# Counters
file_uploads_total: Counter = meter.create_counter(
    name="storage.file_uploads_total",
    description="Total number of file uploads",
    unit="1",
)

file_downloads_total: Counter = meter.create_counter(
    name="storage.file_downloads_total",
    description="Total number of file downloads",
    unit="1",
)

file_deletes_total: Counter = meter.create_counter(
    name="storage.file_deletes_total",
    description="Total number of file deletions",
    unit="1",
)

storage_errors_total: Counter = meter.create_counter(
    name="storage.errors_total",
    description="Total number of storage operation errors",
    unit="1",
)

# Histograms for latency
upload_duration_seconds: Histogram = meter.create_histogram(
    name="storage.upload_duration_seconds",
    description="Duration of file upload operations",
    unit="s",
)

download_duration_seconds: Histogram = meter.create_histogram(
    name="storage.download_duration_seconds",
    description="Duration of file download operations",
    unit="s",
)

# Gauges for current state
storage_bytes_used: UpDownCounter = meter.create_up_down_counter(
    name="storage.bytes_used",
    description="Current storage usage in bytes",
    unit="By",
)

file_count: UpDownCounter = meter.create_up_down_counter(
    name="storage.file_count",
    description="Current number of files stored",
    unit="1",
)


@dataclass
class StorageMetricsLabels:
    """Common labels for storage metrics."""

    tenant_id: str
    provider: str
    operation: str = ""
    status: str = "success"
    mime_type: str = ""

    def to_dict(self) -> dict[str, str]:
        """Convert to dictionary for metric attributes."""
        return {
            "tenant_id": self.tenant_id,
            "provider": self.provider,
            "operation": self.operation,
            "status": self.status,
            "mime_type": self.mime_type,
        }


class StorageMetrics:
    """Storage metrics helper class.

    Provides convenience methods for recording storage metrics.

    Usage:
        metrics = StorageMetrics()
        metrics.record_upload(tenant_id="123", provider="s3", size_bytes=1024)

        with metrics.measure_upload(tenant_id="123", provider="s3"):
            await upload_file(...)
    """

    @staticmethod
    def record_upload(
        tenant_id: str,
        provider: str,
        size_bytes: int,
        mime_type: str = "",
        success: bool = True,
    ) -> None:
        """Record a file upload metric."""
        labels = StorageMetricsLabels(
            tenant_id=tenant_id,
            provider=provider,
            operation="upload",
            status="success" if success else "error",
            mime_type=mime_type,
        )
        attrs = labels.to_dict()

        file_uploads_total.add(1, attrs)

        if success:
            storage_bytes_used.add(size_bytes, {"tenant_id": tenant_id, "provider": provider})
            file_count.add(1, {"tenant_id": tenant_id, "provider": provider})
        else:
            storage_errors_total.add(1, attrs)

    @staticmethod
    def record_download(
        tenant_id: str,
        provider: str,
        size_bytes: int = 0,
        success: bool = True,
    ) -> None:
        """Record a file download metric."""
        labels = StorageMetricsLabels(
            tenant_id=tenant_id,
            provider=provider,
            operation="download",
            status="success" if success else "error",
        )
        attrs = labels.to_dict()

        file_downloads_total.add(1, attrs)

        if not success:
            storage_errors_total.add(1, attrs)

    @staticmethod
    def record_delete(
        tenant_id: str,
        provider: str,
        size_bytes: int = 0,
        success: bool = True,
    ) -> None:
        """Record a file deletion metric."""
        labels = StorageMetricsLabels(
            tenant_id=tenant_id,
            provider=provider,
            operation="delete",
            status="success" if success else "error",
        )
        attrs = labels.to_dict()

        file_deletes_total.add(1, attrs)

        if success:
            storage_bytes_used.add(-size_bytes, {"tenant_id": tenant_id, "provider": provider})
            file_count.add(-1, {"tenant_id": tenant_id, "provider": provider})
        else:
            storage_errors_total.add(1, attrs)

    @staticmethod
    @contextmanager
    def measure_upload(tenant_id: str, provider: str) -> Generator[None]:
        """Context manager to measure upload duration."""
        start = time.perf_counter()
        try:
            yield
        finally:
            duration = time.perf_counter() - start
            upload_duration_seconds.record(
                duration,
                {"tenant_id": tenant_id, "provider": provider},
            )

    @staticmethod
    @contextmanager
    def measure_download(tenant_id: str, provider: str) -> Generator[None]:
        """Context manager to measure download duration."""
        start = time.perf_counter()
        try:
            yield
        finally:
            duration = time.perf_counter() - start
            download_duration_seconds.record(
                duration,
                {"tenant_id": tenant_id, "provider": provider},
            )


# Singleton instance
storage_metrics = StorageMetrics()
