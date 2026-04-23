"""Structural validation functions for HDF5 datasets.

Each function accepts an open h5py.File (opened for reading) and returns a
list of human-readable error strings.  An empty list means the file passes
that particular check.

Functions
---------
validate_structural(f)
    Checks that required groups, root attributes, map datasets and trajectory
    datasets/attributes are present.

validate_refs(f)
    Checks that every trajectory's ``map_id`` attribute points to an existing
    entry in ``/maps/``.

validate_splits(f)
    Checks that train/val/test map-id splits are non-overlapping and together
    cover exactly the set of maps stored in ``/maps/``.

validate_trajectories(f)
    Checks trajectory quality: start/goal on water, waypoints endpoints,
    NaN/Inf values, num_waypoints range, path_cost validity, spacing.
"""
from __future__ import annotations

import json

import h5py
import numpy as np


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_structural(f: h5py.File) -> list[str]:
    """Check groups, root attrs, map datasets, and trajectory attrs/datasets.

    Parameters
    ----------
    f:
        Open h5py.File object (any mode).

    Returns
    -------
    list[str]
        Descriptions of every structural error found.  Empty list means OK.
    """
    errors: list[str] = []

    # 1. Top-level groups
    missing_groups = []
    if "maps" not in f:
        errors.append("Missing group: /maps/")
        missing_groups.append("maps")
    if "trajectories" not in f:
        errors.append("Missing group: /trajectories/")
        missing_groups.append("trajectories")

    if missing_groups:
        # Cannot continue without the primary groups
        return errors

    # 2. Required root attributes
    required_attrs = [
        "grid_size",
        "seed",
        "vessel_classes",
        "train_map_ids",
        "val_map_ids",
        "test_map_ids",
        "num_maps",
        "num_trajectories",
    ]
    for attr in required_attrs:
        if attr not in f.attrs:
            errors.append(f"Missing root attribute: {attr}")

    # 3. Each map must have three datasets
    required_map_datasets = ["current_intensity", "current_direction", "land_mask"]
    for map_id in f["maps"]:
        map_grp = f["maps"][map_id]
        for ds_name in required_map_datasets:
            if ds_name not in map_grp:
                errors.append(f"Map {map_id}: missing dataset '{ds_name}'")

    # 4. Each trajectory must have waypoints dataset and required attributes
    required_traj_attrs = [
        "map_id",
        "vessel_class_idx",
        "vessel_max_current",
        "start",
        "goal",
        "path_cost",
        "num_waypoints",
    ]
    for traj_id in f["trajectories"]:
        tgrp = f["trajectories"][traj_id]
        if "waypoints" not in tgrp:
            errors.append(f"Trajectory {traj_id}: missing dataset 'waypoints'")
        for attr in required_traj_attrs:
            if attr not in tgrp.attrs:
                errors.append(f"Trajectory {traj_id}: missing attribute '{attr}'")

    return errors


def validate_refs(f: h5py.File) -> list[str]:
    """Check that each trajectory's map_id references an existing /maps/ entry.

    Parameters
    ----------
    f:
        Open h5py.File object (any mode).

    Returns
    -------
    list[str]
        Descriptions of every reference error found.  Empty list means OK.
    """
    errors: list[str] = []

    if "maps" not in f or "trajectories" not in f:
        # Let validate_structural catch missing groups
        return errors

    map_ids = set(f["maps"].keys())
    for traj_id in f["trajectories"]:
        tgrp = f["trajectories"][traj_id]
        if "map_id" in tgrp.attrs:
            ref_id = str(tgrp.attrs["map_id"])
            if ref_id not in map_ids:
                errors.append(
                    f"Trajectory {traj_id}: map_id '{ref_id}' not found in /maps/"
                )

    return errors


def validate_splits(f: h5py.File) -> list[str]:
    """Check that train/val/test splits are disjoint and cover all maps.

    Parameters
    ----------
    f:
        Open h5py.File object (any mode).

    Returns
    -------
    list[str]
        Descriptions of every split error found.  Empty list means OK.
    """
    errors: list[str] = []

    # Read split attrs — if missing or malformed, defer to structural check
    try:
        train = set(json.loads(f.attrs["train_map_ids"]))
        val = set(json.loads(f.attrs["val_map_ids"]))
        test = set(json.loads(f.attrs["test_map_ids"]))
    except (KeyError, json.JSONDecodeError, TypeError):
        return errors

    # Check pairwise overlaps
    if train & val:
        errors.append(
            f"Split overlap: train & val share {sorted(train & val)}"
        )
    if train & test:
        errors.append(
            f"Split overlap: train & test share {sorted(train & test)}"
        )
    if val & test:
        errors.append(
            f"Split overlap: val & test share {sorted(val & test)}"
        )

    # Check coverage against /maps/
    if "maps" in f:
        all_maps = set(f["maps"].keys())
        all_split = train | val | test

        missing = all_maps - all_split
        if missing:
            errors.append(
                f"Maps not covered by splits: {sorted(missing)}"
            )

        extra = all_split - all_maps
        if extra:
            errors.append(
                f"Splits reference non-existent maps: {sorted(extra)}"
            )

    return errors


