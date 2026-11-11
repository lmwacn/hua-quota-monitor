from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from src.account_store import _decode_jwt_payload


SECTIONS = "sidebar-custom-sections-v3"


def account_ids(auth_paths: list[Path]) -> dict[str, str]:
    result = {}
    for path in auth_paths:
        try:
            payload = json.loads(path.read_bytes())
        except (OSError, ValueError):
            continue
        tokens = payload.get("tokens", {}) if isinstance(payload, dict) else {}
        if not isinstance(tokens, dict):
            continue
        for token in ("id_token", "access_token"):
            claims = _decode_jwt_payload(tokens.get(token)).get("https://api.openai.com/auth", {})
            if not isinstance(claims, dict):
                continue
            user = claims.get("chatgpt_user_id") or claims.get("user_id")
            account = tokens.get("account_id") or claims.get("chatgpt_account_id")
            if isinstance(user, str) and isinstance(account, str):
                identity = json.dumps([user, account], ensure_ascii=False, separators=(",", ":"))
                result[hashlib.sha256(identity.encode()).hexdigest()] = account
                break
    return result


def _local_item(item: str, ids: set[str], data: dict) -> bool:
    if item.startswith("codex:project:"):
        return item.removeprefix("codex:project:") in ids
    if item.startswith("codex:thread:local:"):
        thread = item.removeprefix("codex:thread:local:")
        return data.get("threadHostIds", {}).get(thread, "local") == "local"
    return False


def _identity(section: dict) -> str:
    return section.get("hostSectionIds", {}).get("local") or section["id"]


def _view(data: dict, ids: set[str]) -> dict:
    sections = []
    translated = {}
    for section in data.get("sections", []):
        hosts = section.get("hostSectionIds", {})
        items = [item for item in section["itemKeys"] if _local_item(item, ids, data)]
        if "local" not in hosts and (hosts or (section["itemKeys"] and not items)):
            continue
        key = _identity(section)
        translated[section["id"]] = key
        sections.append({
            "id": key, "name": section["name"], "itemKeys": items,
            "hostSectionIds": {"local": hosts["local"]} if "local" in hosts else {},
            "collapsed": section["id"] in data.get("collapsedSectionIds", []),
            "pendingDeletion": {
                item: ["local"] for item, hosts in section.get("pendingDeletionHostIdsByItemKey", {}).items()
                if item in items and "local" in hosts
            },
        })
    order = [translated[key[7:]] for key in data.get("sectionOrder", [])
             if key.startswith("custom:") and key[7:] in translated]
    order.extend(section["id"] for section in sections if section["id"] not in order)
    by_id = {section["id"]: section for section in sections}
    return {"sections": [by_id[key] for key in order]}


def _apply(data: dict, selected: dict, ids: set[str]) -> None:
    old = data.get("sections", [])
    local_ids = {section["id"] for section in _view(data, ids)["sections"]}
    shared = {section["id"]: section for section in selected["sections"]}
    existing = {_identity(section): section for section in old}
    result = []
    removed = set()
    for section in old:
        key = _identity(section)
        private = [item for item in section["itemKeys"] if not _local_item(item, ids, data)]
        if key in local_ids and key not in shared:
            if private or any(host != "local" for host in section.get("hostSectionIds", {})):
                section["itemKeys"] = private
                section.get("hostSectionIds", {}).pop("local", None)
                result.append(section)
            else:
                removed.add(section["id"])
        else:
            result.append(section)
    translated = {}
    collapsed = [key for key in data.get("collapsedSectionIds", []) if key not in removed]
    for key, source in shared.items():
        section = existing.get(key)
        if section is None:
            section = {"id": key, "name": source["name"], "itemKeys": []}
            result.append(section)
        section["name"] = source["name"]
        section["hostSectionIds"] = {
            **section.get("hostSectionIds", {}), **source["hostSectionIds"],
        }
        private = [item for item in section["itemKeys"] if not _local_item(item, ids, data)]
        section["itemKeys"] = private + list(source["itemKeys"])
        if "pendingDeletionHostIdsByItemKey" in section or source["pendingDeletion"]:
            section["pendingDeletionHostIdsByItemKey"] = {
                item: hosts for item, hosts in section.get("pendingDeletionHostIdsByItemKey", {}).items()
                if item in private
            }
            section["pendingDeletionHostIdsByItemKey"].update(copy.deepcopy(source["pendingDeletion"]))
        translated[key] = section["id"]
        collapsed = [item for item in collapsed if item != section["id"]]
        if source["collapsed"]:
            collapsed.append(section["id"])
        for item in source["itemKeys"]:
            if item.startswith("codex:thread:local:"):
                data.setdefault("threadHostIds", {})[item.removeprefix("codex:thread:local:")] = "local"
        if not source["hostSectionIds"] and key not in existing:
            # 旧格式分组需让客户端在下次启动时迁移到本机后端。
            legacy = data.setdefault("appServerLegacySectionIds", [s["id"] for s in old])
            if section["id"] not in legacy:
                legacy.append(section["id"])
            data["appServerMigratedHostIds"] = [
                host for host in data.get("appServerMigratedHostIds", []) if host != "local"
            ]
    selected_items = {item for section in shared.values() for item in section["itemKeys"]}
    for section in result:
        if _identity(section) not in shared:
            section["itemKeys"] = [item for item in section["itemKeys"] if item not in selected_items]
    custom_ids = {s["id"] for s in old if _identity(s) in local_ids} | removed
    order = [key for key in data.get("sectionOrder", [])
             if not (key.startswith("custom:") and key[7:] in custom_ids)]
    new_order = [f"custom:{translated[key]}" for key in shared]
    # 用原位置插入，保留内置栏目与账号私有分组之间的相对顺序。
    old_order = data.get("sectionOrder", [])
    positions = [i for i, key in enumerate(old_order) if key.startswith("custom:") and key[7:] in custom_ids]
    index = min(positions) if positions else next((i for i, key in enumerate(order) if key in ("threads", "chats")), len(order))
    order[index:index] = new_order
    data.update(sections=result, collapsedSectionIds=collapsed, sectionOrder=order)


def sync_sections(accounts: dict, previous: dict, owner: str, ids: set[str], identities: dict) -> dict:
    targets = {
        key: (atoms, identities[key]) for key, (_, atoms) in accounts.items() if key in identities
    }
    views = {key: _view(atoms.get(SECTIONS, {}).get(account, {}), ids)
             for key, (atoms, account) in targets.items()}
    if not views:
        return {}
    edits = [key for key in views if "sections" in previous.get(key, {})
             and views[key] != previous[key]["sections"]]
    seed = max(views, key=lambda key: (bool(views[key]["sections"]), key == owner))
    source = owner if owner in edits else next(iter(sorted(edits)), seed)
    selected = views[source]
    if not edits:
        selected = next((entry["sections"] for entry in previous.values() if "sections" in entry), selected)
    if not selected["sections"] and not any(view["sections"] for view in views.values()):
        return copy.deepcopy(views)
    for atoms, account in targets.values():
        data = atoms.setdefault(SECTIONS, {}).setdefault(account, {})
        _apply(data, selected, ids)
    return {key: _view(atoms[SECTIONS][account], ids) for key, (atoms, account) in targets.items()}
