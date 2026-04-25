from pydantic import BaseModel, Field


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
