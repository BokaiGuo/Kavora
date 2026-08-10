import pytest

from engine_events.vllm_hash import VLLMBlockHasher, _validate_supported_request


def test_vllm_block_hasher_matches_vllm_sha256_cbor_chain(monkeypatch) -> None:
    pytest.importorskip("vllm")
    monkeypatch.setenv("PYTHONHASHSEED", "7")
    hasher = VLLMBlockHasher(block_size=4, hash_algo="sha256_cbor", python_hash_seed="7")

    result = hasher.hash_tokens([1, 2, 3, 4, 5, 6, 7, 8, 9])

    assert result.token_count == 9
    assert result.full_blocks == 2
    assert result.cache_keys == ["vllm:block:12193498987697778473", "vllm:block:7578280077416998201"]


def test_vllm_block_hasher_rejects_seed_mismatch(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONHASHSEED", "11")
    try:
        VLLMBlockHasher(block_size=4, hash_algo="sha256_cbor", python_hash_seed="7")
    except ValueError as error:
        assert "PYTHONHASHSEED" in str(error)
    else:
        raise AssertionError("expected seed mismatch to be rejected")


@pytest.mark.parametrize("field", ["cache_salt", "prompt_embeds", "lora_request"])
def test_exact_hash_alignment_rejects_extra_hash_inputs(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        _validate_supported_request({field: "configured"})


def test_exact_hash_alignment_rejects_multimodal_content() -> None:
    with pytest.raises(ValueError, match="multimodal"):
        _validate_supported_request(
            {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]}
        )
