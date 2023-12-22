from typing import Iterable, AsyncIterable


def eager_iterable_to_async_iterable(iterable: Iterable) -> AsyncIterable:
    class AsyncIterableWrapper:
        async def __aiter__(self):
            for item in iterable:
                yield item

    return AsyncIterableWrapper()
