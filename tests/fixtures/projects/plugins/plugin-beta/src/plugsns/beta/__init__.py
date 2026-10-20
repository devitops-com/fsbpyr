"""The beta plugin: one of two distributions that share the namespace `plugsns` (PEP 420)."""


def hook(text: str) -> str:
    return f"beta({text})"
