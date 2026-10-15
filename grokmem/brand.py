"""Single rename point for the product name."""

NAME = "GrokMem"
PRODUCT = "grokmem"
ENV_DIR = "GROKMEM_DIR"
WORKSPACE_ROOT = "/workspace"
WORKSPACE_STORE = "/workspace/.grokmem"
FALLBACK_STORE = "~/.grokmem"


def tool(action: str) -> str:
    return f"{PRODUCT}_{action}"
