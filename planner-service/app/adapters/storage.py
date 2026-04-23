import asyncio

import boto3
from botocore.client import Config


class S3Storage:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        use_ssl: bool = False,
    ) -> None:
        scheme = "https" if use_ssl else "http"
        self._client = boto3.client(
            "s3",
            endpoint_url=f"{scheme}://{endpoint}",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
        )

    def _ensure_bucket(self, bucket: str) -> None:
        try:
            self._client.head_bucket(Bucket=bucket)
        except Exception:
            self._client.create_bucket(Bucket=bucket)

    async def upload(self, bucket: str, key: str, data: bytes) -> None:
        def _sync() -> None:
            self._ensure_bucket(bucket)
            self._client.put_object(
                Bucket=bucket,
                Key=key,
                Body=data,
                ContentType="application/octet-stream",
            )

        await asyncio.to_thread(_sync)

    async def download(self, bucket: str, key: str) -> bytes:
        def _sync() -> bytes:
            resp = self._client.get_object(Bucket=bucket, Key=key)
            return resp["Body"].read()

        return await asyncio.to_thread(_sync)
