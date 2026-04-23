import torch
import torch.nn as nn
from torch import Tensor

from .components import MultiHeadAttention, sinusoidal_pe_1d


class DecoderLayer(nn.Module):
    """Pre-norm transformer decoder layer: self-attn -> cross-attn -> FFN."""

    def __init__(self, d_model: int, n_heads: int, d_ff: int, dropout: float):
        super().__init__()
        self.norm1 = nn.LayerNorm(d_model)
        self.self_attn = MultiHeadAttention(d_model, n_heads, dropout)
        self.drop1 = nn.Dropout(dropout)

        self.norm2 = nn.LayerNorm(d_model)
        self.cross_attn = MultiHeadAttention(d_model, n_heads, dropout)
        self.drop2 = nn.Dropout(dropout)

        self.norm3 = nn.LayerNorm(d_model)
        self.ffn = nn.Sequential(
            nn.Linear(d_model, d_ff),
            nn.ReLU(),
            nn.Linear(d_ff, d_model),
        )
        self.drop3 = nn.Dropout(dropout)

    def forward(
        self,
        x: Tensor,
        memory: Tensor,
        self_attn_mask: Tensor | None = None,
        self_kv_cache: tuple[Tensor, Tensor] | None = None,
        cross_kv_cache: tuple[Tensor, Tensor] | None = None,
    ) -> tuple[Tensor, tuple[Tensor, Tensor], tuple[Tensor, Tensor]]:
        # Pre-norm self-attention + residual
        x2 = self.norm1(x)
        x2, new_self_cache = self.self_attn(x2, x2, x2, self_attn_mask, self_kv_cache)
        x = x + self.drop1(x2)

        # Pre-norm cross-attention + residual
        x2 = self.norm2(x)
        x2, new_cross_cache = self.cross_attn(
            x2, memory, memory, None, cross_kv_cache,
            cache_only=(cross_kv_cache is not None),
        )
        x = x + self.drop2(x2)

        # Pre-norm FFN + residual
        x2 = self.norm3(x)
        x = x + self.drop3(self.ffn(x2))

        return x, new_self_cache, new_cross_cache