def validate_trajectories(f: h5py.File) -> list[str]:
    """Check trajectory quality: start/goal on water, waypoints, spacing, costs.

    Parameters
    ----------
    f:
        Open h5py.File object (any mode).

    Returns
    -------
    list[str]
        Descriptions of every quality error found.  Empty list means OK.
    """
    errors: list[str] = []

    if "trajectories" not in f or "maps" not in f:
        return errors

    # Получить target_step из root attrs (если есть)
    target_step = 3.0  # default
    if "resampling" in f.attrs:
        try:
            rs = json.loads(f.attrs["resampling"])
            target_step = rs.get("target_step", 3.0)
        except (json.JSONDecodeError, TypeError):
            pass

    for traj_id in f["trajectories"]:
        tgrp = f["trajectories"][traj_id]
        prefix = f"Trajectory {traj_id}"

        # Пропустить если нет необходимых данных (structural check отловит)
        if "waypoints" not in tgrp:
            continue
        if "map_id" not in tgrp.attrs:
            continue

        waypoints = tgrp["waypoints"][:]  # (N, 2)
        map_id = str(tgrp.attrs["map_id"])

        # --- VAL-04: num_waypoints в [10, 200], нет NaN/Inf ---
        n_wp = waypoints.shape[0]
        if "num_waypoints" in tgrp.attrs:
            declared_n = int(tgrp.attrs["num_waypoints"])
            if declared_n != n_wp:
                errors.append(
                    f"{prefix}: num_waypoints attr ({declared_n}) != actual ({n_wp})"
                )

        if n_wp < 10 or n_wp > 200:
            errors.append(f"{prefix}: num_waypoints {n_wp} not in [10, 200]")

        if np.any(np.isnan(waypoints)):
            errors.append(f"{prefix}: waypoints contain NaN")
        if np.any(np.isinf(waypoints)):
            errors.append(f"{prefix}: waypoints contain Inf")

        # --- VAL-03: start/goal на воде, waypoints[0]~start, waypoints[-1]~goal ---
        if map_id in f["maps"] and "land_mask" in f["maps"][map_id]:
            land = f["maps"][map_id]["land_mask"][:]

            if "start" in tgrp.attrs:
                sr, sc = int(tgrp.attrs["start"][0]), int(tgrp.attrs["start"][1])
                if 0 <= sr < land.shape[0] and 0 <= sc < land.shape[1]:
                    if land[sr, sc]:
                        errors.append(f"{prefix}: start ({sr},{sc}) is on land")

                # waypoints[0] ~ start (tolerance 0.5)
                if n_wp > 0:
                    d = np.linalg.norm(
                        waypoints[0] - np.array([sr, sc], dtype=np.float32)
                    )
                    if d > 0.5:
                        errors.append(
                            f"{prefix}: waypoints[0] distance to start = {d:.2f} > 0.5"
                        )

            if "goal" in tgrp.attrs:
                gr, gc = int(tgrp.attrs["goal"][0]), int(tgrp.attrs["goal"][1])
                if 0 <= gr < land.shape[0] and 0 <= gc < land.shape[1]:
                    if land[gr, gc]:
                        errors.append(f"{prefix}: goal ({gr},{gc}) is on land")

                # waypoints[-1] ~ goal (tolerance 0.5)
                if n_wp > 0:
                    d = np.linalg.norm(
                        waypoints[-1] - np.array([gr, gc], dtype=np.float32)
                    )
                    if d > 0.5:
                        errors.append(
                            f"{prefix}: waypoints[-1] distance to goal = {d:.2f} > 0.5"
                        )

        # --- VAL-07: path_cost > 0 и конечен ---
        if "path_cost" in tgrp.attrs:
            pc = float(tgrp.attrs["path_cost"])
            if pc <= 0:
                errors.append(f"{prefix}: path_cost ({pc}) must be > 0")
            if not np.isfinite(pc):
                errors.append(f"{prefix}: path_cost is not finite")

        # --- VAL-05: spacing между waypoints ~target_step (±50%) ---
        if n_wp >= 2 and not np.any(np.isnan(waypoints)):
            diffs = np.diff(waypoints, axis=0)
            spacings = np.linalg.norm(diffs, axis=1)
            # Проверяем: медианный spacing в пределах target_step ±50%
            if len(spacings) > 0:
                median_sp = float(np.median(spacings))
                lo = target_step * 0.5
                hi = target_step * 1.5
                if median_sp < lo or median_sp > hi:
                    errors.append(
                        f"{prefix}: median spacing {median_sp:.2f} outside "
                        f"[{lo:.2f}, {hi:.2f}] (target_step={target_step})"
                    )

    return errors
