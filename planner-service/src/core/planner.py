"""ModelPlanner — loads best.pt lazily (once) and runs neural inference
with A* collision repair, mirroring canonical pathformer evaluation.

Thread-safe: a single lock guards the lazy init. After that, inference
happens under torch.inference_mode().
"""

import threading
from dataclasses import dataclass

import numpy as np
import torch

from src.core.pathformer.inference import plan_path_with_repair
from src.core.pathformer.model.planner import USVPlanner

# Matches pathformer/src/pathformer/evaluation.py:DEFAULT_GOAL_THRESHOLD
DEFAULT_GOAL_THRESHOLD = 0.04


@dataclass
class PlanOutput:
    waypoints_normalized: np.ndarray  # (N, 2), in [0, 1]
    success: bool
    n_repairs: int


class ModelPlanner:
    def __init__(self, weights_path: str, device: str = "cpu") -> None:
        self._weights_path = weights_path
        self._device = device
        self._model: USVPlanner | None = None
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> USVPlanner:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                ckpt = torch.load(
                    self._weights_path,
                    map_location=self._device,
                    weights_only=False,
                )
                config = ckpt["config"]

                # Guard against legacy/stale checkpoints storing goal_threshold
                # in the wrong units. Inference expects normalized [0, 1] space;
                # max reachable dist is sqrt(2) ~ 1.41, so threshold must be
                # well below 1. Mirrors prepare_config_for_evaluation in
                # pathformer/src/pathformer/evaluation.py:83-91.
                if (
                    getattr(config, "goal_threshold", 0.0) <= 0.0
                    or config.goal_threshold > 0.25
                ):
                    config.goal_threshold = DEFAULT_GOAL_THRESHOLD

                model = USVPlanner(config).to(self._device)
                model.load_state_dict(ckpt["model_state_dict"])
                model.eval()
                self._model = model
        return self._model

    def plan(
        self,
        map_tensor: np.ndarray,
        start_normalized: tuple[float, float],
        goal_normalized: tuple[float, float],
        vessel_class: float,
    ) -> PlanOutput:
        model = self._ensure_loaded()

        x_map = torch.from_numpy(map_tensor).unsqueeze(0).to(self._device)
        start = torch.tensor([list(start_normalized)], dtype=torch.float32, device=self._device)
        goal = torch.tensor([list(goal_normalized)], dtype=torch.float32, device=self._device)
        vc = torch.tensor([[float(vessel_class)]], dtype=torch.float32, device=self._device)

        with torch.inference_mode():
            waypoints, success, n_repairs = plan_path_with_repair(
                model, x_map, start, goal, vc, model.config
            )

        wp_np = waypoints.squeeze(0).cpu().numpy()
        return PlanOutput(
            waypoints_normalized=wp_np,
            success=bool(success),
            n_repairs=int(n_repairs),
        )
