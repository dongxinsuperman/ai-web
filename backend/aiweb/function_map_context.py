"""functionMapContext normalization and merge helpers."""
from __future__ import annotations


def normalize_function_map_context(value, *, field: str, max_chars: int) -> str | None:
    """Return stripped text, None for empty, or raise ValueError for bad input."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是字符串")
    text = value.strip()
    if not text:
        return None
    if max_chars > 0 and len(text) > max_chars:
        raise ValueError(f"{field} 超过长度限制 {max_chars}")
    return text


def merge_function_map_context(batch_text: str | None, item_text: str | None) -> str | None:
    parts = [(batch_text or "").strip(), (item_text or "").strip()]
    merged = "\n\n".join(part for part in parts if part)
    return merged or None
