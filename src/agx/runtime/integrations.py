"""Runtime integrations for middleware and observability."""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Mapping, Optional, Protocol

try:  # pragma: no cover - optional dependency
    import pika
except Exception:  # pragma: no cover - optional dependency
    pika = None

try:  # pragma: no cover - optional dependency
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
except Exception:  # pragma: no cover - optional dependency
    trace = None
    Resource = None
    TracerProvider = None
    BatchSpanProcessor = None
    ConsoleSpanExporter = None

logger = logging.getLogger(__name__)


class EventMiddleware(Protocol):
    """Middleware contract for runtime events."""

    def emit(self, event: Mapping[str, Any]) -> None:
        """Publish a runtime event."""

    def close(self) -> None:
        """Close middleware resources."""


class RabbitMQMiddleware:
    """Publish runtime events to RabbitMQ."""

    def __init__(self, url: str, exchange: str = "agx.events", routing_prefix: str = "agx") -> None:
        if pika is None:  # pragma: no cover - dependency guard
            raise RuntimeError("pika is required for RabbitMQ middleware")
        self.url = url
        self.exchange = exchange
        self.routing_prefix = routing_prefix
        self._connection = None
        self._channel = None
        self._disabled = False

    def emit(self, event: Mapping[str, Any]) -> None:
        if self._disabled:
            return
        channel = self._ensure_channel()
        if channel is None:
            return
        routing_key = f"{self.routing_prefix}.{str(event.get('type', 'event')).replace(' ', '_')}"
        payload = json.dumps(dict(event), default=str).encode("utf-8")
        try:
            channel.basic_publish(
                exchange=self.exchange,
                routing_key=routing_key,
                body=payload,
                properties=pika.BasicProperties(content_type="application/json"),
            )
        except Exception as exc:
            self._disabled = True
            logger.warning("RabbitMQ publish failed: %s", exc)

    def close(self) -> None:
        if self._channel is not None:
            try:
                self._channel.close()
            except Exception:
                pass
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass
        self._channel = None
        self._connection = None

    def _ensure_channel(self):
        if self._channel is not None:
            return self._channel
        if self._disabled:
            return None
        try:
            params = pika.URLParameters(self.url)
            self._connection = pika.BlockingConnection(params)
            self._channel = self._connection.channel()
            self._channel.exchange_declare(
                exchange=self.exchange,
                exchange_type="topic",
                durable=True,
            )
            return self._channel
        except Exception as exc:
            self._disabled = True
            logger.warning("RabbitMQ middleware disabled: %s", exc)
            return None


class Telemetry:
    """OpenTelemetry wrapper with no-op fallback."""

    def __init__(self, service_name: str, enabled: bool) -> None:
        self.enabled = bool(enabled and trace is not None and TracerProvider is not None)
        if not self.enabled:
            self._tracer = None
            return
        provider = trace.get_tracer_provider()
        if provider.__class__.__name__ == "ProxyTracerProvider":
            provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
            if BatchSpanProcessor is not None and ConsoleSpanExporter is not None:
                provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            trace.set_tracer_provider(provider)
        self._tracer = trace.get_tracer("agx.runtime")

    @contextmanager
    def span(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> Iterator[None]:
        if not self.enabled or self._tracer is None:
            with nullcontext():
                yield
            return
        with self._tracer.start_as_current_span(name) as span:
            if attributes:
                for key, value in attributes.items():
                    span.set_attribute(key, value)
            yield

    def event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        if not self.enabled:
            return
        span = trace.get_current_span()
        if span is None:
            return
        span.add_event(name=name, attributes=attributes or {})


@dataclass
class RuntimeIntegrations:
    telemetry: Telemetry
    middlewares: List[EventMiddleware] = field(default_factory=list)

    def emit(self, event: Dict[str, Any]) -> None:
        if "timestamp" not in event:
            event["timestamp"] = datetime.now(timezone.utc).isoformat()
        self.telemetry.event(str(event.get("type", "event")), attributes=dict(event))
        for middleware in self.middlewares:
            try:
                middleware.emit(event)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Middleware emit failed: %s", exc)

    def close(self) -> None:
        for middleware in self.middlewares:
            try:
                middleware.close()
            except Exception:
                pass


def build_runtime_integrations(defaults: Optional[Mapping[str, Any]] = None) -> RuntimeIntegrations:
    defaults = defaults or {}
    middleware_cfg = dict(defaults.get("middleware", {})) if isinstance(defaults.get("middleware"), Mapping) else {}
    observability_cfg = (
        dict(defaults.get("observability", {}))
        if isinstance(defaults.get("observability"), Mapping)
        else {}
    )

    rabbit_enabled = _read_bool(
        middleware_cfg.get("rabbitmq_enabled"),
        os.getenv("AGX_RABBITMQ_ENABLED"),
        default=False,
    )
    rabbit_url = str(middleware_cfg.get("rabbitmq_url") or os.getenv("AGX_RABBITMQ_URL") or "").strip()
    rabbit_exchange = str(middleware_cfg.get("rabbitmq_exchange") or os.getenv("AGX_RABBITMQ_EXCHANGE") or "agx.events")
    rabbit_routing_prefix = str(
        middleware_cfg.get("rabbitmq_routing_prefix")
        or os.getenv("AGX_RABBITMQ_ROUTING_PREFIX")
        or "agx"
    )

    otel_enabled = _read_bool(
        observability_cfg.get("enabled"),
        os.getenv("AGX_OTEL_ENABLED"),
        default=False,
    )
    service_name = str(
        observability_cfg.get("service_name") or os.getenv("AGX_OTEL_SERVICE_NAME") or "agx-framework"
    )

    middlewares: List[EventMiddleware] = []
    if rabbit_enabled and rabbit_url:
        try:
            middlewares.append(
                RabbitMQMiddleware(
                    rabbit_url,
                    exchange=rabbit_exchange,
                    routing_prefix=rabbit_routing_prefix,
                )
            )
        except Exception as exc:
            logger.warning("RabbitMQ middleware unavailable: %s", exc)

    return RuntimeIntegrations(
        telemetry=Telemetry(service_name=service_name, enabled=otel_enabled),
        middlewares=middlewares,
    )


def _read_bool(*values: Any, default: bool) -> bool:
    for value in values:
        if value is None:
            continue
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return default
