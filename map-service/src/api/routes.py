from fastapi import APIRouter, HTTPException, Request, Body

from src.schemas.generate import GenerateRequest, GenerateResponse

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.post("/generate", response_model=GenerateResponse)
async def generate(
    request: Request,
    req: GenerateRequest = Body(...),
) -> GenerateResponse:

    use_case = request.app.state.generate_use_case
    try:
        result = await use_case.execute(req.height, req.width, req.seed)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return GenerateResponse(map_id=result.map_id, grid_size=result.grid_size)
