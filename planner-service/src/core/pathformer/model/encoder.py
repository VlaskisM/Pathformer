import torch.nn as nn
from torch import Tensor

from .components import ConvBlock


class MapEncoder(nn.Module):
    """5-block CNN encoder for map feature extraction.

    Downsamples spatial dimensions by 16x (4 stride-2 blocks + 1 stride-1 refine).
    """

    def __init__(self, in_channels: int, feature_dim: int):
        super().__init__()
        self.blocks = nn.Sequential(
            ConvBlock(in_channels, 32, stride=2),   # H/2
            ConvBlock(32, 64, stride=2),             # H/4
            ConvBlock(64, 128, stride=2),            # H/8
            ConvBlock(128, feature_dim, stride=2),   # H/16
            ConvBlock(feature_dim, feature_dim, stride=1),  # H/16 (refine)
        )

    def forward(self, x: Tensor) -> Tensor:
        """(B, C_map, H, W) -> (B, D, H/16, W/16)."""
        return self.blocks(x)
