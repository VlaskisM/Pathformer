"""PlanPathUseCase — orchestrates /plan end-to-end.

Pipeline: download map .npz → preprocess into 3-channel tensor → run model
→ render PNG → upload PNG → publish event → return result.
"""

import asyncio
import uuid
from dataclasses import dataclass
from typing import Callable

import numpy as np

from shared.events import ROUTING_KEY_PLAN_COMPLETED, PlanCompleted

from src.core.preprocessor import load_arrays, to_model_tensor
from src.core.visualizer import render_plan
from src.core.planner import ModelPlanner
from src.unit_of_work import UnitOfWorkInterface


@dataclass
class PlanResult:
    plan_id: str
    waypoints: list[tuple[float, float]]
    success: bool
    n_repairs: int
    visual_bucket: str
    visual_key: str


class MapNotFoundError(Exception):
    pass


class PlanPathUseCase:
    def __init__(
        self,
        planner: ModelPlanner,
        uow_factory: Callable[[], UnitOfWorkInterface],
        maps_bucket: str,
        visuals_bucket: str,
        max_current_global: float,
    ) -> None:
        self._planner = planner
        self._uow_factory = uow_factory
        self._maps_bucket = maps_bucket
        self._visuals_bucket = visuals_bucket
        self._max_current = max_current_global

    async def execute(
        self,
        map_id: str,
        start_pixels: tuple[int, int],
        goal_pixels: tuple[int, int],
        vessel_max_current: float,
    ) -> PlanResult:
        async with self._uow_factory() as uow:
            key = f"{map_id}.npz"
            try:
                npz_bytes = await uow.download(self._maps_bucket, key)
            except Exception as e:
                raise MapNotFoundError(f"Map {map_id} not found in bucket {self._maps_bucket}") from e

            arrays = load_arrays(npz_bytes)
            H, W = arrays["land_mask"].shape

            vessel_class_normalized = vessel_max_current / self._max_current

            def _run():
                map_tensor = to_model_tensor(arrays, self._max_current)

                start_norm = (start_pixels[0] / (H - 1), start_pixels[1] / (W - 1))
                goal_norm = (goal_pixels[0] / (H - 1), goal_pixels[1] / (W - 1))

                plan_out = self._planner.plan(map_tensor, start_norm, goal_norm, vessel_class_normalized)

                wp_pixels = plan_out.waypoints_normalized * np.array([H - 1, W - 1], dtype=np.float32)

                png = render_plan(
                    arrays=arrays,
                    waypoints_pixels=wp_pixels,
                    start_pixels=start_pixels,
                    goal_pixels=goal_pixels,
                    success=plan_out.success,
                    n_repairs=plan_out.n_repairs,
                    max_current_global=self._max_current,
                )
                return plan_out, wp_pixels, png

            plan_out, wp_pixels, png = await asyncio.to_thread(_run)

            plan_id = str(uuid.uuid4())
            visual_key = f"{plan_id}.png"
            await uow.upload(self._visuals_bucket, visual_key, png)

            event = PlanCompleted(
                plan_id=plan_id,
                map_id=map_id,
                waypoints_count=int(wp_pixels.shape[0]),
                success=plan_out.success,
                n_repairs=plan_out.n_repairs,
                visual_bucket=self._visuals_bucket,
                visual_key=visual_key,
            )
            await uow.publish(ROUTING_KEY_PLAN_COMPLETED, event.model_dump())

            return PlanResult(
                plan_id=plan_id,
                waypoints=[(float(r), float(c)) for r, c in wp_pixels.tolist()],
                success=plan_out.success,
                n_repairs=plan_out.n_repairs,
                visual_bucket=self._visuals_bucket,
                visual_key=visual_key,
            )
