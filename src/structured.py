"""Request a typed model from the integrated client or the standalone CLI client."""

import json


def request_json(prompt, payload, schema, *, complete_json, llm):
    user_prompt = json.dumps(payload, ensure_ascii=False)
    if complete_json is not None:
        return complete_json(prompt, user_prompt, schema)
    response = llm.complete(
        [{"role": "system", "content": prompt}, {"role": "user", "content": user_prompt}],
        response_format={"type": "json_object"},
    )
    return schema.model_validate_json(response["choices"][0]["message"]["content"])
