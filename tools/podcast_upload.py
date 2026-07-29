"""Upload an episode mp3 to Cloudflare R2 (S3 API). Env-driven credentials.

Required env: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY
"""

import os
import sys
from pathlib import Path
from typing import Tuple

import config as app_config


def _client():
    import boto3

    account = os.environ["R2_ACCOUNT_ID"]
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def upload(path: Path, client=None, cfg: dict = None) -> Tuple[str, int]:
    cfg = cfg or app_config.get()
    bucket = cfg["hosting"]["bucket"]
    public_base = cfg["hosting"]["audio_url"].rstrip("/")
    client = client or _client()
    body = path.read_bytes()
    if not body:
        raise ValueError(f"empty file: {path}")
    client.put_object(
        Bucket=bucket, Key=path.name, Body=body, ContentType="audio/mpeg"
    )
    return f"{public_base}/{path.name}", len(body)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: podcast_upload.py <file.mp3>", file=sys.stderr)
        sys.exit(2)
    url, size = upload(Path(sys.argv[1]))
    print(f"{url} {size}")
