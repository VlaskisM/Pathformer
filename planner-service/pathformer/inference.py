import numpy as np
import torch
from torch import Tensor

from .config import PlannerConfig


@torch.inference_mode()
def plan_path(
    model: "USVPlanner",
    x_map: Tensor,
    start: Tensor,
    goal: Tensor,
    vessel_class: Tensor,
    config: PlannerConfig,
) -> tuple[Tensor, bool]:
    """Full autoregressive inference with KV caching.

    Args:
        model: USVPlanner instance (already in eval mode).
        x_map: (1, C, H, W) map tensor.
        start: (1, 2) start position (normalized).
        goal: (1, 2) goal position (normalized).
        vessel_class: (1, Dv) vessel class features.
        config: planner configuration.

    Returns:
        (1, N, 2) path waypoints, success flag.
    """
    # Encode map once
    map_tokens = model.encode_map(x_map)  # (1, S, D)

    # Initialize
    path = [start]  # list of (1, 2) tensors
    cache = None

    for step in range(config.max_seq_len):
        waypoints = torch.stack(path, dim=1)  # (1, n, 2)

        # Decoder step with KV cache
        delta_p, cache = model.decoder.predict_step(
            waypoints, goal, vessel_class, map_tokens, cache,
        )

        # New waypoint
        p_new = path[-1] + delta_p  # (1, 2)
        path.append(p_new)

        # Goal proximity check
        dist_to_goal = torch.norm(p_new - goal, dim=-1)
        if dist_to_goal.item() < config.goal_threshold:
            path.append(goal)
            return torch.stack(path, dim=1), True

        # Stagnation detection
        if step >= config.stagnation_window:
            old_pos = path[-(config.stagnation_window + 1)]
            progress = torch.norm(p_new - old_pos, dim=-1)
            if progress.item() < config.stagnation_threshold:
                return torch.stack(path, dim=1), False

    return torch.stack(path, dim=1), False


@torch.inference_mode()
def plan_path_with_repair(
    model: "USVPlanner",
    x_map: Tensor,
    start: Tensor,
    goal: Tensor,
    vessel_class: Tensor,
    config: PlannerConfig,
) -> tuple[Tensor, bool, int]:
    """Plan path with optional A* collision repair.

    Returns:
        (1, N, 2) path waypoints, success flag, number of repaired segments.
    """
    import numpy as np
    from .collision_repair import repair_path

    # Step 1: Neural planner generates coarse path
    raw_path, reached = plan_path(model, x_map, start, goal, vessel_class, config)

    # Step 2: Derive binary land mask from safety_field (channel 2)
    # safety_field = 1.0 on land, decays toward 0.0 in water
    # Threshold at 0.95 to get binary land mask (on-land pixels)
    safety_field = x_map[0, 2].cpu().numpy()
    land_mask = (safety_field > 0.95).astype(np.float32)

    # Step 3: Repair collisions
    path_np = raw_path.squeeze(0).cpu().numpy()
    repaired_np, n_repairs, repair_ranges = repair_path(path_np, land_mask)

    repaired_tensor = torch.from_numpy(repaired_np).unsqueeze(0).to(raw_path.device)

    return repaired_tensor, reached, n_repairs
