from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..domain.service import MapNotFoundError

router = APIRouter()


class PlanRequest(BaseModel):
    map_id: str
    start: tuple[int, int] = Field(..., description="(row, col) in pixels")
    goal: tuple[int, int] = Field(..., description="(row, col) in pixels")
    vessel_max_current: float = Field(
        default=1.0,
        ge=0.0,
        le=10.0,
        description=(
            "Max current (m/s) the vessel can push through. "
            "Pathformer training values: 0.5=light, 1.0=medium, 2.0=heavy. "
            "Normalized internally by max_current_global before model input."
        ),
    )


class PlanResponse(BaseModel):
    plan_id: str
    waypoints: list[tuple[float, float]]
    success: bool
    n_repairs: int = Field(
        description="Number of path segments rerouted by A* to avoid land"
    )
    visual_url: str


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
