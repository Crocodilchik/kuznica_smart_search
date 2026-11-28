"""Synchronous boundary between graph nodes and module 2's async model client."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from smart_search.llm import LLMFactory


def complete_json[T: BaseModel](system_prompt: str, user_prompt: str, response_model: type[T]) -> T:
    async def request() -> T:
        client = LLMFactory.create()
        try:
            return await client.complete_json(system_prompt, user_prompt, response_model)
        finally:
            await client.aclose()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(request())
    # A synchronous caller embedded in a running event loop needs an independent loop.
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(request())).result()
