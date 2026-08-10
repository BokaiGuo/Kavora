from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import msgspec
import zmq


class SequenceGap(RuntimeError):
    def __init__(self, expected: int, received: int) -> None:
        super().__init__(f"vLLM KV event gap: expected {expected}, received {received}")
        self.expected = expected
        self.received = received


@dataclass(frozen=True)
class PreparedBatch:
    sequence: int
    timestamp: float
    generation: str
    events: list[dict[str, Any]]
    duplicate: bool = False


class VLLMEventAdapter:
    def __init__(self, backend_id: str, generation: str, checkpoint_path: str | Path) -> None:
        if not backend_id or not generation:
            raise ValueError("backend_id and generation are required")
        self.backend_id = backend_id
        self.configured_generation = generation
        self.generation = generation
        self.checkpoint_path = Path(checkpoint_path)
        self.last_sequence: int | None = None
        self.last_timestamp = 0.0
        self._load_checkpoint()

    def _load_checkpoint(self) -> None:
        if not self.checkpoint_path.exists():
            return
        value = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        if value.get("backend_id") != self.backend_id or value.get("configured_generation", value.get("generation")) != self.configured_generation:
            return
        self.generation = str(value.get("generation", self.configured_generation))
        self.last_sequence = int(value["last_sequence"])
        self.last_timestamp = float(value.get("last_timestamp", 0))

    def _save_checkpoint(self) -> None:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.checkpoint_path.with_suffix(self.checkpoint_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "backend_id": self.backend_id,
                    "configured_generation": self.configured_generation,
                    "generation": self.generation,
                    "last_sequence": self.last_sequence,
                    "last_timestamp": self.last_timestamp,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.checkpoint_path)

    def prepare(self, sequence: int, payload: bytes) -> PreparedBatch:
        batch = msgspec.msgpack.decode(payload)
        if not isinstance(batch, list) or len(batch) < 2 or not isinstance(batch[1], list):
            raise ValueError("invalid vLLM KV EventBatch payload")
        timestamp = float(batch[0])
        if not 0 <= sequence < 1 << 64 or not timestamp >= 0:
            raise ValueError("invalid vLLM sequence or timestamp")
        generation = self.generation
        restart = self.last_sequence is not None and sequence == 0 and timestamp > self.last_timestamp
        if restart:
            generation = f"auto-{int(timestamp * 1000)}"
        elif self.last_sequence is not None:
            if sequence <= self.last_sequence:
                return PreparedBatch(sequence, timestamp, generation, [], duplicate=True)
            if sequence != self.last_sequence + 1:
                raise SequenceGap(self.last_sequence + 1, sequence)
        events: list[dict[str, Any]] = []
        if restart:
            events.append(self._event("clear", sequence, generation, 0, timestamp))
        for event_index, raw_event in enumerate(batch[1], start=1):
            if not isinstance(raw_event, list) or not raw_event:
                raise ValueError("invalid vLLM KV event")
            event_type = raw_event[0]
            if event_type == "BlockStored":
                if len(raw_event) < 5 or not isinstance(raw_event[1], list):
                    raise ValueError("invalid BlockStored event")
                block_size = int(raw_event[4])
                if block_size <= 0:
                    raise ValueError("BlockStored block_size must be positive")
                for block_index, block_hash in enumerate(raw_event[1]):
                    events.append(
                        self._event(
                            "store",
                            sequence,
                            generation,
                            event_index * 1_000_000 + block_index,
                            timestamp,
                            cache_key=_cache_key(block_hash),
                            matched_tokens=block_size,
                            total_tokens=block_size,
                        )
                    )
            elif event_type == "BlockRemoved":
                if len(raw_event) < 2 or not isinstance(raw_event[1], list):
                    raise ValueError("invalid BlockRemoved event")
                for block_index, block_hash in enumerate(raw_event[1]):
                    events.append(
                        self._event(
                            "remove",
                            sequence,
                            generation,
                            event_index * 1_000_000 + block_index,
                            timestamp,
                            cache_key=_cache_key(block_hash),
                        )
                    )
            elif event_type == "AllBlocksCleared":
                events.append(self._event("clear", sequence, generation, event_index * 1_000_000, timestamp))
            else:
                raise ValueError(f"unsupported vLLM KV event type {event_type!r}")
        return PreparedBatch(sequence, timestamp, generation, events)

    def _event(
        self,
        operation: str,
        sequence: int,
        generation: str,
        event_index: int,
        timestamp: float,
        *,
        cache_key: str = "",
        matched_tokens: int = 0,
        total_tokens: int = 0,
    ) -> dict[str, Any]:
        return {
            "operation": operation,
            "backend_id": self.backend_id,
            "cache_key": cache_key,
            "matched_tokens": matched_tokens,
            "total_tokens": total_tokens,
            "sequence": sequence,
            "has_sequence": True,
            "generation": generation,
            "engine_event_id": f"{generation}:{sequence}:{event_index}",
            "observed_at": _rfc3339(timestamp),
            "quality": "fresh",
        }

    def commit(self, prepared: PreparedBatch) -> None:
        if prepared.duplicate:
            return
        self.generation = prepared.generation
        self.last_sequence = prepared.sequence
        self.last_timestamp = prepared.timestamp
        self._save_checkpoint()

    def process(self, sequence: int, payload: bytes) -> list[dict[str, Any]]:
        prepared = self.prepare(sequence, payload)
        self.commit(prepared)
        return prepared.events


