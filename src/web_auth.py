from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path


DEFAULT_LOGIN_TIMEOUT = 600.0
_MAX_AUTH_FILE_SIZE = 16 * 1024 * 1024


class WebAuthError(RuntimeError):
    """Base error for an isolated Codex browser login."""


class CodexNotFoundError(WebAuthError):
    pass


class WebAuthTimeoutError(WebAuthError):
    pass


class WebAuthCommandError(WebAuthError):
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        super().__init__(f"Codex 网页授权失败（退出码 {returncode}）")


class InvalidWebAuthResultError(WebAuthError):
    pass


def login_with_chatgpt(
    *,
    timeout: float = DEFAULT_LOGIN_TIMEOUT,
    codex_executable: Path | str | None = None,
) -> bytes:
    """Log in through ChatGPT in an isolated ``CODEX_HOME`` and return auth bytes.

    The normal Codex home (and therefore ``~/.codex/auth.json``) is never used.
    The temporary home is removed before this function returns, on both success
    and failure. Command output is discarded so credentials cannot leak into
    application logs.

    This function blocks while the browser login is in progress. GUI callers
    should run it on a background thread.
    """

    if timeout <= 0:
        raise ValueError("授权超时时间必须大于 0")

    executable = _resolve_codex_executable(codex_executable)
    with tempfile.TemporaryDirectory(prefix="hua-quota-monitor-login-") as temp_name:
        isolated_home = Path(temp_name)
        isolated_home.chmod(0o700)
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(isolated_home)
        environment["PATH"] = _login_path(executable, environment.get("PATH", ""))

        try:
            process = subprocess.Popen(
                [
                    executable,
                    "login",
                    "-c",
                    'cli_auth_credentials_store="file"',
                ],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise CodexNotFoundError("无法启动 codex 命令，请检查 Codex CLI 安装") from exc
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _terminate_process_group(process)
            raise WebAuthTimeoutError(
                f"等待 ChatGPT 网页授权超时（{timeout:g} 秒）"
            ) from exc

        if returncode != 0:
            raise WebAuthCommandError(returncode)

        auth_path = isolated_home / "auth.json"
        return _read_auth_result(auth_path)


def _resolve_codex_executable(executable: Path | str | None) -> str:
    if executable is None:
        resolved = shutil.which("codex")
        if not resolved:
            candidates = []
            npm_prefix = os.environ.get("NPM_CONFIG_PREFIX")
            if npm_prefix:
                candidates.append(Path(npm_prefix).expanduser() / "bin" / "codex")
            candidates.extend(
                (
                    Path.home() / ".npm-global" / "bin" / "codex",
                    Path.home() / ".local" / "bin" / "codex",
                    Path("/opt/homebrew/bin/codex"),
                    Path("/usr/local/bin/codex"),
                )
            )
            resolved = next(
                (os.fspath(path) for path in candidates if os.access(path, os.X_OK)),
                None,
            )
    elif isinstance(executable, Path):
        candidate = os.fspath(executable.expanduser())
        resolved = candidate if os.access(candidate, os.X_OK) else None
    else:
        candidate = os.fspath(Path(executable).expanduser())
        if os.sep in candidate or (os.altsep and os.altsep in candidate):
            resolved = candidate if os.access(candidate, os.X_OK) else None
        else:
            resolved = shutil.which(candidate)
    if not resolved:
        raise CodexNotFoundError("未找到 codex 命令，请先安装 Codex CLI")
    return resolved


def _login_path(executable: str, existing_path: str) -> str:
    """Provide GUI-launched npm scripts with both ``codex`` and ``node`` paths."""

    candidates = [
        os.fspath(Path(executable).parent),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        os.fspath(Path.home() / ".npm-global" / "bin"),
        os.fspath(Path.home() / ".local" / "bin"),
        *existing_path.split(os.pathsep),
    ]
    unique = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return os.pathsep.join(unique)


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        process.terminate()
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        process.kill()
    process.wait()


def _read_auth_result(auth_path: Path) -> bytes:
    if auth_path.is_symlink() or not auth_path.is_file():
        raise InvalidWebAuthResultError("网页授权完成，但没有生成有效的 auth.json")
    try:
        size = auth_path.stat().st_size
        if size <= 0 or size > _MAX_AUTH_FILE_SIZE:
            raise InvalidWebAuthResultError("网页授权生成的 auth.json 大小异常")
        credential = auth_path.read_bytes()
        parsed = json.loads(credential)
    except InvalidWebAuthResultError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidWebAuthResultError(
            "网页授权生成的 auth.json 无法读取或格式无效"
        ) from exc
    if not isinstance(parsed, dict):
        raise InvalidWebAuthResultError("网页授权生成的 auth.json 格式无效")
    return credential
