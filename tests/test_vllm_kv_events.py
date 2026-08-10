import threading
from pathlib import Path

import msgspec
import zmq

from engine_events.vllm import SequenceGap, VLLMEventAdapter, VLLMSubscriber


def _batch(timestamp: float, events: list[list[object]]) -> bytes:
    return msgspec.msgpack.encode([timestamp, events, 0])


def test_vllm_adapter_decodes_store_remove_and_clear(tmp_path: Path) -> None:
    adapter = VLLMEventAdapter("gpu-0", "gen-1", tmp_path / "checkpoint.json")
    stored = adapter.process(
        0,
        _batch(1.0, [["BlockStored", [123, b"abc"], None, [1, 2], 16, None, "GPU"]]),
    )
    assert [event["operation"] for event in stored] == ["store", "store"]
    assert stored[0]["cache_key"] == "vllm:block:123"
    assert stored[1]["cache_key"] == "vllm:block:616263"
    assert all(event["has_sequence"] and event["generation"] == "gen-1" for event in stored)

    removed = adapter.process(1, _batch(2.0, [["BlockRemoved", [123], "GPU"]]))
    assert removed[0]["operation"] == "remove"
    cleared = adapter.process(2, _batch(3.0, [["AllBlocksCleared"]]))
    assert cleared[0]["operation"] == "clear"


def test_vllm_adapter_detects_gap_and_deduplicates_restart(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    adapter = VLLMEventAdapter("gpu-0", "gen-1", checkpoint)
    adapter.process(0, _batch(1.0, []))
    try:
        adapter.process(2, _batch(2.0, []))
    except SequenceGap as error:
        assert error.expected == 1 and error.received == 2
    else:
        raise AssertionError("expected sequence gap")
    adapter.process(1, _batch(1.5, []))
    adapter.process(2, _batch(2.0, []))
    restarted = VLLMEventAdapter("gpu-0", "gen-1", checkpoint)
    assert restarted.process(2, _batch(2.0, [])) == []


def test_vllm_adapter_resets_on_backend_sequence_restart(tmp_path: Path) -> None:
    adapter = VLLMEventAdapter("gpu-0", "gen-1", tmp_path / "checkpoint.json")
    adapter.process(0, _batch(1.0, []))
    adapter.process(1, _batch(2.0, []))
    events = adapter.process(0, _batch(10.0, [["BlockStored", [42], None, [1], 1, None, "GPU"]]))
    assert events[0]["operation"] == "clear"
    assert events[1]["operation"] == "store"
    assert events[1]["generation"].startswith("auto-")


def test_replay_uses_streaming_dealer_protocol(tmp_path: Path) -> None:
    context = zmq.Context.instance()
    endpoint = "inproc://kavora-vllm-replay-test"
    payload = _batch(12.5, [["BlockStored", [123], None, None, 16]])
    ready = threading.Event()

    def publish_replay() -> None:
        socket = context.socket(zmq.ROUTER)
        socket.bind(endpoint)
        ready.set()
        request = socket.recv_multipart()
        assert len(request) == 3
        client_id, delimiter, start_sequence = request
        assert delimiter == b""
        assert int.from_bytes(start_sequence, "big") == 0
        socket.send_multipart([client_id, b"", b"", (0).to_bytes(8, "big"), payload])
        socket.send_multipart([client_id, b"", b"", (-1).to_bytes(8, "big", signed=True), b""])
        socket.close(linger=0)

    thread = threading.Thread(target=publish_replay)
    thread.start()
    assert ready.wait(timeout=1)
    adapter = VLLMEventAdapter("gpu-0", "generation-a", tmp_path / "checkpoint.json")
    subscriber = VLLMSubscriber(
        adapter,
        endpoint="inproc://unused-publisher",
        replay_endpoint=endpoint,
        gateway_url="http://127.0.0.1:1",
        admin_token="",
    )
    delivered: list[dict[str, object]] = []
    subscriber._post = delivered.append  # type: ignore[method-assign]
    try:
        subscriber._replay(0)
    finally:
        subscriber.client.close()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert adapter.last_sequence == 0
    assert delivered[0]["cache_key"] == "vllm:block:123"
