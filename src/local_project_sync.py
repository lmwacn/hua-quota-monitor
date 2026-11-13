from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path
from threading import Event

from src.local_sidebar_sync import account_ids, sync_sections


_PROFILE = "electron-account-renderer-state-v1"
_OWNER = "electron-renderer-content-owner"
_OWNER_ATOMS = "electron-owner-atoms:"
_ATOMS = "electron-persisted-atom-state"
_PROJECTS = "local-projects"
_MAPPINGS = "app-server-project-id-by-legacy-project-id-by-host"
_SORT = "flat-project-sidebar-preferences-v1"
_LAYOUT = {
    "order": ("project-order", "unified-sidebar-project-order-v1"),
    "pins": ("pinned-project-ids", "unified-sidebar-pinned-order-v1"),
}
_CLOUD_FIELDS = ("chatGptBacking", "retiredChatGptBacking", "chatGptBackingCreation", "chatGptBackingRecoveryPending")
_PROJECT_FIELDS = ("id", "name", "rootPaths", "createdAt", "updatedAt")
_PINNED_SECTION = "01984de2-8f74-7c91-a3b2-5c5e937cf318"
_THREAD_PIN_ORDER = "app-server-pinned-thread-order-v1"
_COLLAPSED = "sidebar-collapsed-sections-v1"
_SHOW_PINNED = "sidebar-priority-show-pinned-v1"
_ACTIVITY = {
    "sidebar-priority-show-priority-v1": True,
    _SHOW_PINNED: False,
    "sidebar-priority-show-scheduled-v1": False,
}


def desktop_running() -> bool:
    result = subprocess.run(
        ["/bin/ps", "-axo", "comm="], capture_output=True, text=True, check=True
    )
    return any(
        path.endswith(("/Contents/MacOS/ChatGPT", "/Contents/MacOS/Codex"))
        for path in result.stdout.splitlines()
    )


def _local(project: dict) -> bool:
    return not any(project.get(key) for key in _CLOUD_FIELDS)


def _layout(globals_: dict, atoms: dict, ids: set[str]) -> dict:
    sidebar_ids = {f"codex:project:{project_id}" for project_id in ids}
    result = {
        kind: {
            "ids": [item for item in globals_.get(global_key, []) if item in ids],
            "sidebar": [item for item in atoms.get(atom_key, []) if item in sidebar_ids],
        }
        for kind, (global_key, atom_key) in _LAYOUT.items()
    }
    preferences = atoms.get(_SORT, {})
    mode = preferences.get("projectSortMode")
    if mode == "manual" and preferences.get("manualSortVersion") == 1:
        result["order"]["mode"] = "manual"
    else:
        result["order"]["mode"] = "updated_at"
    return result


def _replace_local(items: list, selected: list, local: set[str]) -> list:
    replacements = iter(selected)
    result = []
    for item in items:
        replacement = next(replacements, None) if item in local else item
        if replacement is not None:
            result.append(replacement)
    result.extend(replacements)
    return result


def _sync_layout(accounts: dict, layouts: dict, previous: dict, owner: str, ids: set[str]) -> dict:
    sidebar_ids = {f"codex:project:{project_id}" for project_id in ids}
    seed = max(
        layouts,
        key=lambda key: (
            bool(layouts[key]["pins"]["ids"] or layouts[key]["pins"]["sidebar"]),
            bool(layouts[key]["order"]["sidebar"]),
            key == owner,
        ),
    )
    selected = {}
    for kind, (global_key, atom_key) in _LAYOUT.items():
        known = {key: entry[kind] for key, entry in previous.items() if kind in entry}
        edits = [key for key in layouts if key in known and layouts[key][kind] != known[key]]
        # 同一次退出遇到冲突时，以退出前仍在使用且改过该项的账号为准。
        source = owner if owner in edits else next(iter(sorted(edits)), seed)
        selected[kind] = layouts[source][kind]
        if not edits and known:
            selected[kind] = known[owner] if owner in known else next(iter(known.values()))
        for globals_, atoms in accounts.values():
            for data, key, values, valid in (
                (globals_, global_key, selected[kind]["ids"], ids),
                (atoms, atom_key, selected[kind]["sidebar"], sidebar_ids),
            ):
                ordered = _replace_local(data.get(key, []), [item for item in values if item in valid], valid)
                if ordered or key in data:
                    data[key] = ordered
            if kind == "order":
                preferences = atoms.setdefault(_SORT, {})
                preferences["projectSortMode"] = selected[kind]["mode"]
                if selected[kind]["mode"] == "manual":
                    if preferences.get("chatSortMode") == "manual" and preferences.get("manualSortVersion") != 1:
                        raise ValueError("项目手动排序与旧版聊天排序设置冲突")
                    preferences["manualSortVersion"] = 1
    return {key: _layout(*account, ids) for key, account in accounts.items()}


