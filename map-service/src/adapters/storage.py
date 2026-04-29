import aioboto3
from botocore.client import Config
from abc import ABC, abstractmethod


class S3ClientInterface(ABC):

    @abstractmethod
    async def upload(self, bucket: str, key: str, data: bytes) -> None: ...

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def delete(self, bucket: str, key: str) -> None: ...


class S3Client(S3ClientInterface):
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
    ) -> None:
        self._endpoint = endpoint
        self._access_key = access_key
        self._secret_key = secret_key
        self._session = aioboto3.Session()
        self._client = None
        self._ctx = None

    async def connect(self) -> None:
        self._ctx = self._session.client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name="us-east-1",
            config=Config(signature_version="s3v4"),
        )
        self._client = await self._ctx.__aenter__()

    async def close(self) -> None:
        if self._ctx is not None:
            await self._ctx.__aexit__(None, None, None)
            self._client = None
            self._ctx = None

    async def _ensure_bucket(self, bucket: str) -> None:
        try:
            await self._client.head_bucket(Bucket=bucket)
        except Exception:
            await self._client.create_bucket(Bucket=bucket)

    async def upload(self, bucket: str, key: str, data: bytes) -> None:
        await self._ensure_bucket(bucket=bucket)
        await self._client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType="application/octet-stream",
        )

    async def delete(self, bucket: str, key: str) -> None:
        try:
            await self._client.head_object(Bucket=bucket, Key=key)
            await self._client.delete_object(Bucket=bucket, Key=key)
        except Exception:
            raise FileNotFoundError(f"File {key} not found in bucket {bucket}")
        
