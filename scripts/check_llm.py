"""Manual smoke check for the provider selected in the local `.env` file."""

import asyncio

from pydantic import BaseModel

from smart_search.llm import LLMError, LLMFactory


class StructuredCheck(BaseModel):
    ok: bool
    message: str


async def main() -> None:
    client = LLMFactory.create()
    try:
        text_result = await client.generate(
            "You are a concise test assistant.",
            "Reply with the single word READY.",
        )
        print(f"Text response ({text_result.provider}/{text_result.model}): {text_result.content}")

        json_result = await client.complete_json(
            "Return a small status object.",
            'Set ok to true and message to "structured output works".',
            StructuredCheck,
        )
        print(f"Structured response: {json_result.model_dump_json()}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except LLMError as exc:
        print(f"LLM smoke check failed: {exc}")
        raise SystemExit(1) from None