def _cache_key(value: Any) -> str:
    if isinstance(value, bytes):
        encoded = value.hex()
    elif isinstance(value, int) and value >= 0:
        encoded = str(value)
    else:
        raise ValueError("vLLM block hash must be bytes or a non-negative integer")
    return "vllm:block:" + encoded


def _rfc3339(timestamp: float) -> str:
    seconds = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(timestamp))
    milliseconds = int((timestamp - int(timestamp)) * 1000)
    return f"{seconds}.{milliseconds:03d}Z"


class VLLMSubscriber:
    def __init__(
        self,
        adapter: VLLMEventAdapter,
        *,
        endpoint: str,
        replay_endpoint: str | None,
        gateway_url: str,
        admin_token: str,
        topic: str = "",
        timeout_seconds: float = 5.0,
    ) -> None:
        self.adapter = adapter
        self.endpoint = endpoint
        self.replay_endpoint = replay_endpoint
        self.gateway_url = gateway_url.rstrip("/")
        self.topic = topic.encode("utf-8")
        headers = {"Authorization": f"Bearer {admin_token}"} if admin_token else {}
        self.client = httpx.Client(headers=headers, timeout=timeout_seconds)
        self.context = zmq.Context.instance()

    def _post(self, event: dict[str, Any]) -> None:
        response = self.client.post(self.gateway_url + "/v1/admin/cache-events", json=event)
        response.raise_for_status()

    def _deliver(self, sequence: int, payload: bytes) -> None:
        prepared = self.adapter.prepare(sequence, payload)
        for event in prepared.events:
            self._post(event)
        self.adapter.commit(prepared)

    def _replay(self, start_sequence: int) -> None:
        if not self.replay_endpoint:
            raise RuntimeError("sequence gap detected but replay_endpoint is not configured")
        socket = self.context.socket(zmq.DEALER)
        socket.setsockopt(zmq.RCVTIMEO, 5000)
        socket.setsockopt(zmq.SNDTIMEO, 5000)
        socket.connect(self.replay_endpoint)
        try:
            socket.send_multipart([b"", start_sequence.to_bytes(8, "big")])
            first_sequence: int | None = None
            while True:
                frames = socket.recv_multipart()
                if len(frames) != 4 or frames[0] != b"" or len(frames[2]) != 8:
                    raise RuntimeError("invalid vLLM replay response")
                sequence = int.from_bytes(frames[2], "big", signed=True)
                if sequence == -1:
                    break
                if frames[1] != self.topic:
                    raise RuntimeError("vLLM replay topic does not match subscription topic")
                if first_sequence is None:
                    first_sequence = sequence
                    if sequence > start_sequence:
                        raise RuntimeError(
                            f"vLLM replay buffer starts at {sequence}, before requested sequence {start_sequence} could be recovered"
                        )
                self._deliver(sequence, frames[3])
        finally:
            socket.close(linger=0)

    def run(self) -> None:
        socket = self.context.socket(zmq.SUB)
        socket.connect(self.endpoint)
        socket.setsockopt(zmq.SUBSCRIBE, self.topic)
        try:
            if not self.replay_endpoint:
                raise RuntimeError("replay_endpoint is required for strict native KV evidence")
            self._replay(0 if self.adapter.last_sequence is None else self.adapter.last_sequence + 1)
            while True:
                frames = socket.recv_multipart()
                if len(frames) != 3 or frames[0] != self.topic or len(frames[1]) != 8:
                    continue
                sequence = int.from_bytes(frames[1], "big")
                try:
                    self._deliver(sequence, frames[2])
                except SequenceGap as gap:
                    self._replay(gap.expected)
                    self._deliver(sequence, frames[2])
        finally:
            socket.close(linger=0)
            self.client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Subscribe to native vLLM KV events and feed Kavora exact cache evidence")
    parser.add_argument("--backend-id", required=True)
    parser.add_argument("--generation", required=True, help="backend generation or pod/start identifier")
    parser.add_argument("--endpoint", required=True, help="vLLM PUB endpoint, for example tcp://127.0.0.1:5557")
    parser.add_argument("--replay-endpoint", required=True)
    parser.add_argument("--gateway-url", default="http://127.0.0.1:18000")
    parser.add_argument("--admin-token", default=os.environ.get("KAVORA_ADMIN_TOKEN", ""))
    parser.add_argument("--topic", default="")
    parser.add_argument("--checkpoint", default="results/state/vllm-kv-events.checkpoint.json")
    args = parser.parse_args()
    subscriber = VLLMSubscriber(
        VLLMEventAdapter(args.backend_id, args.generation, args.checkpoint),
        endpoint=args.endpoint,
        replay_endpoint=args.replay_endpoint or None,
        gateway_url=args.gateway_url,
        admin_token=args.admin_token,
        topic=args.topic,
    )
    subscriber.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
