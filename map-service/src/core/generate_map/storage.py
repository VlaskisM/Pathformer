"""HDF5 dataset writer for synthetic map data."""
from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np

from .vessel import VESSEL_CLASSES


class DatasetWriter:
    """Writes synthetic map dataset to an HDF5 file.

    Usage::

        with DatasetWriter("dataset.h5", grid_size=64, seed=42) as w:
            w.add_map("0000", env_data)

    The file is opened immediately in ``__init__`` so the writer can be used
    both as a plain object and as a context manager.

    Root attributes
    ---------------
    grid_size : int
        Side length of the grid.
    seed : int
        Master seed used when generating the dataset.
    vessel_classes : str
        JSON-encoded list of vessel class dicts.

    Map group layout
    ----------------
    /maps/{map_id}/
        current_intensity  -- float32 (H, W), gzip-4
        current_direction  -- float32 (H, W), gzip-4
        land_mask          -- bool   (H, W), gzip-4
    """

    def __init__(self, path: str | Path, grid_size: int, seed: int) -> None:
        self._path = Path(path)
        self._f = h5py.File(str(self._path), "w")

        # Root attributes
        self._f.attrs["grid_size"] = grid_size
        self._f.attrs["seed"] = seed
        self._f.attrs["vessel_classes"] = json.dumps(VESSEL_CLASSES)

        # Pre-create /maps/ and /trajectories/ groups
        self._maps = self._f.create_group("maps")
        self._trajectories = self._f.create_group("trajectories")

        # Tracking counters for finalize()
        self._map_ids: list[str] = []
        self._traj_count: int = 0

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "DatasetWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HDF5 file."""
        self._f.close()

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def add_map(self, map_id: str, env_data: dict[str, np.ndarray]) -> None:
        """Write one map into /maps/{map_id}/.

        Parameters
        ----------
        map_id:
            Zero-padded string identifier, e.g. ``"0000"``.
        env_data:
            Dict with keys ``current_intensity``, ``current_direction``,
            ``land_mask`` — all 2-D numpy arrays.  ``land_mask`` is expected
            to be float32 (as returned by :func:`generate_map.generate_map`)
            and will be binarised via ``> 0.5`` before storing.
        """
        grp = self._maps.create_group(map_id)

        _opts = dict(compression="gzip", compression_opts=4)

        grp.create_dataset(
            "current_intensity",
            data=env_data["current_intensity"].astype(np.float32),
            **_opts,
        )
        grp.create_dataset(
            "current_direction",
            data=env_data["current_direction"].astype(np.float32),
            **_opts,
        )
        grp.create_dataset(
            "land_mask",
            data=(env_data["land_mask"] > 0.5),
            **_opts,
        )
        self._map_ids.append(map_id)

    def add_trajectory(
        self,
        traj_id: str,
        waypoints: np.ndarray,
        map_id: str,
        vessel_class_idx: int,
        vessel_max_current: float,
        start: tuple,
        goal: tuple,
        path_cost: float,
    ) -> None:
        """Write one trajectory into /trajectories/{traj_id}/.

        Parameters
        ----------
        traj_id:
            Zero-padded string identifier, e.g. ``"000000"``.
        waypoints:
            Float32 array of shape ``(N, 2)`` with (row, col) coordinates.
        map_id:
            ID of the map this trajectory belongs to.
        vessel_class_idx:
            Index into VESSEL_CLASSES list.
        vessel_max_current:
            Maximum current speed the vessel can handle.
        start:
            (row, col) start cell.
        goal:
            (row, col) goal cell.
        path_cost:
            Total path cost computed by A*.
        """
        grp = self._trajectories.create_group(traj_id)
        grp.create_dataset(
            "waypoints",
            data=waypoints.astype(np.float32),
            compression="gzip",
            compression_opts=4,
        )
        grp.attrs["map_id"] = map_id
        grp.attrs["vessel_class_idx"] = int(vessel_class_idx)
        grp.attrs["vessel_max_current"] = float(vessel_max_current)
        grp.attrs["start"] = np.array(start, dtype=np.int32)
        grp.attrs["goal"] = np.array(goal, dtype=np.int32)
        grp.attrs["path_cost"] = float(path_cost)
        grp.attrs["num_waypoints"] = int(waypoints.shape[0])
        self._traj_count += 1

    def finalize(
        self,
        grid_step_meters: float = 1.0,
        max_current_global: float = 3.0,
        train_ratio: float = 0.8,
        val_ratio: float = 0.1,
        test_ratio: float = 0.1,
    ) -> None:
        """Compute train/val/test split by map_id and write all root metadata.

        The split uses the seed stored in root attrs for deterministic shuffle.
        With fewer than 3 maps the split degrades gracefully.

        Parameters
        ----------
        grid_step_meters:
            Physical size of one grid cell in metres (written to root attrs).
        max_current_global:
            Maximum current magnitude anywhere in the dataset (root attr).
        train_ratio:
            Fraction of maps allocated to the training set.
        val_ratio:
            Fraction of maps allocated to the validation set.
        test_ratio:
            Fraction of maps allocated to the test set.
        """
        ids = sorted(self._map_ids)
        n = len(ids)

        if n == 0:
            train_ids: list[str] = []
            val_ids: list[str] = []
            test_ids: list[str] = []
        elif n == 1:
            train_ids = ids[:]
            val_ids = []
            test_ids = []
        elif n == 2:
            train_ids = [ids[0]]
            val_ids = [ids[1]]
            test_ids = []
        else:
            rng = np.random.default_rng(int(self._f.attrs["seed"]))
            perm = rng.permutation(n)
            shuffled = [ids[i] for i in perm]

            n_test = max(1, round(n * test_ratio)) if test_ratio > 0 else 0
            n_val = max(1, round(n * val_ratio)) if val_ratio > 0 else 0
            n_train = n - n_val - n_test

            train_ids = sorted(shuffled[:n_train])
            val_ids = sorted(shuffled[n_train : n_train + n_val])
            test_ids = sorted(shuffled[n_train + n_val :])

        self._f.attrs["train_map_ids"] = json.dumps(train_ids)
        self._f.attrs["val_map_ids"] = json.dumps(val_ids)
        self._f.attrs["test_map_ids"] = json.dumps(test_ids)
        self._f.attrs["num_maps"] = len(self._map_ids)
        self._f.attrs["num_trajectories"] = self._traj_count
        self._f.attrs["grid_step_meters"] = float(grid_step_meters)
        self._f.attrs["max_current_global"] = float(max_current_global)
        self._f.attrs["cost_function"] = json.dumps({
            "formula": "1.0 + 1.0*(I/3.0) + 10.0*(I > max_current) + inf*land",
            "land_cost": "inf",
            "water_min": 1.0,
            "overcurrent_penalty": 10.0,
        })
        self._f.attrs["resampling"] = json.dumps({
            "method": "arc_length",
            "target_step": 3.0,
            "min_waypoints": 10,
            "max_waypoints": 200,
        })
