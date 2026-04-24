"""Cloudflare R2 upload/delete helpers for Foolish Butcher photo storage."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import FoolishConfig


def _make_client(cfg: "FoolishConfig"):
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=cfg.r2_endpoint,
        aws_access_key_id=cfg.r2_access_key_id,
        aws_secret_access_key=cfg.r2_secret_access_key,
        region_name="auto",
    )


async def upload_photo(cfg: "FoolishConfig", order_id: int, data: bytes, ext: str = "jpg") -> str:
    """Upload photo bytes to R2 and return the public URL."""
    key = f"orders/{order_id}/{int(time.time())}.{ext}"

    def _upload() -> None:
        client = _make_client(cfg)
        client.put_object(
            Bucket=cfg.r2_bucket,
            Key=key,
            Body=data,
            ContentType=f"image/{ext}",
        )

    await asyncio.to_thread(_upload)
    return f"{cfg.r2_public_url.rstrip('/')}/{key}"


async def delete_photo(cfg: "FoolishConfig", url: str) -> None:
    """Delete a photo from R2 given its public URL."""
    base = cfg.r2_public_url.rstrip("/") + "/"
    if not url.startswith(base):
        return
    key = url[len(base):]

    def _delete() -> None:
        client = _make_client(cfg)
        client.delete_object(Bucket=cfg.r2_bucket, Key=key)

    await asyncio.to_thread(_delete)
