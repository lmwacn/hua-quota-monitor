from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import threading
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import fcntl


DEFAULT_STORE_ROOT = Path("~/.gpt-quota").expanduser()
DEFAULT_CANONICAL_AUTH_PATH = Path("~/.codex/auth.json").expanduser()
_ACCOUNT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_STATE_VERSION = 2


class AccountStoreError(RuntimeError):
    """Base error for the local account credential store."""


class InvalidAccountNameError(AccountStoreError, ValueError):
    pass


class InvalidDisplayNameError(AccountStoreError, ValueError):
    pass


class AccountExistsError(AccountStoreError):
    pass


class AccountNotFoundError(AccountStoreError):
    pass


class InvalidAuthFileError(AccountStoreError):
    pass


@dataclass(frozen=True)
class AccountProfile:
    name: str
    display_name: str
    auth_path: Path
    account_id: str | None
    email: str | None
    is_current: bool = False
    is_active: bool = False
    error: str | None = None


@dataclass(frozen=True)
class ActivationResult:
    previous: AccountProfile | None
    activated: AccountProfile
    backup_path: Path | None = None


@dataclass(frozen=True)
class _AuthMetadata:
    account_id: str | None
    email: str | None


class AccountStore:
    """Secure local store for multiple Codex ``auth.json`` profiles.

    The metadata state file only contains the selected profile name and safe
    display metadata. Credentials stay in per-profile ``auth.json`` files and
    are never copied into state.json.
    """

    def __init__(
        self,
        root: Path | str = DEFAULT_STORE_ROOT,
        canonical_auth_path: Path | str = DEFAULT_CANONICAL_AUTH_PATH,
    ) -> None:
        self.root = Path(root).expanduser()
        self.accounts_dir = self.root / "accounts"
        self.backups_dir = self.root / "backups"
        self.state_path = self.root / "state.json"
        self.lock_path = self.root / ".lock"
        self.canonical_auth_path = Path(canonical_auth_path).expanduser()
        self._thread_lock = threading.RLock()
        if self.canonical_auth_path.is_symlink():
            raise AccountStoreError(
                f"Codex 认证文件不能是符号链接：{self.canonical_auth_path}"
            )
        if self.canonical_auth_path.is_file():
            try:
                self.canonical_auth_path.chmod(0o600)
            except OSError as exc:
                raise AccountStoreError(
                    f"无法收紧 Codex 认证文件权限：{self.canonical_auth_path}"
                ) from exc
        self._ensure_layout()

    def import_account(
        self,
        name: str,
        source: Path | str,
        *,
        display_name: str | None = None,
        make_current: bool = False,
        replace: bool = False,
    ) -> AccountProfile:
        """Copy an auth.json into a named profile.

        Existing profiles are preserved unless the caller explicitly passes
        ``replace=True``.
        """

        self.validate_name(name)
        normalized_display_name = (
            self.validate_display_name(display_name) if display_name is not None else None
        )
        source_path = Path(source).expanduser()
        try:
            credential = source_path.read_bytes()
        except OSError as exc:
            raise InvalidAuthFileError(f"无法读取认证文件：{source_path}") from exc
        _parse_auth_bytes(credential, source_path)

        with self._locked():
            destination = self._auth_path(name)
            if destination.exists() and not replace:
                raise AccountExistsError(f"账号已存在：{name}")
            self._ensure_private_dir(destination.parent)
            self._atomic_write(destination, credential)
            self._set_display_name_unlocked(name, normalized_display_name)
            if make_current:
                self._set_current_unlocked(name)
            return self._profile_unlocked(name)

    def list_accounts(self) -> list[AccountProfile]:
        with self._locked():
            current = self._current_name_unlocked()
            active = self._detect_active_name_unlocked()
            profiles: list[AccountProfile] = []
            if not self.accounts_dir.exists():
                return profiles
            for directory in sorted(self.accounts_dir.iterdir(), key=lambda item: item.name.lower()):
                if (
                    directory.is_symlink()
                    or not directory.is_dir()
                    or not _ACCOUNT_NAME_PATTERN.fullmatch(directory.name)
                ):
                    continue
                profiles.append(
                    self._profile_unlocked(
                        directory.name,
                        current_name=current,
                        active_name=active,
                        tolerate_invalid=True,
                    )
                )
            return profiles

    def get_account(self, name: str) -> AccountProfile:
        self.validate_name(name)
        with self._locked():
            return self._profile_unlocked(name)

    def get_current(self) -> AccountProfile | None:
        with self._locked():
            name = self._current_name_unlocked()
            if name is None or not self._auth_path(name).is_file():
                return None
            return self._profile_unlocked(name)

    def set_current(self, name: str) -> AccountProfile:
        """Select the profile used for monitoring without changing Codex login."""

        self.validate_name(name)
        with self._locked():
            if not self._auth_path(name).is_file():
                raise AccountNotFoundError(f"账号不存在：{name}")
            self._set_current_unlocked(name)
            return self._profile_unlocked(name)

    def set_display_name(self, name: str, display_name: str) -> AccountProfile:
        """Set a user-facing name without changing the profile directory alias."""

        self.validate_name(name)
        normalized = self.validate_display_name(display_name)
        with self._locked():
            if not self._auth_path(name).is_file():
                raise AccountNotFoundError(f"账号不存在：{name}")
            self._set_display_name_unlocked(name, normalized)
            return self._profile_unlocked(name)

    def detect_active_name(self) -> str | None:
        """Return the profile whose account_id matches the canonical auth file."""

        with self._locked():
            return self._detect_active_name_unlocked()

    def snapshot_canonical(self) -> AccountProfile | None:
        """Save the canonical credential back to its matching profile.

        If the canonical account is unknown, a protected backup is created and
        ``None`` is returned. ``activate`` exposes that backup path to callers.
        """

        with self._locked():
            profile, _ = self._snapshot_canonical_unlocked()
            return profile

    def activate(self, name: str) -> ActivationResult:
        """Atomically activate a profile as the canonical Codex auth file.

        The previous canonical credential is first persisted to its matching
        profile. An unknown canonical credential is preserved in ``backups``.
        """

        self.validate_name(name)
        with self._locked():
            target_path = self._auth_path(name)
            if not target_path.is_file():
                raise AccountNotFoundError(f"账号不存在：{name}")

            previous, backup_path = self._snapshot_canonical_unlocked()
            # Reload after snapshot: when the target is already active, snapshot
            # may have written a fresher credential into the target profile.
            credential = target_path.read_bytes()
            _parse_auth_bytes(credential, target_path)
            self._ensure_private_dir(self.canonical_auth_path.parent)
            self._atomic_write(self.canonical_auth_path, credential)
            self._set_current_unlocked(name)
            activated = self._profile_unlocked(name, current_name=name, active_name=name)
            return ActivationResult(previous=previous, activated=activated, backup_path=backup_path)

    @staticmethod
    def validate_name(name: str) -> str:
        if not isinstance(name, str) or not _ACCOUNT_NAME_PATTERN.fullmatch(name):
            raise InvalidAccountNameError(
                "账号名必须为 1~64 位，仅包含 ASCII 字母、数字、下划线或连字号，"
                "且必须以字母或数字开头"
            )
        return name

    @staticmethod
    def validate_display_name(display_name: str) -> str:
        if not isinstance(display_name, str):
            raise InvalidDisplayNameError("显示名必须是字符串")
        normalized = display_name.strip()
        if not normalized or len(normalized) > 64:
            raise InvalidDisplayNameError("显示名必须为 1~64 个字符")
        if any(unicodedata.category(character) == "Cc" for character in normalized):
            raise InvalidDisplayNameError("显示名不能包含控制字符")
        return normalized

    def _profile_unlocked(
        self,
        name: str,
        *,
        current_name: str | None = None,
        active_name: str | None = None,
        tolerate_invalid: bool = False,
    ) -> AccountProfile:
        auth_path = self._auth_path(name)
        if not auth_path.is_file():
            raise AccountNotFoundError(f"账号不存在：{name}")
        try:
            metadata = _parse_auth_bytes(auth_path.read_bytes(), auth_path)
            error = None
        except (OSError, InvalidAuthFileError) as exc:
            if not tolerate_invalid:
                raise
            metadata = _AuthMetadata(account_id=None, email=None)
            error = str(exc)
        if current_name is None:
            current_name = self._current_name_unlocked()
        if active_name is None:
            active_name = self._detect_active_name_unlocked()
        return AccountProfile(
            name=name,
            display_name=self._display_name_unlocked(name),
            auth_path=auth_path,
            account_id=metadata.account_id,
            email=metadata.email,
            is_current=name == current_name,
            is_active=name == active_name,
            error=error,
        )

    def _detect_active_name_unlocked(self) -> str | None:
        if not self.canonical_auth_path.is_file():
            return None
        try:
            canonical = _parse_auth_bytes(
                self.canonical_auth_path.read_bytes(), self.canonical_auth_path
            )
        except (OSError, InvalidAuthFileError):
            return None
        if not canonical.account_id:
            return None
        for name in self._account_names_unlocked():
            try:
                profile = _parse_auth_bytes(self._auth_path(name).read_bytes(), self._auth_path(name))
            except (OSError, InvalidAuthFileError):
                continue
            if profile.account_id == canonical.account_id:
                return name
        return None

    def _snapshot_canonical_unlocked(self) -> tuple[AccountProfile | None, Path | None]:
        if not self.canonical_auth_path.is_file():
            return None, None
        try:
            credential = self.canonical_auth_path.read_bytes()
        except OSError as exc:
            raise InvalidAuthFileError(
                f"无法读取认证文件：{self.canonical_auth_path}"
            ) from exc
        metadata = _parse_auth_bytes(credential, self.canonical_auth_path)
        matched_name = None
        if metadata.account_id:
            for candidate in self._account_names_unlocked():
                try:
                    candidate_metadata = _parse_auth_bytes(
                        self._auth_path(candidate).read_bytes(), self._auth_path(candidate)
                    )
                except (OSError, InvalidAuthFileError):
                    continue
                if candidate_metadata.account_id == metadata.account_id:
                    matched_name = candidate
                    break

        if matched_name is not None:
            self._atomic_write(self._auth_path(matched_name), credential)
            return self._profile_unlocked(matched_name, active_name=matched_name), None

        self._ensure_private_dir(self.backups_dir)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = self.backups_dir / f"canonical-{timestamp}-{uuid.uuid4().hex[:8]}.auth.json"
        self._atomic_write(backup_path, credential)
        return None, backup_path

    def _current_name_unlocked(self) -> str | None:
        name = self._read_state_unlocked()["current"]
        if name is None:
            return None
        return name

    def _set_current_unlocked(self, name: str) -> None:
        state = self._read_state_unlocked()
        state["current"] = name
        self._write_state_unlocked(state)

    def _display_name_unlocked(self, name: str) -> str:
        state = self._read_state_unlocked()
        profile_state = state["profiles"].get(name)
        if not isinstance(profile_state, dict):
            return name
        display_name = profile_state.get("display_name")
        return display_name if isinstance(display_name, str) else name

    def _set_display_name_unlocked(self, name: str, display_name: str | None) -> None:
        state = self._read_state_unlocked()
        profiles = state["profiles"]
        existing = profiles.get(name)
        if display_name is None and isinstance(existing, dict):
            return
        profiles[name] = {"display_name": display_name or name}
        self._write_state_unlocked(state)

    def _read_state_unlocked(self) -> dict[str, Any]:
        if not self.state_path.exists():
            state: dict[str, Any] = {
                "version": _STATE_VERSION,
                "current": None,
                "profiles": {},
            }
            needs_write = bool(self._account_names_unlocked())
        else:
            try:
                raw_state = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise AccountStoreError(f"无法读取账号状态：{self.state_path}") from exc
            if not isinstance(raw_state, dict) or raw_state.get("version") not in (1, _STATE_VERSION):
                raise AccountStoreError(f"账号状态格式无效：{self.state_path}")
            current = self._validate_state_current(raw_state.get("current"))
            if raw_state["version"] == 1:
                state = {"version": _STATE_VERSION, "current": current, "profiles": {}}
                needs_write = True
            else:
                raw_profiles = raw_state.get("profiles")
                if not isinstance(raw_profiles, dict):
                    raise AccountStoreError(f"账号状态格式无效：{self.state_path}")
                profiles: dict[str, dict[str, str]] = {}
                try:
                    for profile_name, profile_state in raw_profiles.items():
                        self.validate_name(profile_name)
                        if not isinstance(profile_state, dict):
                            raise InvalidDisplayNameError
                        display_name = self.validate_display_name(profile_state.get("display_name"))
                        profiles[profile_name] = {"display_name": display_name}
                except (InvalidAccountNameError, InvalidDisplayNameError) as exc:
                    raise AccountStoreError(f"账号状态格式无效：{self.state_path}") from exc
                state = {"version": _STATE_VERSION, "current": current, "profiles": profiles}
                needs_write = False

        profiles = state["profiles"]
        for account_name in self._account_names_unlocked():
            if account_name not in profiles:
                profiles[account_name] = {"display_name": account_name}
                needs_write = True
        if needs_write:
            self._write_state_unlocked(state)
        return state

    def _validate_state_current(self, name: Any) -> str | None:
        if name is None:
            return None
        try:
            return self.validate_name(name)
        except InvalidAccountNameError as exc:
            raise AccountStoreError(f"账号状态格式无效：{self.state_path}") from exc

    def _write_state_unlocked(self, state: dict[str, Any]) -> None:
        data = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self._atomic_write(self.state_path, data)

    def _account_names_unlocked(self) -> list[str]:
        if not self.accounts_dir.exists():
            return []
        return sorted(
            entry.name
            for entry in self.accounts_dir.iterdir()
            if not entry.is_symlink()
            and entry.is_dir()
            and _ACCOUNT_NAME_PATTERN.fullmatch(entry.name)
        )

    def _auth_path(self, name: str) -> Path:
        self.validate_name(name)
        account_dir = self.accounts_dir / name
        auth_path = account_dir / "auth.json"
        if account_dir.is_symlink() or auth_path.is_symlink():
            raise AccountStoreError(f"账号路径不能是符号链接：{name}")
        return auth_path

    def _ensure_layout(self) -> None:
        self._ensure_private_dir(self.root)
        self._ensure_private_dir(self.accounts_dir)
        self._ensure_private_dir(self.backups_dir)

    @staticmethod
    def _ensure_private_dir(path: Path) -> None:
        if path.is_symlink():
            raise AccountStoreError(f"凭据目录不能是符号链接：{path}")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        # Store-owned directories are tightened by their callers.  The
        # canonical auth file may live in an existing caller-owned directory;
        # never chmod that broader directory as a side effect.
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            path.chmod(0o600)
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temporary_path.unlink(missing_ok=True)
            raise

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock:
            self._ensure_layout()
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)


