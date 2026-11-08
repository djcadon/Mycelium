#!/usr/bin/env python3

import json
from typing import Any

PROTOCOL_VERSION = 1
MAX_DATAGRAM_BYTES = 1200
MAX_PAYLOAD_BYTES = 512
NODE_UDP_PORT = 45801
BRIDGE_UDP_PORT = 45802


class ProtocolError(ValueError):
    pass


def encode_datagram(message: dict[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"message cannot be encoded as JSON: {exc}") from exc

    if len(encoded) > MAX_DATAGRAM_BYTES:
        raise ProtocolError(
            f"datagram is {len(encoded)} bytes; maximum is {MAX_DATAGRAM_BYTES}"
        )
    return encoded


def decode_datagram(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_DATAGRAM_BYTES:
        raise ProtocolError(
            f"datagram is {len(data)} bytes; maximum is {MAX_DATAGRAM_BYTES}"
        )
    try:
        message = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"datagram is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(message, dict):
        raise ProtocolError("datagram JSON must be an object")
    return message


def require_string(message: dict[str, Any], key: str) -> str:
    value = message.get(key)
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"{key} must be a non-empty string")
    return value


def require_payload(message: dict[str, Any]) -> str:
    payload = require_string(message, "payload")
    if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ProtocolError(
            f"payload exceeds the {MAX_PAYLOAD_BYTES}-byte limit"
        )
    return payload
