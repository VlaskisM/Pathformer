from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MapCreated(BaseModel):
    event: str = "map.created"
    map_id: str
    bucket: str
    key: str
    grid_size: tuple[int, int]
    max_current: float
    seed: int
    ts: str = Field(default_factory=_now)


class PlanCompleted(BaseModel):
    event: str = "plan.completed"
    plan_id: str
    map_id: str
    waypoints_count: int
    success: bool
    n_repairs: int = 0
    visual_bucket: str
    visual_key: str
    ts: str = Field(default_factory=_now)


ROUTING_KEY_MAP_CREATED = "map.created"
ROUTING_KEY_PLAN_COMPLETED = "plan.completed"
EXCHANGE_NAME = "pathformer"
