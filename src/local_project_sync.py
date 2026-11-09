from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from threading import Event
from typing import Any


_PROFILE = "electron-account-renderer-state-v1"
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
            bool(layouts[key]["pins"]["ids"]),
            bool(layouts[key]["order"]["sidebar"]),
            key == owner,
        ),
    )
    selected = {}
    for kind, (global_key, atom_key) in _LAYOUT.items():
        edits = [
            key for key in layouts if key in previous and layouts[key][kind] != previous[key][kind]
        ]
        # 同一次退出遇到冲突时，以退出前仍在使用且改过该项的账号为准。
        source = owner if owner in edits else next(iter(sorted(edits)), seed)
        selected[kind] = layouts[source][kind]
        if not edits and previous:
            selected[kind] = next(iter(previous.values()))[kind]
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


def merge_local_projects(state: dict, previous: dict) -> dict:
    profiles = state.get(_PROFILE, {})
    owner = profiles.get("owner")
    if not owner or not profiles.get("inactive"):
        return previous
    accounts = {
        owner: (state, state.setdefault(_ATOMS, {})),
        **{key: (entry["globals"], entry["atoms"]) for key, entry in profiles["inactive"].items()},
    }
    shared: dict[str, dict] = {}
    mappings: dict[str, dict] = {}
    for globals_, _ in accounts.values():
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
    if not ids:
        return previous
    for globals_, _ in accounts.values():
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
    baseline = _sync_layout(accounts, layouts, previous, owner, ids)
    for key, (globals_, _) in accounts.items():
        order = globals_.setdefault("project-order", [])
        order.extend(project for project in shared if project not in order)
        baseline[key]["order"]["ids"] = [project for project in order if project in ids]
    return baseline


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
        new_baseline = merge_local_projects(state, previous)
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
            except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as error:
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
