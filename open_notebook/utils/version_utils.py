"""
Version utilities for Open Notebook.
Handles version comparison, GitHub version fetching, and package version management.
"""

import tomllib
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import urlparse

import httpx
from packaging.version import parse as parse_version


def _github_owner_repo(repo_url: str) -> tuple[str, str]:
    parsed_url = urlparse(repo_url)
    if "github.com" not in parsed_url.netloc:
        raise ValueError("Not a GitHub URL")
    path_parts = parsed_url.path.strip("/").split("/")
    if len(path_parts) < 2:
        raise ValueError("Invalid GitHub repository URL")
    return path_parts[0], path_parts[1]


def _version_from_pyproject_text(text: str) -> str:
    pyproject_data = tomllib.loads(text)
    try:
        return pyproject_data["tool"]["poetry"]["version"]
    except KeyError:
        try:
            return pyproject_data["project"]["version"]
        except KeyError as e:
            raise KeyError("Version not found in pyproject.toml") from e


def _raw_pyproject_url(owner: str, repo: str, branch: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/pyproject.toml"


async def get_version_from_github_async(repo_url: str, branch: str = "main") -> str:
    """Fetch and parse the version from pyproject.toml on a public GitHub repo."""
    owner, repo = _github_owner_repo(repo_url)
    raw_url = _raw_pyproject_url(owner, repo, branch)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(raw_url)
        response.raise_for_status()
    return _version_from_pyproject_text(response.text)


def get_version_from_github(repo_url: str, branch: str = "main") -> str:
    """Sync wrapper around URL validation + fetch (for tests / CLI)."""
    owner, repo = _github_owner_repo(repo_url)
    raw_url = _raw_pyproject_url(owner, repo, branch)
    with httpx.Client(timeout=10.0) as client:
        response = client.get(raw_url)
        response.raise_for_status()
    return _version_from_pyproject_text(response.text)


def get_installed_version(package_name: str) -> str:
    """Get the version of an installed package."""
    try:
        return version(package_name)
    except PackageNotFoundError:
        raise PackageNotFoundError(f"Package '{package_name}' not found")


def compare_versions(version1: str, version2: str) -> int:
    """
    Compare two semantic versions.

    Returns:
        -1 if version1 < version2, 0 if equal, 1 if version1 > version2
    """
    v1 = parse_version(version1)
    v2 = parse_version(version2)
    if v1 < v2:
        return -1
    if v1 > v2:
        return 1
    return 0
