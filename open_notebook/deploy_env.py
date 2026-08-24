"""Deploy-time secrets policy for Open Notebook.

The door is OPEN_NOTEBOOK_PASSWORD. The vault is OPEN_NOTEBOOK_ENCRYPTION_KEY.
Neither is optional in a real install; both refuse known public placeholders.

Lab-only escape: OPEN_NOTEBOOK_ALLOW_INSECURE_DEFAULTS=1 (tests, throwaway boxes).

Used by:
- API lifespan (api/main.py)
- host preflight (scripts/check-deploy-env.sh → python -m open_notebook.deploy_env)
- container entrypoint (same module)
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Iterable, Optional

from open_notebook.utils.encryption import get_secret_from_env

# Docs/examples historically shipped these; they are not secrets.
INSECURE_ENCRYPTION_PLACEHOLDERS = frozenset(
    {
        "",
        "change-me-to-a-secret-string",
        "change-me",
        "secret",
        "changeme",
        "your-unique-secret-here",
        "my-secret-passphrase",
        "my-super-secret-key-123",
    }
)

INSECURE_PASSWORD_PLACEHOLDERS = frozenset(
    {
        "",
        "password",
        "pass",
        "admin",
        "changeme",
        "change-me",
        "secret",
        "open-notebook",
        "open_notebook",
    }
)


def allow_insecure_defaults() -> bool:
    return os.getenv("OPEN_NOTEBOOK_ALLOW_INSECURE_DEFAULTS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _is_placeholder(value: Optional[str], placeholders: Iterable[str]) -> bool:
    if value is None:
        return True
    stripped = value.strip()
    if stripped in placeholders:
        return True
    return stripped.lower() in {p.lower() for p in placeholders if p}


@dataclass(frozen=True)
class DeployEnvIssue:
    code: str
    message: str
    fatal: bool = True


def check_deploy_env(
    *,
    encryption_key: Optional[str] = None,
    password: Optional[str] = None,
    allow_insecure: Optional[bool] = None,
) -> list[DeployEnvIssue]:
    """Return policy issues. Fatal ones must stop boot/compose unless allow_insecure."""
    if encryption_key is None:
        encryption_key = get_secret_from_env("OPEN_NOTEBOOK_ENCRYPTION_KEY")
    if password is None:
        password = get_secret_from_env("OPEN_NOTEBOOK_PASSWORD")
    if allow_insecure is None:
        allow_insecure = allow_insecure_defaults()

    issues: list[DeployEnvIssue] = []

    if _is_placeholder(encryption_key, INSECURE_ENCRYPTION_PLACEHOLDERS):
        issues.append(
            DeployEnvIssue(
                code="encryption_key",
                message=(
                    "OPEN_NOTEBOOK_ENCRYPTION_KEY is missing or a known public "
                    "placeholder. Set a unique secret (openssl rand -hex 32)."
                ),
                fatal=not allow_insecure,
            )
        )
    elif encryption_key and len(encryption_key.strip()) < 16:
        issues.append(
            DeployEnvIssue(
                code="encryption_key_short",
                message=(
                    "OPEN_NOTEBOOK_ENCRYPTION_KEY is shorter than 16 characters; "
                    "prefer openssl rand -hex 32."
                ),
                fatal=False,
            )
        )

    if _is_placeholder(password, INSECURE_PASSWORD_PLACEHOLDERS):
        issues.append(
            DeployEnvIssue(
                code="password",
                message=(
                    "OPEN_NOTEBOOK_PASSWORD is missing or a weak/public placeholder. "
                    "Without a real password the API is unauthenticated — that is "
                    "the front door, not a footnote. Set a strong password."
                ),
                fatal=not allow_insecure,
            )
        )

    return issues


def enforce_deploy_env(*, exit_on_fatal: bool = False) -> list[DeployEnvIssue]:
    """Apply policy for process boot. Raises RuntimeError or exits on fatals."""
    issues = check_deploy_env()
    fatals = [i for i in issues if i.fatal]
    warnings = [i for i in issues if not i.fatal]

    for issue in warnings:
        print(f"deploy_env: WARNING: {issue.message}", file=sys.stderr)
    for issue in fatals:
        print(f"deploy_env: FAIL: {issue.message}", file=sys.stderr)
        if allow_insecure_defaults():
            print(
                "deploy_env: continuing because OPEN_NOTEBOOK_ALLOW_INSECURE_DEFAULTS=1",
                file=sys.stderr,
            )

    if fatals and not allow_insecure_defaults():
        msg = " ".join(i.message for i in fatals)
        if exit_on_fatal:
            print(
                "deploy_env: Lab only: OPEN_NOTEBOOK_ALLOW_INSECURE_DEFAULTS=1",
                file=sys.stderr,
            )
            raise SystemExit(1)
        raise RuntimeError(msg)

    oks = []
    if not any(i.code.startswith("encryption") and i.fatal for i in issues):
        key = get_secret_from_env("OPEN_NOTEBOOK_ENCRYPTION_KEY") or ""
        if key and not _is_placeholder(key, INSECURE_ENCRYPTION_PLACEHOLDERS):
            oks.append(f"encryption key present (length {len(key.strip())})")
    if not any(i.code == "password" and i.fatal for i in issues):
        pw = get_secret_from_env("OPEN_NOTEBOOK_PASSWORD") or ""
        if pw and not _is_placeholder(pw, INSECURE_PASSWORD_PLACEHOLDERS):
            oks.append("password is set")
    for line in oks:
        print(f"deploy_env: OK {line}")

    return issues


def main(argv: Optional[list[str]] = None) -> int:
    """CLI for host preflight and container entrypoint."""
    # Optional path to .env: load without overriding existing env.
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        env_path = args[0]
        if os.path.isfile(env_path):
            try:
                from dotenv import load_dotenv

                load_dotenv(env_path, override=False)
            except ImportError:
                # Minimal .env load without python-dotenv
                with open(env_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, _, v = line.partition("=")
                        k, v = k.strip(), v.strip().strip('"').strip("'")
                        if k and k not in os.environ:
                            os.environ[k] = v

    try:
        enforce_deploy_env(exit_on_fatal=True)
    except SystemExit as e:
        return int(e.code or 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
