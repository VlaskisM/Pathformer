"""PlanPathUseCase — orchestrates /plan end-to-end.

Pipeline: download map .npz → preprocess into 3-channel tensor → run model
→ render PNG → upload PNG → publish event → return result.
"""

import asyncio
import uuid
from dataclasses import dataclass

import numpy as np

from shared.events import ROUTING_KEY_PLAN_COMPLETED, PlanCompleted

from ..core.preprocessor import load_arrays, to_model_tensor
from ..core.visualizer import render_plan
from .ports import BrokerPort, PlannerPort, StoragePort


@dataclass
class PlanResult:
    plan_id: str
    waypoints: list[tuple[float, float]]  # pixel coords (row, col)
    success: bool
    n_repairs: int
    visual_bucket: str
    visual_key: str


class MapNotFoundError(Exception):
    pass


class PlanPathUseCase:
    def __init__(
        self,
        storage: StoragePort,
        broker: BrokerPort,
        planner: PlannerPort,
        maps_bucket: str,
        visuals_bucket: str,
        max_current_global: float,
    ) -> None:
        self._storage = storage
        self._broker = broker
        self._planner = planner
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
        key = f"{map_id}.npz"
        try:
            npz_bytes = await self._storage.download(self._maps_bucket, key)
        except Exception as e:
            raise MapNotFoundError(f"Map {map_id} not found in bucket {self._maps_bucket}") from e

        arrays = load_arrays(npz_bytes)
        H, W = arrays["land_mask"].shape

        # Normalize vessel feature to match training semantics
        # (pathformer/src/pathformer/data/dataset.py:202-204)
        vessel_class_normalized = vessel_max_current / self._max_current

        # Run model + render synchronously off the event loop.
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
        await self._storage.upload(self._visuals_bucket, visual_key, png)

        event = PlanCompleted(
            plan_id=plan_id,
            map_id=map_id,
            waypoints_count=int(wp_pixels.shape[0]),
            success=plan_out.success,
            n_repairs=plan_out.n_repairs,
            visual_bucket=self._visuals_bucket,
            visual_key=visual_key,
        )
        await self._broker.publish(ROUTING_KEY_PLAN_COMPLETED, event.model_dump())

        return PlanResult(
            plan_id=plan_id,
            waypoints=[(float(r), float(c)) for r, c in wp_pixels.tolist()],
            success=plan_out.success,
            n_repairs=plan_out.n_repairs,
            visual_bucket=self._visuals_bucket,
            visual_key=visual_key,
        )
