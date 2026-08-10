from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException


@dataclass(frozen=True)
class HashResult:
    cache_keys: list[str]
    token_count: int
    full_blocks: int


class VLLMBlockHasher:
    def __init__(self, *, block_size: int, hash_algo: str, python_hash_seed: str) -> None:
        if block_size <= 0:
            raise ValueError("block_size must be positive")
        if not python_hash_seed:
            raise ValueError("python_hash_seed is required")
        configured_seed = os.environ.get("PYTHONHASHSEED")
        if configured_seed not in (None, python_hash_seed):
            raise ValueError("PYTHONHASHSEED must match python_hash_seed")
        os.environ["PYTHONHASHSEED"] = python_hash_seed
        try:
            try:
                from vllm.utils import get_hash_fn_by_name
            except ImportError:
                from vllm.utils.hashing import get_hash_fn_by_name
            from vllm.v1.core import kv_cache_utils
        except ImportError as error:
            raise RuntimeError("vLLM is required for request hash alignment") from error
        self.block_size = block_size
        self.hash_function = get_hash_fn_by_name(hash_algo)
        self.kv_cache_utils = kv_cache_utils
        self.kv_cache_utils.init_none_hash(self.hash_function)

    def hash_tokens(self, token_ids: list[int]) -> HashResult:
        parent = None
        cache_keys: list[str] = []
        full_blocks = len(token_ids) // self.block_size
        for block_index in range(full_blocks):
            start = block_index * self.block_size
            block = token_ids[start : start + self.block_size]
            parent = self.kv_cache_utils.hash_block_tokens(self.hash_function, parent, block, None)
            external = self.kv_cache_utils.maybe_convert_block_hash(parent)
            if isinstance(external, bytes):
                encoded = external.hex()
            else:
                encoded = str(external)
            cache_keys.append("vllm:block:" + encoded)
        return HashResult(cache_keys=cache_keys, token_count=len(token_ids), full_blocks=full_blocks)


class TokenizeHashService:
    def __init__(self, tokenize_url: str, hasher: VLLMBlockHasher, timeout_seconds: float = 5.0) -> None:
        self.tokenize_url = tokenize_url.rstrip("/") + "/tokenize"
        self.hasher = hasher
        self.client = httpx.AsyncClient(timeout=timeout_seconds)

    async def resolve(self, request: dict[str, Any]) -> HashResult:
        _validate_supported_request(request)
        payload = {key: request[key] for key in ("model", "messages", "tools") if key in request}
        response = await self.client.post(self.tokenize_url, json=payload)
        response.raise_for_status()
        body = response.json()
        tokens = body.get("tokens")
        if not isinstance(tokens, list) or any(not isinstance(token, int) or token < 0 for token in tokens):
            raise ValueError("vLLM /tokenize returned invalid token IDs")
        return self.hasher.hash_tokens(tokens)


def _validate_supported_request(request: dict[str, Any]) -> None:
    for unsupported in ("cache_salt", "prompt_embeds", "lora_request"):
        if request.get(unsupported) is not None:
            raise ValueError(f"{unsupported} is not supported by exact hash alignment")
    for message in request.get("messages", []):
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list) and any(not isinstance(part, dict) or part.get("type") != "text" for part in content):
            raise ValueError("multimodal message content is not supported by exact hash alignment")


def create_app(service: TokenizeHashService) -> FastAPI:
    app = FastAPI(title="Kavora vLLM Hash Alignment")

    @app.post("/v1/cache-keys")
    async def cache_keys(request: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await service.resolve(request)
        except (httpx.HTTPError, ValueError, RuntimeError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {
            "cache_keys": result.cache_keys,
            "token_count": result.token_count,
            "full_blocks": result.full_blocks,
        }

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve OpenAI requests to vLLM-compatible external block hashes")
    parser.add_argument("--tokenize-url", required=True)
    parser.add_argument("--block-size", type=int, default=16)
    parser.add_argument("--hash-algo", default="sha256_cbor")
    parser.add_argument("--python-hash-seed", default=os.environ.get("PYTHONHASHSEED", "7"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19120)
    args = parser.parse_args()
    hasher = VLLMBlockHasher(block_size=args.block_size, hash_algo=args.hash_algo, python_hash_seed=args.python_hash_seed)
    uvicorn.run(create_app(TokenizeHashService(args.tokenize_url, hasher)), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