def _sync_activity(accounts: dict, previous: dict, owner: str, pinned_threads: set[str]) -> dict:
    pin_key = _LAYOUT["pins"][1]
    local_keys = {f"codex:thread:local:{thread}" for thread in pinned_threads}

    def view(atoms: dict) -> dict:
        return {
            **{key: atoms.get(key, default) for key, default in _ACTIVITY.items()},
            "collapsed": atoms.get(_COLLAPSED, {}).get("pinned", False),
            "sidebar": [item for item in atoms.get(pin_key, []) if item in local_keys],
            "order": [item for item in atoms.get(_THREAD_PIN_ORDER, []) if item in pinned_threads],
        }

    views = {key: view(atoms) for key, (_, atoms) in accounts.items()}
    seed = max(views, key=lambda key: (
        bool(views[key][_SHOW_PINNED] or views[key]["sidebar"] or views[key]["order"]),
        key == owner,
    ))
    selected = {}
    for field in views[owner]:
        known = {key: entry["activity"][field] for key, entry in previous.items()
                 if field in entry.get("activity", {})}
        edits = sorted(key for key in views if key in known and views[key][field] != known[key])
        if edits:
            selected[field] = views[owner if owner in edits else edits[0]][field]
        elif known:
            selected[field] = known[owner] if owner in known else known[min(known)]
        else:
            selected[field] = views[seed][field]

    for _, atoms in accounts.values():
        for key, default in _ACTIVITY.items():
            if key in atoms or selected[key] != default:
                atoms[key] = selected[key]
        if _COLLAPSED in atoms or selected["collapsed"]:
            atoms.setdefault(_COLLAPSED, {})["pinned"] = selected["collapsed"]
        for key, field, local in (
            (pin_key, "sidebar", local_keys),
            (_THREAD_PIN_ORDER, "order", pinned_threads),
        ):
            local_order = [item for item in selected[field] if item in local]
            values = _replace_local(atoms.get(key, []), local_order, local)
            if values or key in atoms:
                atoms[key] = values
    return {key: view(atoms) for key, (_, atoms) in accounts.items()}


def _pinned_threads(state_path: Path) -> set[str]:
    # SQLite 中的本机置顶由客户端共享。这里只读取成员，过滤远程聊天和过期排序记录。
    database = state_path.with_name("state_5.sqlite")
    if not database.is_file():
        return set()
    try:
        with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True, timeout=1)) as connection:
            return {row[0] for row in connection.execute(
                "SELECT id FROM threads WHERE thread_section_id = ? AND archived = 0",
                (_PINNED_SECTION,),
            )}
    except sqlite3.OperationalError as error:
        if str(error) in ("no such table: threads", "no such column: thread_section_id"):
            return set()
        raise


