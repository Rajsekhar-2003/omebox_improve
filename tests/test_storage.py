import asyncio

import pytest

from omebox.database import db
from omebox.storage import decode_text, encode_text


def test_text_compression_roundtrip():
    short_token, compressed = encode_text("short text")
    assert compressed is False
    assert decode_text({"text_enc": short_token}) == "short text"

    long_text = "Lorem ipsum dolor sit amet. " * 1000
    long_token, compressed = encode_text(long_text)
    assert compressed is True
    assert decode_text({"text_enc": long_token, "is_compressed": True}) == long_text
    assert "Lorem ipsum" not in long_token


def test_legacy_base64_shares_still_decode():
    import base64
    share = {"text_b64": base64.b64encode(b"legacy text").decode()}
    assert decode_text(share) == "legacy text"


async def test_many_concurrent_share_creations_get_unique_codes(client):
    responses = await asyncio.gather(*[
        client.post(
            "/api/share/text",
            data={"text": f"message {index}", "expiry": "1h"},
        )
        for index in range(40)
    ])
    assert all(response.status_code == 200 for response in responses)
    codes = [response.json()["code"] for response in responses]
    share_ids = [response.json()["share_id"] for response in responses]
    assert len(set(codes)) == len(codes) == 40
    assert len(set(share_ids)) == 40
    assert await db.shares.count_documents({}) == 40
