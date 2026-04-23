import secrets

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()


class GenerateRequest(BaseModel):
    height: int = Field(default=128, ge=16, le=1024)
    width: int = Field(default=128, ge=16, le=1024)
    seed: int | None = None


class GenerateResponse(BaseModel):
    map_id: str
    grid_size: tuple[int, int]


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest, request: Request) -> GenerateResponse:
    use_case = request.app.state.generate_use_case
    seed = req.seed if req.seed is not None else secrets.randbits(32)
    try:
        result = await use_case.execute(req.height, req.width, seed)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return GenerateResponse(map_id=result.map_id, grid_size=result.grid_size)
