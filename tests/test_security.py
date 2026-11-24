import hashlib

from omebox.config import settings
from omebox.security import (
    check_session_auth,
    hash_password,
    hash_token,
    is_file_allowed,
    make_session_token,
    public_share,
    sanitize_filename,
    valid_code,
    valid_share_id,
    verify_password,
)


def test_password_hashing_handles_long_input_and_roundtrip():
    long_password = "pässwörd" * 40  # well over bcrypt's 72-byte limit
    hashed = hash_password(long_password)
    assert hashed != long_password
    assert verify_password(long_password, hashed)
    assert not verify_password("wrong", hashed)


def test_token_hashing_and_session_binding():
    token = "a" * 32
    assert hash_token(token) == hashlib.sha256(token.encode()).hexdigest()
    share = {"share_id": "abc123", "password_hash": "hashvalue"}
    proof = make_session_token(share)
    assert check_session_auth({f"share_abc123_auth": proof}, share)
    assert not check_session_auth({}, share)
    assert not check_session_auth({f"share_abc123_auth": "bad"}, share)
    assert proof != make_session_token({**share, "share_id": "other"})


def test_sanitize_filename_blocks_traversal():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("..\\..\\windows\\system32\\cmd.exe") == "cmd.exe"
    assert sanitize_filename("a<b>c:d/e") == "e"  # basename wins
    assert sanitize_filename("a<b>c:d") == "a_b_c_d"
    assert sanitize_filename("   ") == "unnamed"


def test_share_id_and_code_validation():
    assert valid_share_id("abcDEF_123-")
    assert not valid_share_id("bad/id")
    assert not valid_share_id("")
    assert valid_code("000000")
    assert not valid_code("12345")
    assert not valid_code("abcdef")


def test_upload_policy_rejects_disguised_executables():
    assert not is_file_allowed("photo.jpg", "application/x-dosexec")
    assert not is_file_allowed("report.pdf", "application/x-executable")
    assert not is_file_allowed("image.png", "text/html")
    assert not is_file_allowed("movie.mp4", "application/pdf")
    assert not is_file_allowed("payload.unknown", "application/octet-stream")


def test_upload_policy_keeps_supported_formats():
    assert is_file_allowed("photo.jpg", "image/jpeg")
    assert is_file_allowed("report.pdf", "application/pdf")
    assert is_file_allowed("notes.txt", "text/plain")
    assert is_file_allowed("archive.zip", "application/zip")
    assert is_file_allowed("script.py", "text/plain")


def test_public_share_removes_private_fields_and_paths():
    share = {
        "share_id": "abc",
        "password_hash": "secret",
        "delete_token_hash": "secret",
        "text_enc": "secret",
        "text_b64": "secret",
        "files": [{"id": "1", "path": "/etc/passwd", "filename": "a.txt"}],
    }
    public = public_share(share)
    assert public["share_id"] == "abc"
    for field in ("password_hash", "delete_token_hash", "text_enc", "text_b64"):
        assert field not in public
    assert "path" not in public["files"][0]
    assert public["files"][0]["filename"] == "a.txt"


def test_production_defaults_are_hardened():
    assert 4 <= settings.bcrypt_rounds <= 15
    assert "frame-ancestors 'none'" in settings.csp
    assert settings.max_files_per_share == 20
    assert not settings.enable_docs
