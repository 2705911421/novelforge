"""Safe import of standard ``SKILL.md`` packages.

The importer accepts a GitHub repository/blob/tree/release URL or an already
downloaded archive/folder.  It only parses Markdown/YAML and stores reference
metadata; scripts, hooks, package managers, and arbitrary commands are never
executed.
"""

from __future__ import annotations

import base64
import io
import posixpath
import re
import tarfile
import zipfile
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

import httpx
import yaml


MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_PACKAGE_FILES = 512
MAX_REFERENCE_BYTES = 700_000
MAX_REFERENCE_FILE_BYTES = 120_000
ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz")
TEXT_REFERENCE_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".csv"}
ALLOWED_GITHUB_HOSTS = {
    "github.com",
    "www.github.com",
    "raw.githubusercontent.com",
    "api.github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
    "github-releases.githubusercontent.com",
}


class SkillImportError(ValueError):
    """A downloaded or uploaded Skill package is unsafe or invalid."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SkillPackage:
    name: str
    key: str
    description: str
    instructions: str
    manifest: dict[str, Any]
    config: dict[str, Any]
    origin: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "key": self.key,
            "description": self.description,
            "instructions": self.instructions,
            "definition": self.manifest,
            "config": self.config,
            "source": "github" if self.origin.startswith("http") else "imported",
            "enabled": True,
        }


def _skill_key(name: str) -> str:
    value = re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-._")
    return (value or "imported-skill")[:64]


def _safe_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise SkillImportError("SKILL_PATH_INVALID", "skill package contains an invalid path")
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise SkillImportError("SKILL_PATH_INVALID", "skill package paths must be relative")
    normalized = posixpath.normpath(normalized)
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise SkillImportError("SKILL_PATH_INVALID", "skill package path escapes its root")
    if len(normalized) > 240:
        raise SkillImportError("SKILL_PATH_INVALID", "skill package path is too long")
    return normalized


def _decode_utf8(payload: bytes, path: str) -> str:
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SkillImportError("SKILL_MANIFEST_ENCODING", f"{path} must be UTF-8 Markdown") from exc


def parse_skill_files(files: Mapping[str, bytes] | Iterable[tuple[str, bytes]], *, origin: str = "local") -> SkillPackage:
    """Parse one folder/archive worth of files into a durable Skill payload."""
    if isinstance(files, Mapping):
        entries = list(files.items())
    else:
        entries = list(files)
    if not entries:
        raise SkillImportError("SKILL_PACKAGE_EMPTY", "skill package is empty")
    normalized: dict[str, bytes] = {}
    total_bytes = 0
    for raw_path, payload in entries:
        path = _safe_path(str(raw_path))
        if not isinstance(payload, (bytes, bytearray)):
            raise SkillImportError("SKILL_FILE_INVALID", f"skill file {path} is not binary content")
        data = bytes(payload)
        total_bytes += len(data)
        if total_bytes > MAX_DOWNLOAD_BYTES:
            raise SkillImportError("SKILL_PACKAGE_TOO_LARGE", "skill package exceeds the 50 MiB limit")
        normalized[path] = data
    manifests = [path for path in normalized if path.lower().endswith("/skill.md") or path.lower() == "skill.md"]
    root_manifests = [path for path in manifests if path.lower() == "skill.md"]
    if root_manifests:
        manifest_path = root_manifests[0]
    elif len(manifests) == 1:
        manifest_path = manifests[0]
    else:
        raise SkillImportError(
            "SKILL_MANIFEST_AMBIGUOUS",
            "package must contain one root SKILL.md (or exactly one nested SKILL.md)",
        )
    if len(manifests) > 1 and manifest_path not in root_manifests:
        raise SkillImportError("SKILL_MANIFEST_AMBIGUOUS", "package contains multiple SKILL.md files")

    raw_markdown = _decode_utf8(normalized[manifest_path], manifest_path)
    manifest: dict[str, Any] = {}
    body = raw_markdown
    if raw_markdown.startswith("---"):
        lines = raw_markdown.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            raise SkillImportError("SKILL_MANIFEST_INVALID", "SKILL.md front matter is invalid")
        closing = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
        if closing is None:
            raise SkillImportError("SKILL_MANIFEST_INVALID", "SKILL.md front matter is not closed")
        try:
            parsed = yaml.safe_load("".join(lines[1:closing])) or {}
        except yaml.YAMLError as exc:
            raise SkillImportError("SKILL_MANIFEST_INVALID", "SKILL.md YAML front matter is invalid") from exc
        if not isinstance(parsed, dict):
            raise SkillImportError("SKILL_MANIFEST_INVALID", "SKILL.md front matter must be an object")
        manifest = parsed
        body = "".join(lines[closing + 1:])
    name = manifest.get("name")
    description = manifest.get("description")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise SkillImportError("SKILL_NAME_REQUIRED", "SKILL.md must define a short name")
    if not isinstance(description, str) or not description.strip() or len(description.strip()) > 4_000:
        raise SkillImportError("SKILL_DESCRIPTION_REQUIRED", "SKILL.md must define a description")
    body = body.strip()
    if not body:
        raise SkillImportError("SKILL_INSTRUCTIONS_EMPTY", "SKILL.md instructions are empty")
    if len(body) > 200_000:
        raise SkillImportError("SKILL_INSTRUCTIONS_TOO_LARGE", "SKILL.md instructions exceed 200000 characters")

    reference_files: list[dict[str, Any]] = []
    reference_bytes = 0
    prefix = posixpath.dirname(manifest_path)
    for path, data in sorted(normalized.items()):
        if path == manifest_path or (prefix and not (path == prefix or path.startswith(prefix + "/"))):
            continue
        suffix = posixpath.splitext(path.lower())[1]
        item: dict[str, Any] = {
            "path": path,
            "size": len(data),
            "sha256": sha256(data).hexdigest(),
        }
        if suffix in TEXT_REFERENCE_SUFFIXES and len(data) <= MAX_REFERENCE_FILE_BYTES and reference_bytes + len(data) <= MAX_REFERENCE_BYTES:
            item["content"] = _decode_utf8(data, path)
            reference_bytes += len(data)
        reference_files.append(item)
    config = {
        "import": {
            "origin": origin,
            "manifestPath": manifest_path,
            "files": [{"path": path, "size": len(data), "sha256": sha256(data).hexdigest()} for path, data in sorted(normalized.items())],
            "referenceFiles": reference_files,
            "scriptsExecuted": False,
        }
    }
    return SkillPackage(
        name=name.strip(),
        key=_skill_key(str(manifest.get("key") or name)),
        description=description.strip(),
        instructions=body,
        manifest=manifest,
        config=config,
        origin=origin,
    )


def _archive_files(payload: bytes, filename: str) -> dict[str, bytes]:
    lower = filename.lower()
    try:
        if lower.endswith(".zip") or payload[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                infos = [info for info in archive.infolist() if not info.is_dir()]
                if len(infos) > MAX_PACKAGE_FILES:
                    raise SkillImportError("SKILL_PACKAGE_TOO_MANY_FILES", "skill package contains too many files")
                result: dict[str, bytes] = {}
                for info in infos:
                    path = _safe_path(info.filename)
                    if info.file_size > MAX_DOWNLOAD_BYTES or info.file_size > MAX_REFERENCE_BYTES * 4:
                        raise SkillImportError("SKILL_FILE_TOO_LARGE", f"skill file {path} is too large")
                    result[path] = archive.read(info)
                return result
        if lower.endswith(ARCHIVE_SUFFIXES[1:]) or lower.endswith(".gz"):
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
                members = [member for member in archive.getmembers() if member.isfile()]
                if len(members) > MAX_PACKAGE_FILES:
                    raise SkillImportError("SKILL_PACKAGE_TOO_MANY_FILES", "skill package contains too many files")
                result = {}
                for member in members:
                    path = _safe_path(member.name)
                    if member.size > MAX_DOWNLOAD_BYTES or member.size > MAX_REFERENCE_BYTES * 4:
                        raise SkillImportError("SKILL_FILE_TOO_LARGE", f"skill file {path} is too large")
                    handle = archive.extractfile(member)
                    if handle is None:
                        continue
                    result[path] = handle.read()
                return result
    except SkillImportError:
        raise
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise SkillImportError("SKILL_ARCHIVE_INVALID", "skill package archive is invalid") from exc
    raise SkillImportError("SKILL_PACKAGE_FORMAT", "expected SKILL.md, ZIP, TAR, TGZ, or TAR.GZ")


def parse_skill_upload(payload: bytes, filename: str, *, origin: str = "local") -> SkillPackage:
    if filename.lower().endswith("skill.md") or filename.lower().endswith(".md"):
        return parse_skill_files({"SKILL.md": payload}, origin=origin)
    return parse_skill_files(_archive_files(payload, filename), origin=origin)


async def _download(client: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_GITHUB_HOSTS:
        raise SkillImportError("SKILL_GITHUB_HOST_INVALID", "only HTTPS GitHub URLs are supported")
    try:
        response = await client.get(url, headers={"User-Agent": "NovelForge-Skill-Importer/1.0"})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SkillImportError("SKILL_GITHUB_DOWNLOAD_FAILED", "GitHub resource could not be downloaded") from exc
    if len(response.content) > MAX_DOWNLOAD_BYTES:
        raise SkillImportError("SKILL_PACKAGE_TOO_LARGE", "GitHub package exceeds the 50 MiB limit")
    final_host = urlsplit(str(response.url)).hostname
    if final_host not in ALLOWED_GITHUB_HOSTS:
        raise SkillImportError("SKILL_GITHUB_REDIRECT_INVALID", "GitHub download redirected outside the allow-list")
    filename = posixpath.basename(urlsplit(str(response.url)).path) or posixpath.basename(parsed.path) or "SKILL.md"
    return response.content, filename


def _repo_parts(path: str) -> tuple[str, str, list[str]]:
    parts = [item for item in path.split("/") if item]
    if len(parts) < 2:
        raise SkillImportError("SKILL_GITHUB_URL_INVALID", "GitHub URL must include owner and repository")
    return parts[0], parts[1].removesuffix(".git"), parts[2:]


async def import_github_skill(url: str) -> SkillPackage:
    """Download and parse a GitHub blob, repository, tree, or release asset."""
    if not isinstance(url, str) or not url.strip():
        raise SkillImportError("SKILL_GITHUB_URL_INVALID", "GitHub URL is required")
    url = url.strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_GITHUB_HOSTS:
        raise SkillImportError("SKILL_GITHUB_URL_INVALID", "only HTTPS GitHub URLs are supported")
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        host = parsed.hostname or ""
        if host == "raw.githubusercontent.com":
            payload, filename = await _download(client, url)
            return parse_skill_upload(payload, filename, origin=url)
        if host == "codeload.github.com":
            payload, filename = await _download(client, url)
            return parse_skill_upload(payload, filename or "repository.zip", origin=url)
        owner, repo, rest = _repo_parts(parsed.path)
        if rest[:1] == ["blob"] and len(rest) >= 3:
            ref = rest[1]
            file_path = "/".join(rest[2:])
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{file_path}"
            payload, filename = await _download(client, raw_url)
            return parse_skill_upload(payload, filename, origin=url)
        if rest[:1] == ["releases"] and len(rest) >= 3 and rest[1] == "download":
            payload, filename = await _download(client, url)
            return parse_skill_upload(payload, filename, origin=url)
        if rest[:2] == ["releases", "tag"] and len(rest) >= 3:
            tag = "/".join(rest[2:])
            release_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
            response = await client.get(release_url, headers={"User-Agent": "NovelForge-Skill-Importer/1.0"})
            if urlsplit(str(getattr(response, "url", release_url))).hostname not in ALLOWED_GITHUB_HOSTS:
                raise SkillImportError("SKILL_GITHUB_REDIRECT_INVALID", "GitHub API redirected outside the allow-list")
            if response.status_code >= 400:
                raise SkillImportError("SKILL_RELEASE_NOT_FOUND", "GitHub release could not be found")
            release = response.json()
            assets = release.get("assets") if isinstance(release, dict) else None
            asset = next((item for item in assets or [] if str(item.get("name", "")).lower().endswith(ARCHIVE_SUFFIXES)), None)
            if not asset or not asset.get("browser_download_url"):
                raise SkillImportError("SKILL_RELEASE_PACKAGE_MISSING", "release has no supported Skill package asset")
            payload, filename = await _download(client, str(asset["browser_download_url"]))
            return parse_skill_upload(payload, filename, origin=url)
        tree_path = ""
        if rest[:1] == ["tree"] and len(rest) >= 2:
            ref = rest[1]
            tree_path = "/".join(rest[2:]).strip("/")
        else:
            repo_api = f"https://api.github.com/repos/{owner}/{repo}"
            response = await client.get(repo_api, headers={"User-Agent": "NovelForge-Skill-Importer/1.0"})
            if urlsplit(str(getattr(response, "url", repo_api))).hostname not in ALLOWED_GITHUB_HOSTS:
                raise SkillImportError("SKILL_GITHUB_REDIRECT_INVALID", "GitHub API redirected outside the allow-list")
            if response.status_code >= 400:
                raise SkillImportError("SKILL_REPOSITORY_NOT_FOUND", "GitHub repository could not be found")
            info = response.json()
            ref = str(info.get("default_branch") or "main")
        archive_url = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{ref}"
        try:
            payload, filename = await _download(client, archive_url)
        except SkillImportError:
            if ref != "master":
                payload, filename = await _download(client, f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/master")
            else:
                raise
        if tree_path:
            archive_files = _archive_files(payload, filename or "repository.zip")
            marker = "/" + tree_path + "/"
            matches = {}
            for path, data in archive_files.items():
                position = path.find(marker)
                if position < 0:
                    continue
                relative = path[position + len(marker):]
                if relative:
                    matches[relative] = data
            if not matches:
                raise SkillImportError("SKILL_MANIFEST_MISSING", "the selected GitHub folder contains no SKILL.md")
            return parse_skill_files(matches, origin=url)
        return parse_skill_upload(payload, filename or "repository.zip", origin=url)


def decode_data_url(value: str) -> bytes:
    """Decode a browser-provided base64 data URL with a strict size cap."""
    if not isinstance(value, str) or "," not in value:
        raise SkillImportError("SKILL_FILE_INVALID", "folder upload content must be a data URL")
    header, encoded = value.split(",", 1)
    if ";base64" not in header.lower():
        raise SkillImportError("SKILL_FILE_INVALID", "folder upload content must be base64")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise SkillImportError("SKILL_FILE_INVALID", "folder upload content is not valid base64") from exc
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise SkillImportError("SKILL_FILE_TOO_LARGE", "uploaded Skill file is too large")
    return data


__all__ = [
    "SkillImportError",
    "SkillPackage",
    "decode_data_url",
    "import_github_skill",
    "parse_skill_files",
    "parse_skill_upload",
]
