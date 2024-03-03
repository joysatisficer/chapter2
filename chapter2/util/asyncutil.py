import asyncio
from typing import Iterable, AsyncIterable, Callable, AsyncGenerator


def eager_iterable_to_async_iterable(iterable: Iterable) -> AsyncIterable:
    class AsyncIterableWrapper:
        async def __aiter__(self):
            for item in iterable:
                yield item

    return AsyncIterableWrapper()


def async_generator_to_reusable_async_iterable(
    iterable: Callable[[], AsyncGenerator]
) -> AsyncIterable:
    class AsyncIterableWrapper:
        def __aiter__(self):
            return iterable()

    return AsyncIterableWrapper()


_task_refs = set()


def run_task(coro):
    task = asyncio.create_task(coro)
    task.add_done_callback(lambda: _task_refs.remove(task))
