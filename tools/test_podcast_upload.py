from pathlib import Path

import pytest

import config as app_config
import podcast_upload


class FakeS3:
    def __init__(self):
        self.calls = []

    def put_object(self, **kw):
        self.calls.append(kw)


def test_upload_puts_object_and_returns_public_url(tmp_path):
    f = tmp_path / "2026-07-28-zh.mp3"
    f.write_bytes(b"abc123")
    fake = FakeS3()
    cfg = app_config.load(app_config.EXAMPLE_PATH)
    url, size = podcast_upload.upload(f, client=fake, cfg=cfg)
    assert url == "https://podcast.example.com/2026-07-28-zh.mp3"
    assert size == 6
    call = fake.calls[0]
    assert call["Bucket"] == cfg["hosting"]["bucket"]
    assert call["Key"] == "2026-07-28-zh.mp3"
    assert call["ContentType"] == "audio/mpeg"
    assert call["Body"] == b"abc123"


def test_upload_raises_on_empty_file(tmp_path):
    f = tmp_path / "empty.mp3"
    f.write_bytes(b"")
    fake = FakeS3()
    cfg = app_config.load(app_config.EXAMPLE_PATH)
    with pytest.raises(ValueError, match="empty file"):
        podcast_upload.upload(f, client=fake, cfg=cfg)
    assert fake.calls == []
