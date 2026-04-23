import math

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor


class ConvBlock(nn.Module):
    """Two conv layers: Conv2d->BN->ReLU, Conv2d->BN->ReLU."""

    def __init__(self, in_ch: int, out_ch: int, stride: int = 2):
        super().__init__()
        self.conv1 = nn.Conv2d(in_ch, out_ch, kernel_size=3, stride=stride, padding=1)
        self.bn1 = nn.BatchNorm2d(out_ch)
        self.conv2 = nn.Conv2d(out_ch, out_ch, kernel_size=3, stride=1, padding=1)
        self.bn2 = nn.BatchNorm2d(out_ch)

    def forward(self, x: Tensor) -> Tensor:
        x = F.relu(self.bn1(self.conv1(x)))
        x = F.relu(self.bn2(self.conv2(x)))
        return x


class MultiHeadAttention(nn.Module):
    """MHA with explicit KV cache for autoregressive inference."""

    def __init__(self, d_model: int, n_heads: int, dropout: float = 0.0):
        super().__init__()
        assert d_model % n_heads == 0
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_k = d_model // n_heads

        self.W_q = nn.Linear(d_model, d_model)
        self.W_k = nn.Linear(d_model, d_model)
        self.W_v = nn.Linear(d_model, d_model)
        self.W_o = nn.Linear(d_model, d_model)
        self.dropout = dropout

    def forward(
        self,
        q: Tensor,
        k: Tensor,
        v: Tensor,
        attn_mask: Tensor | None = None,
        kv_cache: tuple[Tensor, Tensor] | None = None,
        cache_only: bool = False,
    ) -> tuple[Tensor, tuple[Tensor, Tensor]]:
        B, Lq, _ = q.shape

        Q = self.W_q(q).view(B, Lq, self.n_heads, self.d_k).transpose(1, 2)

        if cache_only and kv_cache is not None:
            # Reuse cached K,V without re-projecting (for cross-attention)
            K, V = kv_cache
        else:
            K = self.W_k(k).view(B, -1, self.n_heads, self.d_k).transpose(1, 2)
            V = self.W_v(v).view(B, -1, self.n_heads, self.d_k).transpose(1, 2)
            if kv_cache is not None:
                K = torch.cat([kv_cache[0], K], dim=2)
                V = torch.cat([kv_cache[1], V], dim=2)

        new_cache = (K, V)

        drop = self.dropout if self.training else 0.0
        # Ensure mask is 4D for SDPA: (B, 1, Lq, Lk) or (B, n_heads, Lq, Lk)
        if attn_mask is not None and attn_mask.ndim == 3:
            attn_mask = attn_mask.unsqueeze(1)  # (B, 1, Lq, Lk)
        out = F.scaled_dot_product_attention(Q, K, V, attn_mask=attn_mask, dropout_p=drop)

        out = out.transpose(1, 2).contiguous().view(B, Lq, self.d_model)
        out = self.W_o(out)
        return out, new_cache


def sinusoidal_pe_2d(h: int, w: int, d_model: int) -> Tensor:
    """2D sinusoidal positional encoding. Returns (h*w, d_model)."""
    d_half = d_model // 2
    row_pe = _sinusoidal_table(h, d_half)  # (h, d_half)
    col_pe = _sinusoidal_table(w, d_half)  # (w, d_half)

    pe = torch.zeros(h, w, d_model)
    pe[:, :, :d_half] = row_pe.unsqueeze(1)  # broadcast over columns
    pe[:, :, d_half:] = col_pe.unsqueeze(0)  # broadcast over rows
    return pe.reshape(h * w, d_model)


def sinusoidal_pe_1d(max_len: int, d_model: int) -> Tensor:
    """Standard 1D sinusoidal positional encoding. Returns (max_len, d_model)."""
    return _sinusoidal_table(max_len, d_model)


def _sinusoidal_table(length: int, d_model: int) -> Tensor:
    """Build sinusoidal PE table of shape (length, d_model)."""
    pos = torch.arange(length, dtype=torch.float32).unsqueeze(1)  # (L, 1)
    dim = torch.arange(0, d_model, 2, dtype=torch.float32)       # (d/2,)
    freq = torch.exp(dim * (-math.log(10000.0) / d_model))       # (d/2,)

    pe = torch.zeros(length, d_model)
    pe[:, 0::2] = torch.sin(pos * freq)
    pe[:, 1::2] = torch.cos(pos * freq)
    return pe


def build_causal_goal_mask(n_waypoints: int, device: torch.device) -> Tensor:
    """Build causal mask with goal token visible to all.

    L = n_waypoints + 1 (goal at end).
    Returns float mask (B=none): (L, L) with 0.0=attend, -inf=block.
    """
    L = n_waypoints + 1
    mask = torch.full((L, L), float("-inf"), device=device)

    # Causal mask for waypoint block: lower triangle = 0.0 (attend)
    causal = torch.zeros(n_waypoints, n_waypoints, device=device)
    causal.masked_fill_(~torch.tril(torch.ones(n_waypoints, n_waypoints, device=device, dtype=torch.bool)), float("-inf"))
    mask[:n_waypoints, :n_waypoints] = causal

    # Goal column: everyone can see goal
    mask[:, -1] = 0.0

    # Goal row: goal only self-attends
    mask[-1, :] = float("-inf")
    mask[-1, -1] = 0.0

    return mask


def build_training_mask(
    lengths: Tensor, max_len: int, device: torch.device
) -> Tensor:
    """Build batched causal+goal+padding mask.

    Args:
        lengths: (B,) number of waypoints per sample (including start and end).
        max_len: max waypoint count in batch (L_wp). The full sequence is L_wp+1 with goal.
        device: target device.

    Returns:
        (B, L_wp+1, L_wp+1) float mask (0.0 / -inf).
    """
    B = lengths.shape[0]
    L = max_len + 1  # waypoints + goal

    # Start with all blocked
    mask = torch.full((B, L, L), float("-inf"), device=device)

    # Causal mask for waypoint block: lower triangle = 0.0 (attend)
    causal = torch.zeros(max_len, max_len, device=device)
    causal.masked_fill_(~torch.tril(torch.ones(max_len, max_len, device=device, dtype=torch.bool)), float("-inf"))
    mask[:, :max_len, :max_len] = causal.unsqueeze(0)

    # Validity: positions beyond length are padding
    # For input waypoints [p_0..p_{N-2}], valid count = lengths - 1
    valid_counts = lengths - 1  # (B,)
    pos = torch.arange(max_len, device=device).unsqueeze(0)  # (1, max_len)
    valid = pos < valid_counts.unsqueeze(1)  # (B, max_len)

    # Block columns for invalid waypoint positions
    invalid_col = ~valid  # (B, max_len)
    mask[:, :max_len, :max_len] = mask[:, :max_len, :max_len].masked_fill(
        invalid_col.unsqueeze(1), float("-inf")
    )

    # Goal column: all valid waypoints can see goal
    mask[:, :max_len, -1] = torch.where(valid, 0.0, float("-inf"))

    # Goal row: goal only self-attends
    mask[:, -1, :] = float("-inf")
    mask[:, -1, -1] = 0.0

    return mask


