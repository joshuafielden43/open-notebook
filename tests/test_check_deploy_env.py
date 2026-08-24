"""Deploy secrets policy: door (password) + vault (encryption key)."""

import os
import subprocess
from pathlib import Path

from open_notebook.deploy_env import check_deploy_env

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-deploy-env.sh"


def _run(env: dict, env_file: Path | None = None) -> subprocess.CompletedProcess:
    cmd = ["sh", str(SCRIPT)]
    if env_file is not None:
        cmd.append(str(env_file))
    clean = {k: v for k, v in os.environ.items() if not k.startswith("OPEN_NOTEBOOK_")}
    clean.update(env)
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=clean,
        capture_output=True,
        text=True,
        check=False,
    )


def test_policy_requires_password_and_key():
    issues = check_deploy_env(
        encryption_key="",
        password="",
        allow_insecure=False,
    )
    codes = {i.code for i in issues if i.fatal}
    assert "password" in codes
    assert "encryption_key" in codes


def test_policy_rejects_placeholder_key_even_with_password():
    issues = check_deploy_env(
        encryption_key="change-me-to-a-secret-string",
        password="good-password-not-public",
        allow_insecure=False,
    )
    assert any(i.code == "encryption_key" and i.fatal for i in issues)


def test_policy_rejects_weak_password():
    issues = check_deploy_env(
        encryption_key="a" * 32,
        password="password",
        allow_insecure=False,
    )
    assert any(i.code == "password" and i.fatal for i in issues)


def test_policy_ok_when_both_real():
    issues = check_deploy_env(
        encryption_key="a" * 32,
        password="good-password-not-public",
        allow_insecure=False,
    )
    assert not any(i.fatal for i in issues)


def test_script_fails_without_password(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPEN_NOTEBOOK_ENCRYPTION_KEY=" + ("a" * 32) + "\n",
        encoding="utf-8",
    )
    result = _run({}, env_file=env_file)
    assert result.returncode == 1
    assert "password" in result.stderr.lower() or "FAIL" in result.stderr


def test_script_ok_with_both(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPEN_NOTEBOOK_ENCRYPTION_KEY="
        + ("a" * 32)
        + "\nOPEN_NOTEBOOK_PASSWORD=good-password-not-public\n",
        encoding="utf-8",
    )
    result = _run({}, env_file=env_file)
    assert result.returncode == 0
    assert "OK" in result.stdout


def test_script_allows_insecure_flag(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPEN_NOTEBOOK_ENCRYPTION_KEY=secret\n"
        "OPEN_NOTEBOOK_PASSWORD=\n"
        "OPEN_NOTEBOOK_ALLOW_INSECURE_DEFAULTS=1\n",
        encoding="utf-8",
    )
    result = _run({}, env_file=env_file)
    assert result.returncode == 0
