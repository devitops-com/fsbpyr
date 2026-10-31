"""A GitHub for the tests of release-sign.sh and audit.sh: a fake `gh` and `curl` over files, and a
bare repository as the remote `hub`. It holds what an account can change, attestations apart: those
are GitHub's own word, which only ``attest`` here gives, as only a workflow run can there."""

import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

FAKE_GH = """#!{python}
import hashlib, json, os, pathlib, shutil, subprocess, sys

root = pathlib.Path(os.environ["FAKE_GITHUB"])
state = json.loads((root / "state.json").read_text())
with (root / "log").open("a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")


def fail(message, status=1):
    print(message, file=sys.stderr)
    raise SystemExit(status)


def save():
    (root / "state.json").write_text(json.dumps(state))


def option(name):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None


def document(tag):
    assets = []
    release = state["releases"][tag]
    for path in sorted((root / "assets" / tag).iterdir()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assets.append({{"name": path.name, "digest": f"sha256:{{digest}}"}})
    return {{"tag_name": tag, "id": release["id"], "draft": release["draft"],
             "isDraft": release.get("isDraft", release["draft"]),
             "prerelease": release.get("prerelease", False), "target_commitish": "main",
             "name": release.get("name", tag), "body": release.get("body", "notes"),
             "immutable": release["immutable"], "assets": assets}}


def documents():
    return [document(tag) for tag in state["releases"]]


def answer(body):
    expression = option("--jq")
    if expression is None:
        print(json.dumps(body))
        return
    subprocess.run(["jq", "-c", "-r", expression], input=json.dumps(body), text=True, check=True)


command = " ".join(sys.argv[1:3])
if command == "auth status":
    raise SystemExit(0 if state.get("logged_in", True) else 1)
if command == "api" or sys.argv[1] == "api":
    path = next(a for a in sys.argv[2:] if a.startswith(("repos/", "orgs/")))
    if any(broken in f"{{path}}?" for broken in state.get("broken", [])):
        fail(f"gh: Server Error (HTTP 500) for {{path}}")
    tail = path.split("?")[0].removeprefix("repos/devitops-com/fsbpyr")
    if tail == "/releases":
        listed = documents()
        answer(listed + [dict(d) for d in listed if d["tag_name"] in state.get("second_draft", [])])
    elif tail.startswith("/releases/") and "-X" in sys.argv:  # PATCH, to publish by id
        wanted = tail.rpartition("/")[2]
        tag = next(tag for tag, release in state["releases"].items()
                   if str(release["id"]) == wanted)  # fmt: skip
        assert "draft=false" in sys.argv
        for name in state.get("swapped_while_publishing", []):
            (root / "assets" / tag / name).write_bytes(b"swapped")
        state["releases"][tag].update(draft=False, immutable=True)
        if "make_latest=true" in sys.argv:
            state["latest"] = tag
        save()
    elif "/actions/runs/" in tail:
        answer({{"jobs": state["runs"][tail.split("/")[3]]}})
    elif tail == "/releases/latest":
        answer({{"tag_name": state["latest"]}})
    elif tail.startswith("/releases/tags/"):
        answer(document(tail.rpartition("/")[2]))
    else:
        answer(state["settings"][path.split("?")[0]])
elif command == "release view":
    tag = sys.argv[3]
    if tag in state.get("view_fails", []):
        fail("gh: Server Error (HTTP 500)")
    if tag not in state["releases"]:
        fail("release not found")
    answer(document(tag))
elif command == "release download":
    tag, directory = sys.argv[3], pathlib.Path(option("--dir"))
    patterns = [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--pattern"]
    directory.mkdir(parents=True)
    for path in (root / "assets" / tag).iterdir():
        if not patterns or path.name in patterns:
            shutil.copy2(path, directory / path.name)
    if not patterns and state.get("after_download"):
        subprocess.run(state["after_download"], shell=True, check=True)
elif command == "release upload":
    tag, file = sys.argv[3], pathlib.Path(sys.argv[4])
    if not state["releases"][tag]["draft"]:
        fail("HTTP 422: Cannot upload assets to an immutable release.")
    shutil.copy2(file, root / "assets" / tag / file.name)
elif command == "attestation verify":
    digest = hashlib.sha256(pathlib.Path(sys.argv[3]).read_bytes()).hexdigest()
    made = state["attestations"].get(digest)
    if made is None:
        fail("no attestation")
    workflow = option("--signer-workflow").partition("/.github/workflows/")[2]
    good = (made["workflow"] == workflow and made["ref"] == option("--source-ref")
            and made["commit"] == option("--source-digest")
            and option("--repo") == "devitops-com/fsbpyr"
            and ("--deny-self-hosted-runners" in sys.argv and not made["self_hosted"]))
    if not good:
        raise SystemExit(1)
    repository = made.get("repository", "devitops-com/fsbpyr")
    run = f"https://github.com/{{repository}}/actions/runs/{{made['run']}}/attempts/1"
    certificate = {{"runInvocationURI": run}}
    entry = {{"verificationResult": {{"signature": {{"certificate": certificate}}}}}}
    print(json.dumps([entry] * made.get("count", 1)))
else:
    fail(f"fake gh: no answer for {{sys.argv[1:]}}", 64)
"""
FAKE_CURL = """#!/bin/sh
exec gh api "repos/devitops-com/fsbpyr/releases?per_page=100"
"""
ORGANIZATION = "orgs/devitops-com"
REPOSITORY = "repos/devitops-com/fsbpyr"


