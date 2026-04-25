from src.core.generate_map import generate_map


class PathformerMapGenerator:
    def generate(self, height: int, width: int, seed: int) -> dict:
        return generate_map(height=height, width=width, seed=seed)
