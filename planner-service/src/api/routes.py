import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from src.domain.service import MapNotFoundError
from src.schemas.plan import PlanRequest, PlanResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.post("/plan", response_model=PlanResponse)
async def plan(req: PlanRequest, request: Request) -> PlanResponse:
    use_case = request.app.state.plan_use_case
    try:
        result = await use_case.execute(
            map_id=req.map_id,
            start_pixels=req.start,
            goal_pixels=req.goal,
            vessel_max_current=req.vessel_max_current,
        )
    except MapNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("plan failed")
        raise HTTPException(status_code=500, detail=str(e)) from e

    return PlanResponse(
        plan_id=result.plan_id,
        waypoints=result.waypoints,
        success=result.success,
        n_repairs=result.n_repairs,
        visual_url=f"/plans/{result.plan_id}/image",
    )


@router.get("/plans/{plan_id}/image")
async def plan_image(plan_id: str, request: Request) -> Response:
    storage = request.app.state.storage
    visuals_bucket = request.app.state.visuals_bucket
    try:
        png = await storage.download(visuals_bucket, f"{plan_id}.png")
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Image for plan {plan_id} not found") from e
    return Response(content=png, media_type="image/png")
