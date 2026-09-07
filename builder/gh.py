"""A small GitHub REST client on urllib (no dependencies) plus the handful
of semantic calls the builder needs. Errors carry the status code and the
path so the caller can log them after redaction."""
import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request


class GitHubError(Exception):
    def __init__(self, status: int, path: str, message: str = ""):
        super().__init__(f"HTTP {status} on {path}: {message}".strip())
        self.status = status
        self.path = path


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHub:
    def __init__(self, token: str, api_url: str = "https://api.github.com"):
        if not token:
            raise ValueError("a GitHub token is required")
        self.token = token
        self.api_url = api_url.rstrip("/")
        self._opener = urllib.request.build_opener()
        self._no_redirect = urllib.request.build_opener(_NoRedirect)
        self.calls = 0

    # --- low level -----------------------------------------------------
    def request(self, method: str, path: str, params: dict | None = None, body=None,
                accept: str = "application/vnd.github+json", raw: bool = False,
                follow: bool = True):
        url = path if path.startswith("http") else self.api_url + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", accept)
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "ese6150-leaderboard")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        opener = self._opener if follow else self._no_redirect
        for attempt in range(3):
            self.calls += 1
            try:
                with opener.open(req, timeout=60) as resp:
                    payload = resp.read()
                    return resp.status, dict(resp.headers), (payload if raw else _decode(payload))
            except urllib.error.HTTPError as err:
                if err.code in (301, 302, 307) and not follow:
                    return err.code, dict(err.headers), None
                if err.code in (403, 429) and attempt < 2 and _is_rate_limited(err):
                    time.sleep(_retry_delay(err))
                    continue
                if err.code >= 500 and attempt < 2:
                    time.sleep(2 + 3 * attempt)
                    continue
                message = _error_message(err)
                raise GitHubError(err.code, _strip_host(url), message) from None
            except urllib.error.URLError as err:
                if attempt < 2:
                    time.sleep(2 + 3 * attempt)
                    continue
                raise GitHubError(0, _strip_host(url), str(err.reason)) from None
        raise GitHubError(0, _strip_host(url), "gave up")

    def get(self, path: str, params: dict | None = None):
        return self.request("GET", path, params)[2]

    def get_all(self, path: str, params: dict | None = None) -> list:
        params = dict(params or {})
        params.setdefault("per_page", 100)
        out, page = [], 1
        while True:
            params["page"] = page
            status, headers, data = self.request("GET", path, params)
            if not isinstance(data, list):
                raise GitHubError(status, path, "expected a list")
            out.extend(data)
            if len(data) < params["per_page"] or 'rel="next"' not in headers.get("Link", ""):
                return out
            page += 1

    def put(self, path: str, body=None):
        return self.request("PUT", path, body=body)[2]

    def post(self, path: str, body=None):
        return self.request("POST", path, body=body)[2]

    def patch(self, path: str, body=None):
        return self.request("PATCH", path, body=body)[2]

    # --- semantic calls -------------------------------------------------
    def file_text(self, repo: str, path: str, ref: str = "main") -> str:
        data = self.get(f"/repos/{repo}/contents/{path}", {"ref": ref})
        if data.get("encoding") != "base64":
            raise GitHubError(200, path, "unexpected content encoding")
        return base64.b64decode(data["content"]).decode("utf-8")

    def org_repos(self, org: str) -> list:
        return [r["name"] for r in self.get_all(f"/orgs/{org}/repos", {"type": "all"})]

    def team_members(self, org: str, team_slug: str) -> list:
        return [m["login"] for m in self.get_all(f"/orgs/{org}/teams/{team_slug}/members")]

    def releases(self, repo: str) -> list:
        return self.get_all(f"/repos/{repo}/releases")

    def tag_refs(self, repo: str, prefix: str = "submit/") -> list:
        """[{name, sha}] for every tag under `prefix` (lightweight tags point
        straight at the commit, which is what the runner creates)."""
        refs = self.get_all(f"/repos/{repo}/git/matching-refs/tags/{prefix}")
        return [{"name": r["ref"][len("refs/tags/"):], "sha": (r.get("object") or {}).get("sha", "")}
                for r in refs if r.get("ref", "").startswith("refs/tags/" + prefix)]

    def delete_tag(self, repo: str, tag: str) -> None:
        self.request("DELETE", f"/repos/{repo}/git/refs/tags/{tag}")

    def download_asset(self, asset_url: str) -> bytes:
        """Release assets redirect to a signed storage URL that must be
        fetched WITHOUT the GitHub token, so follow that hop by hand."""
        status, headers, data = self.request("GET", asset_url, accept="application/octet-stream",
                                             raw=True, follow=False)
        if status in (301, 302, 307):
            location = headers.get("Location") or headers.get("location")
            req = urllib.request.Request(location, headers={"User-Agent": "ese6150-leaderboard"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        return data

    def workflow_runs(self, repo: str) -> list:
        data = self.get(f"/repos/{repo}/actions/runs", {"per_page": 100})
        return data.get("workflow_runs", [])

    def feedback_pr(self, repo: str, base: str = "feedback"):
        pulls = self.get(f"/repos/{repo}/pulls", {"state": "open", "base": base, "per_page": 5})
        return pulls[0]["number"] if pulls else None

    def issue_comments(self, repo: str, number: int) -> list:
        return self.get_all(f"/repos/{repo}/issues/{number}/comments")

    def create_comment(self, repo: str, number: int, body: str) -> int:
        return self.post(f"/repos/{repo}/issues/{number}/comments", {"body": body})["id"]

    def update_comment(self, repo: str, comment_id: int, body: str) -> None:
        self.patch(f"/repos/{repo}/issues/comments/{comment_id}", {"body": body})

    def create_issue(self, repo: str, title: str, body: str) -> int:
        return self.post(f"/repos/{repo}/issues", {"title": title, "body": body})["number"]

    def update_issue(self, repo: str, number: int, body: str) -> None:
        self.patch(f"/repos/{repo}/issues/{number}", {"body": body})

    def permission(self, repo: str, username: str) -> str:
        data = self.get(f"/repos/{repo}/collaborators/{username}/permission")
        return data.get("role_name") or data.get("permission") or ""

    def set_permission(self, repo: str, username: str, permission: str) -> None:
        self.put(f"/repos/{repo}/collaborators/{username}", {"permission": permission})


def _decode(payload: bytes):
    if not payload:
        return None
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return payload


def _strip_host(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return parsed.path


def _error_message(err: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(err.read().decode("utf-8"))
        return str(body.get("message", ""))[:200]
    except Exception:
        return ""


def _is_rate_limited(err: urllib.error.HTTPError) -> bool:
    remaining = err.headers.get("X-RateLimit-Remaining")
    return remaining == "0" or err.headers.get("Retry-After") is not None


def _retry_delay(err: urllib.error.HTTPError) -> float:
    retry_after = err.headers.get("Retry-After")
    if retry_after and retry_after.isdigit():
        return min(int(retry_after), 120)
    reset = err.headers.get("X-RateLimit-Reset")
    if reset and reset.isdigit():
        return max(1.0, min(float(reset) - time.time() + 1, 120))
    return 30.0
