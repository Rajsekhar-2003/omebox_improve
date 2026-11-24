from datetime import timedelta

from omebox.database import db
from omebox.security import utcnow


async def insert_share(**overrides):
    share = {
        "share_id": "testshare01",
        "code_6digit": "123456",
        "share_type": "text",
        "text_enc": "encrypted-placeholder",
        "text_length": 5,
        "expiry": utcnow() + timedelta(hours=1),
        "created_at": utcnow(),
        "expiry_option": "1h",
        "views": 0,
        "is_compressed": False,
        "has_password": False,
        "delete_token_hash": "0" * 64,
    }
    share.update(overrides)
    await db.shares.insert_one(share)
    return share


async def test_create_text_share_encrypts_content(client):
    response = await client.post(
        "/api/share/text",
        data={"text": "top secret message", "expiry": "1h", "password": "secret123"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["url"].startswith("http://testserver/s/")
    assert body["has_password"] is True
    assert body["qr_code"]

    document = await db.shares.find_one({"share_id": body["share_id"]})
    assert document is not None
    assert "top secret message" not in document["text_enc"]
    assert document["password_hash"]
    assert document["delete_token_hash"] != body["delete_token"]


async def test_password_flow_and_raw_text(client):
    created = await client.post(
        "/api/share/text", data={"text": "hello world", "expiry": "1h", "password": "pw123456"}
    )
    share_id = created.json()["share_id"]

    locked = await client.get(f"/s/{share_id}")
    assert locked.status_code == 200
    assert "password" in locked.text.lower()

    wrong = await client.post(f"/s/{share_id}/verify", data={"password": "wrong"})
    assert wrong.status_code == 403

    good = await client.post(f"/s/{share_id}/verify", data={"password": "pw123456"})
    assert good.status_code == 302
    assert good.headers["location"] == f"/s/{share_id}"

    viewed = await client.get(f"/s/{share_id}")
    assert viewed.status_code == 200
    assert "hello world" in viewed.text

    raw = await client.get(f"/api/share/{share_id}/raw")
    assert raw.status_code == 200
    assert raw.json()["text"] == "hello world"
    assert "text_enc" not in raw.json()


async def test_file_upload_download_and_delete(client):
    created = await client.post(
        "/api/share/files",
        data={"expiry": "1d"},
        files={"files": ("note.txt", b"hello file", "text/plain")},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["total_files"] == 1
    document = await db.shares.find_one({"share_id": body["share_id"]})
    file_id = document["files"][0]["id"]
    download = await client.get(f"/api/share/{body['share_id']}/file/{file_id}")
    assert download.status_code == 200
    assert download.content == b"hello file"
    assert download.headers["x-content-type-options"] == "nosniff"

    raw = await client.get(f"/api/share/{body['share_id']}/raw")
    assert raw.status_code == 200
    assert raw.json()["files"][0]["filename"] == "note.txt"
    assert "path" not in raw.json()["files"][0]

    deleted = await client.request(
        "DELETE", f"/api/share/{body['share_id']}",
        headers={"X-Delete-Token": body["delete_token"]},
    )
    assert deleted.status_code == 200
    assert await db.shares.find_one({"share_id": body["share_id"]}) is None


async def test_delete_requires_token(client):
    share = await insert_share()
    response = await client.delete(f"/api/share/{share['share_id']}")
    assert response.status_code == 403
    assert await db.shares.find_one({"share_id": share["share_id"]}) is not None


async def test_disguised_executable_is_rejected(client):
    payload = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 128
    response = await client.post(
        "/api/share/files",
        files={"files": ("photo.jpg", payload, "image/jpeg")},
    )
    assert response.status_code == 400
    assert "not allowed" in response.text


async def test_expired_share_returns_410(client):
    share = await insert_share(
        share_id="expired123", expiry=utcnow() - timedelta(minutes=1)
    )
    response = await client.get(f"/s/{share['share_id']}")
    assert response.status_code == 410
    # Access triggers immediate deletion, so a second request is a clean 404.
    raw = await client.get(f"/api/share/{share['share_id']}/raw")
    assert raw.status_code == 404
    assert await db.shares.find_one({"share_id": share["share_id"]}) is None


async def test_access_by_code(client):
    await insert_share(code_6digit="654321")
    response = await client.post("/access", data={"code": "654321"})
    assert response.status_code == 302
    assert response.headers["location"] == "/s/testshare01"

    bad = await client.post("/access", data={"code": "000000"})
    assert bad.status_code == 200
    assert "Invalid code" in bad.text


async def test_admin_endpoints_require_key(client):
    denied = await client.get("/api/stats")
    assert denied.status_code == 401
    allowed = await client.get("/api/stats", headers={"x-api-key": "test-admin-key"})
    assert allowed.status_code == 200
    assert allowed.json()["database"]["name"] == "omebox_test"

    cleanup_response = await client.post(
        "/api/cleanup", headers={"x-api-key": "test-admin-key"}
    )
    assert cleanup_response.status_code == 200
    assert cleanup_response.json()["success"] is True

    status = await client.get(
        "/api/maintenance/status", headers={"x-api-key": "test-admin-key"}
    )
    assert status.status_code == 200
    assert "scheduler" in status.json()


async def test_health_and_security_headers(client):
    health = await client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "healthy"

    home = await client.get("/")
    assert home.status_code == 200
    assert home.headers["x-content-type-options"] == "nosniff"
    assert home.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" in home.headers
    assert "referrer-policy" in home.headers


async def test_legacy_pages_and_seo_files(client):
    for path in ("/", "/main", "/about", "/contact", "/share/text",
                 "/share/files", "/access", "/privacy-policy",
                 "/anonymous-file-share", "/omebox-article",
                 "/best-programming-langues", "/best-file-share-website"):
        response = await client.get(path)
        assert response.status_code == 200, path

    assert (await client.get("/robots.txt")).status_code == 200
    sitemap = await client.get("/sitemap.xml")
    assert sitemap.status_code == 200
    assert "omebox.com" in sitemap.text
    assert "google.com" in (await client.get("/ads.txt")).text
    assert (await client.get("/home")).status_code in (301, 307)
    assert (await client.get("/receiver")).status_code in (301, 307)


async def test_api_docs_are_disabled(client):
    assert (await client.get("/docs")).status_code == 404
    assert (await client.get("/openapi.json")).status_code == 404
