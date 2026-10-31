"""Conservative local input budget; actual provider usage is measured separately."""

import os
from functools import lru_cache
from pathlib import Path

import tiktoken


@lru_cache(maxsize=1)
def encoding():
    os.environ.setdefault(
        "TIKTOKEN_CACHE_DIR", str(Path(__file__).resolve().parents[1] / "tokenizer_cache")
    )
    return tiktoken.get_encoding("cl100k_base")


def input_token_count(payload):
    # Include instructions, tool schemas and context, with a framing allowance.
    # This is a local cl100k budget, not a guarantee of model-specific billed tokens.
    return len(encoding().encode(payload, disallowed_special=())) + 128
