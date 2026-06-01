from typing import Any, List

from astrbot.api import logger


def iter_platform_instances(context_obj) -> List[Any]:
    """Return known platform instances across AstrBot runtime versions."""
    try:
        manager = getattr(context_obj, "platform_manager", None)
        if not manager:
            return []
        if hasattr(manager, "get_insts"):
            return list(manager.get_insts())

        raw = getattr(manager, "insts", None)
        if raw:
            return list(raw.values()) if isinstance(raw, dict) else list(raw)

        return list(getattr(manager, "platform_insts", []) or [])
    except Exception as e:
        logger.debug(f"[DailySharing] 获取平台实例失败: {e}")
        return []


def get_platform_meta(inst):
    try:
        if hasattr(inst, "meta"):
            return inst.meta()
    except Exception as e:
        logger.debug(f"[DailySharing] 读取平台 meta 失败: {e}")
    return getattr(inst, "metadata", None)


def get_platform_id(inst) -> str:
    meta = get_platform_meta(inst)
    p_id = str(getattr(meta, "id", "") or "").strip()
    if p_id:
        return p_id
    config = getattr(inst, "config", {}) or {}
    return str(config.get("id", "") or getattr(inst, "id", "") or "").strip()


def get_platform_type(inst) -> str:
    meta = get_platform_meta(inst)
    p_type = str(getattr(meta, "name", "") or "").strip()
    if p_type:
        return p_type
    config = getattr(inst, "config", {}) or {}
    return str(config.get("type", "") or "").strip()


def get_platform_client(inst):
    if not inst:
        return None
    try:
        if hasattr(inst, "get_client"):
            return inst.get_client()
    except Exception as e:
        logger.debug(f"[DailySharing] 读取平台客户端失败: {e}")
    return getattr(inst, "bot", None)


def platform_match_text(inst) -> str:
    meta = get_platform_meta(inst)
    config = getattr(inst, "config", {}) or {}
    chunks = [
        get_platform_id(inst),
        get_platform_type(inst),
        inst.__class__.__name__,
        inst.__class__.__module__,
        str(config.get("id", "")),
        str(config.get("type", "")),
    ]
    if meta:
        chunks.append(str(getattr(meta, "__dict__", "")))
        for attr in ("name", "platform", "platform_type", "adapter", "adapter_type"):
            chunks.append(str(getattr(meta, attr, "")))
    return " ".join(chunks).lower()


def find_platform_instance_by_keywords(context_obj, keywords: List[str]):
    lowered = [str(k).lower() for k in keywords if k]
    exact_types = set(lowered)
    fallback = None
    for inst in iter_platform_instances(context_obj):
        p_type = get_platform_type(inst).lower()
        if p_type in exact_types:
            return inst
        text = platform_match_text(inst)
        if any(k in text for k in lowered) and fallback is None:
            fallback = inst
    return fallback


def is_weixin_oc_instance(inst) -> bool:
    names = [get_platform_type(inst), get_platform_id(inst)]
    try:
        meta = get_platform_meta(inst)
        if meta:
            names.extend(
                [
                    str(getattr(meta, "name", "") or ""),
                    str(getattr(meta, "id", "") or ""),
                ]
            )
    except Exception as e:
        logger.debug(f"[DailySharing] 判断 weixin_oc 平台失败: {e}")
    return any(str(name).strip().lower() == "weixin_oc" for name in names)
