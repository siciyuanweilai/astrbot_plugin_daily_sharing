from typing import Any, List


def iter_platform_instances(context_obj) -> List[Any]:
    """从当前 AstrBot 平台管理器返回平台实例列表。"""
    manager = getattr(context_obj, "platform_manager", None)
    if not manager:
        return []
    return list(manager.get_insts())


def get_platform_meta(inst):
    if not inst:
        return None

    meta_getter = getattr(inst, "meta", None)
    if callable(meta_getter):
        return meta_getter()

    if hasattr(inst, "id") or hasattr(inst, "name"):
        return inst

    for attr in ("platform_meta", "metadata"):
        meta = getattr(inst, attr, None)
        if meta and (hasattr(meta, "id") or hasattr(meta, "name")):
            return meta

    return None


def get_platform_id(inst) -> str:
    meta = get_platform_meta(inst)
    return str(getattr(meta, "id", "") or "").strip()


def get_platform_type(inst) -> str:
    meta = get_platform_meta(inst)
    return str(getattr(meta, "name", "") or "").strip()


def get_platform_client(inst):
    getter = getattr(inst, "get_client", None)
    return getter() if callable(getter) else None


def platform_match_text(inst) -> str:
    chunks = [
        get_platform_id(inst),
        get_platform_type(inst),
        inst.__class__.__name__,
        inst.__class__.__module__,
    ]
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
    return any(name.strip().lower() == "weixin_oc" for name in names)
