"""Register this package under top-level name `pathformer` in sys.modules.

Why: best.pt was pickled with class refs like `pathformer.config.PlannerConfig`.
After moving the package to `src.core.pathformer`, torch.load's unpickler
can't resolve those names. We re-register the same modules under their
original top-level path so pickle finds them.
"""

import importlib
import sys

_SUBMODULES = [
    "config",
    "collision_repair",
    "inference",
    "metrics",
    "path_utils",
    "model",
    "model.components",
    "model.encoder",
    "model.decoder",
    "model.planner",
]

sys.modules.setdefault("pathformer", sys.modules[__name__])
for _name in _SUBMODULES:
    _full = f"{__name__}.{_name}"
    sys.modules.setdefault(f"pathformer.{_name}", importlib.import_module(_full))
