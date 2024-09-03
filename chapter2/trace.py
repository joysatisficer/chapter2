import inspect
import os
import dataclasses
import functools
from typing import TypeVar, Annotated

from pydantic import BaseModel, Field, Secret
from opentelemetry import trace as ot_trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry_instrumentation_discordpy import DiscordPyInstrumentor


__all__ = ["trace", "TraceSingleton", "ot_tracer"]
ot_tracer = ot_trace.get_tracer("chapter2")
T = TypeVar("T")

provider = TracerProvider()
if "CH2_ENABLE_TELEMETRY" in os.environ:
    provider.add_span_processor(
        SimpleSpanProcessor(OTLPSpanExporter(endpoint="localhost:4317", insecure=True))
    )
ot_trace.set_tracer_provider(provider)


def dehydrate(name: str, value) -> dict:
    if isinstance(value, BaseModel):
        iterator = value.model_dump(mode="json").items()
    elif dataclasses.is_dataclass(value):
        iterator = dataclasses.asdict(value).items()
    elif isinstance(value, dict):
        iterator = value.items()
    elif isinstance(value, list):
        iterator = [(str(k), v) for k, v in enumerate(value)]
    else:
        if isinstance(value, Secret):
            return {}
        else:
            return {name: repr(value)}
    result = {}
    for k, v in iterator:
        result.update(dehydrate(name + "." + k, v))
    return result


class TraceGenerator:
    def __init__(self, gen, links):
        self.gen = gen
        self.links = links

    def __iter__(self):
        return self

    def __aiter__(self):
        return self

    def __next__(self):
        with ot_tracer.start_as_current_span(
            self.gen.__qualname__, links=self.links
        ) as span:
            try:
                ret = next(self.gen)
            except StopIteration:
                span.set_attribute("halt", True)
                raise
            else:
                span.set_attributes(dehydrate("yield", ret))
                return ret

    async def __anext__(self):
        e_func = None
        with ot_tracer.start_as_current_span(
            self.gen.__qualname__, links=self.links
        ) as span:
            span: ot_trace.Span
            try:
                ret = await anext(self.gen)
            except StopAsyncIteration as e:
                e_func = e
                span.set_attribute("halt", True)
            else:
                span.set_attributes(dehydrate("yield", ret))
                return ret
        if e_func is not None:
            raise e_func


class TraceSingleton:
    def __call__(self, func):
        @functools.wraps(func)
        def trace_function(*args, **kwargs):
            bound_args = inspect.signature(func).bind(*args, **kwargs)
            bound_args.apply_defaults()
            attributes = dehydrate("arg", bound_args.arguments)
            if inspect.isgeneratorfunction(func) or inspect.isasyncgenfunction(func):
                with ot_tracer.start_as_current_span(func.__qualname__) as span:
                    span.set_attributes(attributes)
                    links = [ot_trace.Link(span.get_span_context())]
                    return TraceGenerator(func(*args, **kwargs), links)
            else:
                with ot_tracer.start_as_current_span(func.__qualname__) as span:
                    span.set_attributes(attributes)
                    ret = func(*args, **kwargs)
                    span.set_attributes(dehydrate("return", ret))
                    return ret

        return trace_function

    def __getattr__(self, name):
        def instrument_log(*args, attr=False, **kwargs):
            span = ot_trace.get_current_span()
            if attr:
                assert len(args) == 1
                span.set_attribute(name, args[0])
            else:
                span.add_event(
                    name, {**dehydrate("value", args), **dehydrate("value", kwargs)}
                )
                if len(args) == 1 and len(kwargs) == 0:
                    return args[0]
                elif len(args) > 1 and len(kwargs) == 0:
                    return args
                else:
                    return None

        return instrument_log


trace = TraceSingleton()
