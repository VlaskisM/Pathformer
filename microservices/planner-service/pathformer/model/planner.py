import torch
import torch.nn as nn
from torch import Tensor

from ..config import PlannerConfig
from .components import sinusoidal_pe_2d
from .decoder import TrajectoryDecoder
from .encoder import MapEncoder


class USVPlanner(nn.Module):
    """Top-level path planner: CNN encoder + Transformer decoder."""

    def __init__(self, config: PlannerConfig):
        super().__init__()
        self.config = config
        self.encoder = MapEncoder(config.C_map, config.d_model)
        self.decoder = TrajectoryDecoder(
            d_model=config.d_model,
            n_heads=config.n_heads,
            n_layers=config.n_layers,
            d_ff=config.d_ff,
            d_vessel=config.D_vessel,
            max_seq_len=config.max_seq_len,
            max_step_size=config.max_step_size,
            dropout=config.dropout,
        )
        # 2D PE precomputed for max expected grid size (256/16 = 16)
        max_grid = 16
        pe_2d = sinusoidal_pe_2d(max_grid, max_grid, config.d_model)
        self.register_buffer("spatial_pe", pe_2d)

    def encode_map(self, x_map: Tensor) -> Tensor:
        """Encode map to token sequence with 2D positional encoding.

        Args:
            x_map: (B, C, H, W) map tensor.

        Returns:
            (B, H'*W', D) map tokens with spatial PE.
        """
        feat = self.encoder(x_map)  # (B, D, H', W')
        B, D, H, W = feat.shape
        tokens = feat.flatten(2).transpose(1, 2)  # (B, H'*W', D)
        tokens = tokens + self.spatial_pe[: H * W]
        return tokens

    def forward(
        self,
        x_map: Tensor,
        waypoints: Tensor,
        goal: Tensor,
        vessel_class: Tensor,
        mask: Tensor,
    ) -> Tensor:
        """Training forward pass.

        Args:
            x_map: (B, C, H, W) map tensor.
            waypoints: (B, L, 2) input waypoints.
            goal: (B, 2) goal positions.
            vessel_class: (B, Dv) vessel class features.
            mask: (B, L+1, L+1) attention mask.

        Returns:
            (B, L, 2) predicted displacements.
        """
        map_tokens = self.encode_map(x_map)
        return self.decoder(waypoints, goal, vessel_class, map_tokens, mask)

    @torch.inference_mode()
    def plan(
        self,
        x_map: Tensor,
        start: Tensor,
        goal: Tensor,
        vessel_class: Tensor,
    ) -> tuple[Tensor, bool]:
        """Full autoregressive inference with KV cache.

        Args:
            x_map: (1, C, H, W) map tensor.
            start: (1, 2) start position (normalized).
            goal: (1, 2) goal position (normalized).
            vessel_class: (1, Dv) vessel class features.

        Returns:
            (1, N, 2) path waypoints, success flag.
        """
        from ..inference import plan_path

        return plan_path(self, x_map, start, goal, vessel_class, self.config)
