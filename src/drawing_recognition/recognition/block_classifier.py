"""Versionable Block-name aliases for deterministic P1 recognition."""

BLOCK_CLASS_ALIASES = {
    "resistor": ("resistor", "电阻", " resistor", "r_"),
    "switch": ("switch", "开关", "selector", "pushbutton", "button"),
    "fuse": ("fuse", "熔断器"),
    "relay": ("relay", "继电器"),
    "connector": ("connector", "插座", "接插件", "terminal"),
    "capacitor": ("capacitor", "电容"),
    "diode": ("diode", "二极管"),
}


def classify_block(block_name: str) -> str | None:
    normalized = block_name.casefold().replace("-", "_")
    for component_type, aliases in BLOCK_CLASS_ALIASES.items():
        if any(alias.casefold() in normalized for alias in aliases):
            return component_type
    return None