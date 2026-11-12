"""Owner-controlled GitHub repository integration.

Use a fine-grained personal access token scoped to selected repositories.
The allowlist is an additional application-level guard; writes never delete files.
"""
import base64
import httpx
from . import config

class GitHubError(Exception):
    pass

def _headers():
    if not config.GITHUB_TOKEN:
        raise GitHubError("GitHub is not configured. Add GITHUB_TOKEN to your deployment environment.")
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def _check_repo(repo: str):
    repo = (repo or "").strip().strip("/")
    if repo.count("/") != 1 or any(part in ("", ".", "..") for part in repo.split("/")):
        raise GitHubError("Repository must use owner/name format.")
    allowed = config.GITHUB_ALLOWED_REPOS
    if not allowed or ( "*" not in allowed and repo.lower() not in allowed):
        raise GitHubError("This repository is not in GITHUB_ALLOWED_REPOS. Add it to the allowlist and ensure your token is scoped to it.")
    return repo

async def _request(method: str, path: str, **kwargs):
    headers = _headers()
    headers.update(kwargs.pop("headers", {}))
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=8), follow_redirects=False) as client:
            response = await client.request(method, "https://api.github.com" + path, headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        raise GitHubError(f"GitHub request failed: {str(exc)[:180]}") from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("message", "GitHub API error")
        except ValueError:
            detail = "GitHub API error"
        raise GitHubError(f"GitHub HTTP {response.status_code}: {str(detail)[:250]}")
    if response.status_code == 204:
        return {}
    return response.json()

async def list_repositories() -> list[dict]:
    if not config.GITHUB_TOKEN:
        raise GitHubError("GitHub is not configured. Add GITHUB_TOKEN.")
    data = await _request("GET", "/user/repos", params={"per_page": 100, "sort": "updated", "affiliation": "owner,collaborator,organization_member"})
    allowed = config.GITHUB_ALLOWED_REPOS
    return [
        {"full_name": item["full_name"], "private": item["private"], "default_branch": item["default_branch"], "html_url": item["html_url"]}
        for item in data
        if "*" in allowed or item["full_name"].lower() in allowed
    ]

async def read_file(repo: str, path: str, ref: str | None = None) -> dict:
    repo = _check_repo(repo)
    safe_path = "/".join(part for part in (path or "").split("/") if part not in ("", ".", ".."))
    if not safe_path:
        raise GitHubError("Provide a repository file path.")
    params = {"ref": ref} if ref else {}
    item = await _request("GET", f"/repos/{repo}/contents/{safe_path}", params=params)
    if item.get("type") != "file":
        raise GitHubError("The requested path is not a file.")
    if item.get("encoding") == "base64":
        content = base64.b64decode(item.get("content", "")).decode("utf-8", errors="replace")
    else:
        content = item.get("content", "")
    return {"content": content, "sha": item.get("sha", ""), "html_url": item.get("html_url", ""), "size": item.get("size", 0)}

async def search_code(repo: str, query: str) -> list[dict]:
    repo = _check_repo(repo)
    data = await _request("GET", "/search/code", params={"q": f"{query[:180]} repo:{repo}", "per_page": 10})
    return [{"path": item.get("path", ""), "html_url": item.get("html_url", ""), "name": item.get("name", "")} for item in data.get("items", [])]

async def write_file(repo: str, path: str, content: str, message: str, branch: str | None = None) -> dict:
    """Create or update a text file. Caller must be the authenticated bot owner."""
    repo = _check_repo(repo)
    safe_path = "/".join(part for part in (path or "").split("/") if part not in ("", ".", ".."))
    if not safe_path:
        raise GitHubError("Provide a repository file path.")
    if len(content.encode("utf-8")) > 500_000:
        raise GitHubError("File is too large (500 KB maximum for this bot command).")
    if not message.strip():
        raise GitHubError("Provide a commit message.")
    params = {"ref": branch} if branch else {}
    existing = await _request("GET", f"/repos/{repo}/contents/{safe_path}", params=params)
    body = {
        "message": message[:200],
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "sha": existing.get("sha"),
    }
    if branch:
        body["branch"] = branch
    result = await _request("PUT", f"/repos/{repo}/contents/{safe_path}", json=body)
    commit = result.get("commit", {})
    return {"commit_sha": commit.get("sha", ""), "html_url": result.get("content", {}).get("html_url", "")}
