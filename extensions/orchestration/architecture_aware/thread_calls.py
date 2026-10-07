"""Keep synchronous graph operations inside their caller's lifetime."""

import asyncio


async def graph_call(function, *args, **kwargs):
    """Drain an already-started worker before propagating cancellation.

    Cancelling to_thread does not stop its thread. Let the provider's finally
    blocks release SQLite handles before the caller can remove its workspace.
    A deadline therefore stops further work, but may wait for this call to end.
    """
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        # Retrieve any worker exception without replacing cancellation.
        if not worker.cancelled():
            worker.exception()
        raise
