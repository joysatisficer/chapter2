import inspect
import os
import dataclasses
from typing import TypeVar, Annotated

from functools import wraps
from pydantic import BaseModel, Field, Secret
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    ConsoleSpanExporter,
)
from opentelemetry_instrumentation_discordpy import DiscordPyInstrumentor

tracer = trace.get_tracer("chapter2")
T = TypeVar("T")
REDACTED = "%%REDACTED%%"
# This must be used at the top level of a field
Redact = Annotated[T, "redact", Field(repr=False)]

provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
if "CH2_ENABLE_TELEMETRY" in os.environ:
    provider.add_span_processor(
        SimpleSpanProcessor(OTLPSpanExporter(endpoint="localhost:4317", insecure=True))
    )
trace.set_tracer_provider(provider)


def redacted_attrs(obj):
    names = set()
    for name, typ in getattr(obj, "__annotations__", {}).items():
        if hasattr(typ, "__metadata__") and "redact" in typ:
            names.add(name)
    return names


class InstrumentationSingleton:
    def __call__(self, func):
        @wraps(func)
        def instrument_function(*args, **kwargs):
            bound_args = inspect.signature(func).bind(*args, **kwargs)
            bound_args.apply_defaults()

            with tracer.start_as_current_span(func.__qualname__) as span:

                def add_arg(name, value):
                    if isinstance(value, BaseModel):
                        for k, v in value.model_dump(mode="json").items():
                            add_arg(name + "." + k, v)
                    elif dataclasses.is_dataclass(value):
                        for k, v in dataclasses.asdict(value).items():
                            add_arg(name + "." + k, v)
                    elif isinstance(value, dict):
                        for k, v in value.items():
                            add_arg(name + "." + k, v)
                    elif isinstance(value, list):
                        for k, v in enumerate(value):
                            add_arg(name + "." + str(k), v)
                    elif isinstance(value, Secret):
                        pass
                    else:
                        if param_name in redacted_attrs(func):
                            span.set_attribute(f"arg.{name}", REDACTED)
                        else:
                            span.set_attribute(f"arg.{name}", repr(value))

                for param_name, param_value in bound_args.arguments.items():
                    add_arg(param_name, param_value)
                return func(*args, **kwargs)

        return instrument_function

    def __getattr__(self, prop):
        def instrument_log(**kwargs):
            pass

        return instrument_log


instrument = InstrumentationSingleton()