def merge_local_projects(
    state: dict, previous: dict, identities: dict | None = None, pinned_threads: set[str] | None = None,
) -> dict:
    profiles = state.get(_PROFILE, {})
    identities = identities or {}
    owner = state.get(_OWNER)
    modern = bool(owner)
    if modern:
        # 新版把项目数据放在全局，只按账号保存渲染偏好。
        accounts = {
            key: (state, atoms) for key in identities
            if isinstance(atoms := state.get(f"{_OWNER_ATOMS}{key}"), dict)
        }
    else:
        owner = profiles.get("owner")
        accounts = {
            key: (entry["globals"], entry["atoms"]) for key, entry in profiles.get("inactive", {}).items()
        }
        if owner and accounts:
            accounts[owner] = (state, state.setdefault(_ATOMS, {}))
    if owner not in accounts or len(accounts) < 2:
        return previous
    shared: dict[str, dict] = {}
    mappings: dict[str, dict] = {}
    globals_by_profile = [state] if modern else [globals_ for globals_, _ in accounts.values()]
    for globals_ in globals_by_profile:
        for key, project in globals_.get(_PROJECTS, {}).items():
            if not _local(project):
                continue
            roots = project.get("rootPaths")
            if project.get("id") != key or not roots or not all(
                isinstance(root, str) and Path(root).is_absolute() for root in roots
            ):
                raise ValueError("本地项目记录格式异常")
            candidate = {field: project[field] for field in _PROJECT_FIELDS if field in project}
            old = shared.get(key)
            if old is None or candidate.get("updatedAt", 0) > old.get("updatedAt", 0):
                shared[key] = candidate
            elif candidate != old and candidate.get("updatedAt", 0) == old.get("updatedAt", 0):
                raise ValueError("同一项目有冲突且更新时间相同")
        for host, values in globals_.get(_MAPPINGS, {}).items():
            target = mappings.setdefault(host, {})
            for key, value in values.items():
                target.setdefault(key, set()).add(value)
    ids = set(shared)
    for globals_ in globals_by_profile:
        if not ids:
            continue
        projects = globals_.setdefault(_PROJECTS, {})
        for key, project in shared.items():
            existing = projects.get(key, {})
            if not _local(existing):
                raise ValueError("本地项目标识与云端项目冲突")
            projects[key] = {**existing, **project}
        for host, values in mappings.items():
            local = {key: options for key, options in values.items() if key in ids}
            if not local:
                continue
            if any(len(options) != 1 for options in local.values()):
                raise ValueError("项目后端映射冲突")
            target = globals_.setdefault(_MAPPINGS, {}).setdefault(host, {})
            target.update({key: next(iter(options)) for key, options in local.items()})
    layouts = {key: _layout(*account, ids) for key, account in accounts.items()}
    baseline = _sync_layout(accounts, layouts, previous, owner, ids) if ids else layouts
    sections = sync_sections(accounts, previous, owner, ids, identities)
    if modern:
        for key, view in _sync_activity(accounts, previous, owner, pinned_threads or set()).items():
            baseline[key]["activity"] = view
    for key, view in sections.items():
        baseline[key]["sections"] = view
    for key in baseline:
        if key not in sections and "sections" in previous.get(key, {}):
            baseline[key]["sections"] = previous[key]["sections"]
    for key, (globals_, _) in accounts.items():
        if not ids:
            continue
        order = globals_.setdefault("project-order", [])
        order.extend(project for project in shared if project not in order)
        baseline[key]["order"]["ids"] = [project for project in order if project in ids]
    return {**previous, **baseline}


class LocalProjectSync:
    def __init__(self, *, state_path: Path, store_dir: Path) -> None:
        self.state_path = state_path
        self.directory = store_dir / "local-project-sync"

    def sync(self) -> bool:
        import fcntl

        if desktop_running() or not self.state_path.is_file():
            return False
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (self.directory / "sync.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if desktop_running():
                return False
            return self._sync_closed_desktop()

    def _sync_closed_desktop(self) -> bool:
        baseline_path = self.directory / "layout.json"
        original = self.state_path.read_bytes()
        state = json.loads(original)
        previous = json.loads(baseline_path.read_bytes()) if baseline_path.is_file() else {}
        identities = account_ids([
            self.state_path.with_name("auth.json"),
            *self.directory.parent.glob("accounts/*/auth.json"),
        ])
        pinned_threads = _pinned_threads(self.state_path) if state.get(_OWNER) else None
        new_baseline = merge_local_projects(state, previous, identities, pinned_threads)
        changed = state != json.loads(original)
        if changed:
            _atomic_write(self.directory / "last-backup.json", original)
            payload = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode()
            if not _atomic_write(self.state_path, payload, before=original):
                return False
        elif desktop_running() or self.state_path.read_bytes() != original:
            return False
        if new_baseline != previous:
            _atomic_write(baseline_path, json.dumps(new_baseline, ensure_ascii=False).encode())
        return changed

    def watch(self, stop: Event) -> None:
        last_signature = None
        while not stop.is_set():
            try:
                if desktop_running():
                    last_signature = None
                elif self.state_path.is_file():
                    stat = self.state_path.stat()
                    signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
                    if signature != last_signature and not stop.wait(2):
                        self.sync()
                        last_signature = signature
            except (
                OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error, subprocess.SubprocessError,
            ) as error:
                print(f"本地项目同步失败（{type(error).__name__}）", flush=True)
                if stop.wait(30):
                    break
            stop.wait(1)


def _atomic_write(path: Path, payload: bytes, *, before: bytes | None = None) -> bool:
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".project-sync-")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if before is not None and (desktop_running() or path.read_bytes() != before):
            return False
        temporary.replace(path)
        return True
    finally:
        temporary.unlink(missing_ok=True)