class TrajectoryDecoder(nn.Module):
    """Autoregressive trajectory decoder with KV cache support."""

    def __init__(
        self,
        d_model: int,
        n_heads: int,
        n_layers: int,
        d_ff: int,
        d_vessel: int,
        max_seq_len: int,
        max_step_size: float,
        dropout: float,
    ):
        super().__init__()
        self.d_model = d_model
        self.max_step_size = max_step_size

        self.wp_embed = nn.Linear(2, d_model)
        self.goal_embed = nn.Linear(2, d_model)
        self.vessel_embed = nn.Linear(d_vessel, d_model)
        pe = sinusoidal_pe_1d(max_seq_len + 1, d_model)
        self.register_buffer("pe_1d", pe)

        self.layers = nn.ModuleList(
            [DecoderLayer(d_model, n_heads, d_ff, dropout) for _ in range(n_layers)]
        )
        self.final_norm = nn.LayerNorm(d_model)
        self.output_head = nn.Linear(d_model, 2)

    def forward(
        self,
        waypoints: Tensor,
        goal: Tensor,
        vessel_class: Tensor,
        map_features: Tensor,
        mask: Tensor,
    ) -> Tensor:
        """Training: parallel over all positions. No [pred] token.

        Args:
            waypoints: (B, L, 2) input waypoints (noisy, excluding last).
            goal: (B, 2) goal position.
            vessel_class: (B, Dv) vessel class features.
            map_features: (B, S, D) encoded map tokens.
            mask: (B, L+1, L+1) attention mask.

        Returns:
            (B, L, 2) predicted displacements.
        """
        B, L, _ = waypoints.shape

        # Embed waypoints and add vessel class
        wp_emb = self.wp_embed(waypoints)  # (B, L, D)
        vessel_emb = self.vessel_embed(vessel_class).unsqueeze(1)  # (B, 1, D)
        wp_emb = wp_emb + vessel_emb

        # Add 1D positional encoding
        wp_emb = wp_emb + self.pe_1d[:L].unsqueeze(0)

        # Embed goal
        goal_emb = self.goal_embed(goal).unsqueeze(1)  # (B, 1, D)

        # Concat: [waypoints, goal]
        seq = torch.cat([wp_emb, goal_emb], dim=1)  # (B, L+1, D)

        # Run through decoder layers
        for layer in self.layers:
            seq, _, _ = layer(seq, map_features, self_attn_mask=mask)

        # Extract waypoint positions, apply head
        wp_out = seq[:, :L]  # (B, L, D)
        wp_out = self.final_norm(wp_out)
        delta = self.output_head(wp_out)  # (B, L, 2)
        delta = torch.tanh(delta) * self.max_step_size

        return delta

    def predict_step(
        self,
        waypoints: Tensor,
        goal: Tensor,
        vessel_class: Tensor,
        map_features: Tensor,
        cache: list[dict] | None = None,
    ) -> tuple[Tensor, list[dict]]:
        """Inference: single-step prediction from last waypoint, with KV cache.

        Architecture matches training exactly: sequence is [wp_0, ..., wp_{n-1}, goal].
        Prediction is extracted from the last waypoint token (position n-1).
        Cache stores ONLY waypoint KVs. Goal is recomputed every step.

        Args:
            waypoints: (B, n, 2) all waypoints generated so far.
            goal: (B, 2) goal position.
            vessel_class: (B, Dv) vessel features.
            map_features: (B, S, D) encoded map tokens.
            cache: list of dicts with 'self' and 'cross' KV caches per layer.
                   cache[i]["self"] = (K, V) containing ONLY waypoint tokens.
                   cache[i]["cross"] = (K, V) for map cross-attention.

        Returns:
            delta: (B, 2) predicted displacement for next waypoint.
            new_cache: updated cache with new waypoint added.
        """
        B, n, _ = waypoints.shape

        # Embed waypoints
        wp_emb = self.wp_embed(waypoints)
        vessel_emb = self.vessel_embed(vessel_class).unsqueeze(1)
        wp_emb = wp_emb + vessel_emb
        wp_emb = wp_emb + self.pe_1d[:n].unsqueeze(0)

        # Goal embedding (no positional encoding — matches training forward())
        goal_emb = self.goal_embed(goal).unsqueeze(1)  # (B, 1, D)

        if cache is None:
            # === FIRST STEP: process full sequence [wp_0, ..., wp_{n-1}, goal] ===
            seq = torch.cat([wp_emb, goal_emb], dim=1)  # (B, n+1, D)
            L = n + 1

            # Build attention mask: causal for waypoints, goal visible to all
            mask = torch.full((L, L), float("-inf"), device=waypoints.device)
            # Lower-triangular for waypoint block
            wp_mask = torch.zeros(n, n, device=waypoints.device)
            wp_mask.masked_fill_(
                ~torch.tril(torch.ones(n, n, device=waypoints.device, dtype=torch.bool)),
                float("-inf"),
            )
            mask[:n, :n] = wp_mask
            # Goal column: all waypoints can see goal
            mask[:, -1] = 0.0
            # Goal row: goal only self-attends
            mask[-1, :] = float("-inf")
            mask[-1, -1] = 0.0

            new_cache = []
            for layer in self.layers:
                seq, self_kv, cross_kv = layer(seq, map_features, self_attn_mask=mask)
                # CRITICAL: cache ONLY waypoint KVs (first n tokens), NOT goal
                wp_k = self_kv[0][:, :, :n]
                wp_v = self_kv[1][:, :, :n]
                new_cache.append({"self": (wp_k, wp_v), "cross": cross_kv})

            # Extract prediction from last waypoint token (position n-1)
            pred_out = seq[:, n - 1 : n]  # (B, 1, D)

        else:
            # === SUBSEQUENT STEPS: process only [new_wp, goal] ===
            new_wp_emb = wp_emb[:, -1:, :]  # (B, 1, D) — only the newest waypoint
            new_tokens = torch.cat([new_wp_emb, goal_emb], dim=1)  # (B, 2, D)

            n_cached = cache[0]["self"][0].shape[2]  # number of cached waypoint tokens

            # Mask: 2 new tokens attending to (n_cached cached + 2 new) positions
            L_total = n_cached + 2
            mask = torch.full((2, L_total), float("-inf"), device=waypoints.device)
            # Row 0 (new_wp): sees all cached waypoints + itself + goal
            mask[0, :n_cached + 1] = 0.0
            mask[0, -1] = 0.0
            # Row 1 (goal): only self-attends
            mask[1, :] = float("-inf")
            mask[1, -1] = 0.0

            new_cache = []
            for i, layer in enumerate(self.layers):
                new_tokens, self_kv, cross_kv = layer(
                    new_tokens,
                    map_features,
                    self_attn_mask=mask,
                    self_kv_cache=cache[i]["self"],
                    cross_kv_cache=cache[i]["cross"],
                )
                # CRITICAL: cache all previous waypoints + new_wp (exclude goal)
                wp_k = self_kv[0][:, :, : n_cached + 1]
                wp_v = self_kv[1][:, :, : n_cached + 1]
                new_cache.append({"self": (wp_k, wp_v), "cross": cross_kv})

            # Extract prediction from new_wp token (position 0 of new_tokens)
            pred_out = new_tokens[:, 0:1]  # (B, 1, D)

        pred_out = self.final_norm(pred_out)
        delta = self.output_head(pred_out).squeeze(1)  # (B, 2)
        delta = torch.tanh(delta) * self.max_step_size

        return delta, new_cache
