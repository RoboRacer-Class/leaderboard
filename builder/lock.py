"""Locking a repo after its last allowed submission: run the class's own
lab-access.sh with a single-user filter, then verify through the API and
fall back to the direct permission call if the script could not do it."""
import os
import subprocess
import tempfile


def lock_repo(api, org: str, classroom: str, slug: str, username: str, token: str,
              script_text: str | None, run=subprocess.run) -> tuple[bool, str]:
    repo = f"{org}/{classroom}-{slug}-{username}"
    how = "script"
    if script_text:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "lab-access.sh")
            with open(path, "w") as fh:
                fh.write(script_text)
            env = dict(os.environ, GH_TOKEN=token, ORG=org, CLASSROOM=classroom)
            try:
                run(["bash", path, "lock", slug, "--user", username], env=env,
                    capture_output=True, text=True, timeout=300, check=False)
            except (OSError, subprocess.SubprocessError):
                pass
    if _is_read_only(api, repo, username):
        return True, how
    how = "api"
    try:
        api.set_permission(repo, username, "pull")
    except Exception:
        return False, how
    return _is_read_only(api, repo, username), how


def unlock_repo(api, org: str, classroom: str, slug: str, username: str) -> bool:
    repo = f"{org}/{classroom}-{slug}-{username}"
    api.set_permission(repo, username, "push")
    return api.permission(repo, username) in ("write", "push")


def _is_read_only(api, repo: str, username: str) -> bool:
    try:
        return api.permission(repo, username) in ("read", "pull", "triage")
    except Exception:
        return False
