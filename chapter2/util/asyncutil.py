import asyncio
from typing import Iterable, AsyncIterable, Callable, AsyncGenerator


def eager_iterable_to_async_iterable(iterable: Iterable) -> AsyncIterable:
    class AsyncIterableWrapper:
        async def __aiter__(self):
            for item in iterable:
                yield item

        def __repr__(self):
            return repr(iterable)

    return AsyncIterableWrapper()


def async_generator_to_reusable_async_iterable(
    iterable: Callable[[], AsyncGenerator], *args, **kwargs
) -> AsyncIterable:
    class AsyncIterableWrapper:
        def __aiter__(self):
            return iterable(*args, **kwargs)

    return AsyncIterableWrapper()


_running_tasks = set()


def run_task(coro):
    task = asyncio.create_task(coro)
    _running_tasks.add(task)
    task.add_done_callback(lambda task: _running_tasks.remove(task))
