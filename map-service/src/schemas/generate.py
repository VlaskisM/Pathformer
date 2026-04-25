from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    height: int = Field(default=128, ge=16, le=1024)
    width: int = Field(default=128, ge=16, le=1024)
    seed: int | None = None


class GenerateResponse(BaseModel):
    map_id: str
    grid_size: tuple[int, int]