def settings() -> dict[str, Any]:
    """Every endpoint audit.sh reads, as a repository looks that nobody tampered with."""
    return {
        REPOSITORY: {"visibility": "public", "default_branch": "main", "archived": False,
                     "has_wiki": True},
        f"{REPOSITORY}/collaborators": [{"login": "admin", "role_name": "admin"}],
        f"{ORGANIZATION}/members": [{"login": "admin"}],
        f"{ORGANIZATION}/installations": {"installations": []},
        f"{REPOSITORY}/hooks": [],
        f"{REPOSITORY}/keys": [],
        f"{REPOSITORY}/rulesets": [{"id": 1}],
        f"{REPOSITORY}/rulesets/1": {"name": "release tags", "target": "tag",
                                     "enforcement": "active", "bypass_actors": [],
                                     "conditions": {}, "rules": []},
        f"{REPOSITORY}/actions/secrets": {"secrets": []},
        f"{ORGANIZATION}/actions/secrets": {"secrets": []},
        f"{REPOSITORY}/actions/variables": {"variables": []},
        f"{ORGANIZATION}/actions/variables": {"variables": []},
        f"{REPOSITORY}/environments": {"environments": []},
        f"{REPOSITORY}/actions/runners": {"runners": []},
        f"{ORGANIZATION}/actions/runners": {"runners": []},
        f"{REPOSITORY}/actions/permissions": {"enabled": True, "allowed_actions": "all"},
        f"{REPOSITORY}/actions/permissions/workflow": {
            "default_workflow_permissions": "read", "can_approve_pull_request_reviews": False},
        f"{REPOSITORY}/immutable-releases": {"enabled": True},
    }  # fmt: skip


class FakeGitHub:
    """The state behind the fake programs. Whoever holds the account can do all of this."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "assets").mkdir(parents=True)
        (root / "bin").mkdir()
        self.state: dict[str, Any] = {"latest": None, "releases": {}, "attestations": {},
                                      "settings": settings(), "broken": [], "runs": {}}  # fmt: skip
        self.save()
        self._program("gh", FAKE_GH.format(python=sys.executable))
        self._program("curl", FAKE_CURL)

    def _program(self, name: str, text: str) -> None:
        path = self.root / "bin" / name
        path.write_text(text)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def save(self) -> None:
        (self.root / "state.json").write_text(json.dumps(self.state))

    def load(self) -> None:
        self.state = json.loads((self.root / "state.json").read_text())

    def env(self) -> dict[str, str]:
        return {"FAKE_GITHUB": str(self.root),
                "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}"}  # fmt: skip

    def draft(self, tag: str, files: dict[str, bytes]) -> None:
        self.load()
        directory = self.root / "assets" / tag
        directory.mkdir()
        for name, content in files.items():
            (directory / name).write_bytes(content)
        self.state["releases"][tag] = {"id": len(self.state["releases"]) + 100, "draft": True,
                                       "immutable": False}  # fmt: skip
        self.save()

    def asset(self, tag: str, name: str) -> Path:
        return self.root / "assets" / tag / name

    def attest(self, content: bytes, workflow: str, ref: str, commit: str,
               *, self_hosted: bool = False) -> None:  # fmt: skip
        """What only a run of ``workflow`` on GitHub can make."""
        self.load()
        digest = hashlib.sha256(content).hexdigest()
        run = str(len(self.state["runs"]) + 1)
        self.state["runs"][run] = [{"name": "build", "conclusion": "success", "runner_group_id": 0},
                                   {"name": "draft", "conclusion": "success", "runner_group_id": 0}]
        self.state["attestations"][digest] = {"workflow": workflow, "ref": ref, "commit": commit,
                                              "self_hosted": self_hosted, "run": run}  # fmt: skip
        self.save()

    def calls(self) -> list[str]:
        log = self.root / "log"
        return log.read_text().splitlines() if log.exists() else []

    def writes(self) -> list[str]:
        """The calls that change a release."""
        return [call for call in self.calls()
                if call.startswith(("release upload", "release edit", "api -X"))]  # fmt: skip