def _parse_auth_bytes(data: bytes, path: Path) -> _AuthMetadata:
    try:
        payload = json.loads(data.decode("utf-8"))
        tokens = payload["tokens"]
        access_token = tokens["access_token"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise InvalidAuthFileError(f"认证文件格式无效：{path}") from exc
    if not isinstance(payload, dict) or not isinstance(tokens, dict):
        raise InvalidAuthFileError(f"认证文件格式无效：{path}")
    if not isinstance(access_token, str) or not access_token.strip():
        raise InvalidAuthFileError(f"认证文件缺少 access_token：{path}")

    claims = _decode_jwt_payload(tokens.get("id_token"))
    account_id = tokens.get("account_id")
    if not isinstance(account_id, str) or not account_id.strip():
        auth_claim = claims.get("https://api.openai.com/auth")
        account_id = auth_claim.get("chatgpt_account_id") if isinstance(auth_claim, dict) else None
    if not isinstance(account_id, str) or not account_id.strip():
        account_id = None

    email = claims.get("email")
    if not isinstance(email, str) or not email.strip():
        email = None
    return _AuthMetadata(account_id=account_id, email=email)


def _decode_jwt_payload(token: Any) -> dict[str, Any]:
    """Decode unverified display metadata from a JWT payload."""

    if not isinstance(token, str):
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        encoded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
