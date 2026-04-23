from dataclasses import dataclass


@dataclass
class PlannerConfig:
    C_map: int = 3   # channels: [u, v, safety_field]
    d_model: int = 128
    n_heads: int = 4
    n_layers: int = 4
    d_ff: int = 512
    D_vessel: int = 1
    max_seq_len: int = 200
    max_step_size: float = 0.05
    lr: float = 1e-4
    weight_decay: float = 1e-4
    batch_size: int = 32
    epochs: int = 100
    dropout: float = 0.1
    warmup_steps: int = 2000
    noise_sigma: float = 0.01
    goal_threshold: float = 10  
    stagnation_window: int = 20
    stagnation_threshold: float = 0.01
    num_workers: int = 4
    collision_weight: float = 3.0       # Balanced with barrier function
    position_loss_weight: float = 0.0   # DISABLED: caused gradient conflict with collision

