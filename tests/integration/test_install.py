"""install.sh against a local HTTPS server: nothing reaches GitHub, nothing touches the real home.

The installer is made from tools/release/install.sh.in by tools/release/make-install.sh out of a
directory of fake release assets (shell scripts that answer `inspect --verify`, `man` and
`completion` where a real fsbpyr is not needed), served over TLS from 127.0.0.1 with the
self-signed certificate in tests/fixtures/install/ (a test key, valid for 100 years, trusted by
nothing but these tests). curl and wget are pointed at it with CURL_CA_BUNDLE and WGETRC, the
installer at the server with its test-only FSBPYR_INSTALLER_BASE_URL. HOME and every install
directory are under ``tmp_path``, and PATH holds only the tools the installer needs, so a test
can leave one out (xz, curl, gh) or fake one (uname, ldd, gh). Every test runs under dash,
bash and busybox sh.
"""

from __future__ import annotations

import hashlib
import lzma
import os
import shutil
import ssl
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
MAKE_INSTALL_SH = ROOT / "tools" / "release" / "make-install.sh"
CERT_DIR = ROOT / "tests" / "fixtures" / "install"
VERSION = "9.9.9"
SCRIPT_TIMEOUT_S = 60.0
BASH = shutil.which("bash") or "/bin/bash"
GNU = "x86_64-linux-gnu"
MUSL = "x86_64-linux-musl"

# What the installer uses besides shell builtins; xz, curl, wget and gh are added per test.
BASE_TOOLS = (
    "awk", "basename", "cat", "chmod", "cp", "cut", "dirname", "grep", "mkdir", "mktemp", "mv",
    "rm", "sed", "sha256sum", "tail", "tr", "uname", "env", "sleep",
)  # fmt: skip

SHELLS = [["dash"], ["bash"], ["busybox", "sh"]]

FAKE_FSBPYR = """\
#!/bin/sh
# {marker}
case "$1" in
    inspect) {inspect} ;;
    man) echo ".TH FSBPYR 1" ;;
    completion) echo "# completion for $2" ;;
    --version) echo {version}-test ;;
esac
"""


@pytest.fixture(params=SHELLS, ids=lambda shell: shell[0] + ("-sh" if len(shell) > 1 else ""))
def shell(request: pytest.FixtureRequest) -> list[str]:
    command: list[str] = request.param
    found = shutil.which(command[0])
    if found is None:
        pytest.skip(f"needs {command[0]}")
    return [found, *command[1:]]


class Server:
    """Serves ``files`` (path -> bytes) over HTTPS and logs the paths asked for."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.requests: list[str] = []
        files, requests = self.files, self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                requests.append(self.path)
                body = files.get(self.path)
                self.send_response(200 if body is not None else 404)
                self.send_header("Content-Length", str(len(body or b"")))
                self.end_headers()
                self.wfile.write(body or b"")

            def log_message(self, *args: object) -> None:
                pass

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(CERT_DIR / "localhost-cert.pem", CERT_DIR / "localhost-key.pem")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.base = f"https://127.0.0.1:{self.server.server_port}"
        threading.Thread(
            target=partial(self.server.serve_forever, poll_interval=0.01), daemon=True
        ).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def server() -> Iterator[Server]:
    running = Server()
    yield running
    running.close()


@dataclass
class Release:
    """Fake assets on disk and on ``server``, and the installer made from them."""

    root: Path
    server: Server
    glibc: dict[str, str] = field(
        default_factory=lambda: {GNU: "2.25", "aarch64-linux-gnu": "2.25"}
    )

    @property
    def assets(self) -> Path:
        return self.root / "assets"

    def add(self, target: str, *, inspect: str = "echo verified") -> None:
        """Both files of a target's executable; the server serves what was written."""
        body = FAKE_FSBPYR.format(marker=target, inspect=inspect, version=VERSION).encode()
        self.assets.mkdir(parents=True, exist_ok=True)
        (self.assets / f"fsbpyr-{target}").write_bytes(body)
        (self.assets / f"fsbpyr-{target}.xz").write_bytes(lzma.compress(body))

    def serve(self) -> None:
        for asset in self.assets.iterdir():
            self.server.files[f"/{asset.name}"] = asset.read_bytes()

    def make(self, *, inspector_glibc: str | None = None) -> Path:
        """The installer, with the glibc each gnu executable records."""
        inspector = self.root / "inspector"
        cases = "".join(
            f'    *{target}) echo \'  "glibc": "{glibc}",\' ;;\n'
            for target, glibc in self.glibc.items()
        )
        inspector.write_text(f'#!/bin/sh\ncase "$3" in\n{cases}esac\n')
        inspector.chmod(0o755)
        out = self.root / "install.sh"
        subprocess.run(
            [BASH, str(MAKE_INSTALL_SH), VERSION, str(self.assets), str(out), str(inspector)],
            check=True,
            capture_output=True,
            timeout=SCRIPT_TIMEOUT_S,
        )
        return out


@pytest.fixture
def release(tmp_path: Path, server: Server) -> Release:
    made = Release(tmp_path / "release", server)
    made.add(GNU)
    made.add("aarch64-linux-gnu")
    made.add(MUSL)
    made.serve()
    return made


class Host:
    """A fake home, a PATH of chosen tools and the environment the installer runs in."""

    def __init__(self, tmp_path: Path, server: Server) -> None:
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.bin = tmp_path / "tools"
        self.bin.mkdir()
        for tool in (*BASE_TOOLS, "xz", "curl"):
            self.link(tool)
        cert = CERT_DIR / "localhost-cert.pem"
        wgetrc = tmp_path / "wgetrc"
        wgetrc.write_text(f"ca_certificate = {cert}\n")
        self.env = {
            "HOME": str(self.home),
            "PATH": str(self.bin),
            "CURL_CA_BUNDLE": str(cert),
            "SSL_CERT_FILE": str(cert),
            "WGETRC": str(wgetrc),
            "FSBPYR_INSTALLER_BASE_URL": server.base,
            "FSBPYR_INSTALL_DIR": str(self.home / ".local" / "bin"),
        }
        self.fake("ldd", 'echo "ldd (Debian GLIBC 2.36-9+deb12u4) 2.36"')
        self.fake("uname", 'case "$1" in -s) echo Linux ;; -m) echo x86_64 ;; esac')

    def link(self, tool: str) -> None:
        found = shutil.which(tool)
        if found is None:
            pytest.skip(f"needs {tool}")
        (self.bin / tool).unlink(missing_ok=True)
        (self.bin / tool).symlink_to(found)

    def drop(self, tool: str) -> None:
        (self.bin / tool).unlink()

    def fake(self, name: str, body: str) -> None:
        path = self.bin / name
        path.unlink(missing_ok=True)
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)

    @property
    def install_dir(self) -> Path:
        return Path(self.env["FSBPYR_INSTALL_DIR"])


@pytest.fixture
def host(tmp_path: Path, server: Server) -> Host:
    return Host(tmp_path, server)


def install(
    shell: list[str], script: Path, host: Host, *args: str, **env: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*shell, str(script), *args],
        env={**host.env, **env},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=SCRIPT_TIMEOUT_S,
        start_new_session=True,
        check=False,
    )


def installed(host: Host) -> Path:
    return host.install_dir / "fsbpyr"


def test_installs_the_xz_when_xz_is_there(
    shell: list[str],
    release: Release,
    host: Host,
    server: Server,
) -> None:
    script = release.make()

    result = install(shell, script, host)

    assert result.returncode == 0, result.stderr
    assert installed(host).read_bytes() == (release.assets / f"fsbpyr-{GNU}").read_bytes()
    assert os.access(installed(host), os.X_OK)
    assert server.requests == [f"/fsbpyr-{GNU}.xz"]
    assert f"downloading fsbpyr {VERSION} {GNU}" in result.stdout
    assert f"installing to {host.install_dir}" in result.stdout
    assert "everything's installed!" in result.stdout
    assert sorted(p.name for p in host.install_dir.iterdir() if p.name.startswith(".")) == []


def test_installs_the_plain_asset_without_xz(
    shell: list[str],
    release: Release,
    host: Host,
    server: Server,
) -> None:
    script = release.make()
    host.drop("xz")

    result = install(shell, script, host)

    assert result.returncode == 0, result.stderr
    assert installed(host).read_bytes() == (release.assets / f"fsbpyr-{GNU}").read_bytes()
    assert server.requests == [f"/fsbpyr-{GNU}"]


def test_installs_with_wget_when_there_is_no_curl(
    shell: list[str],
    release: Release,
    host: Host,
    server: Server,
) -> None:
    if shutil.which("wget") is None:
        pytest.skip("needs wget")
    script = release.make()
    host.drop("curl")
    host.link("wget")

    result = install(shell, script, host)

    assert result.returncode == 0, result.stderr
    assert server.requests == [f"/fsbpyr-{GNU}.xz"]
    assert installed(host).exists()


def test_stops_without_curl_or_wget(shell: list[str], release: Release, host: Host) -> None:
    script = release.make()
    host.drop("curl")

    result = install(shell, script, host)

    assert result.returncode == 1
    assert "need curl or wget" in result.stderr
    assert not installed(host).exists()


def test_a_plain_http_url_is_refused(
    shell: list[str],
    release: Release,
    host: Host,
    server: Server,
) -> None:
    script = release.make()

    result = install(
        shell, script, host, FSBPYR_INSTALLER_BASE_URL=server.base.replace("https", "http")
    )

    assert result.returncode == 1
    assert "only https" in result.stderr
    assert server.requests == []


def test_a_server_the_certificate_does_not_cover_is_refused(
    shell: list[str],
    release: Release,
    host: Host,
    server: Server,
) -> None:
    script = release.make()
    host.env["CURL_CA_BUNDLE"] = str(release.root / "no-such-bundle")
    host.env["SSL_CERT_FILE"] = str(release.root / "no-such-bundle")

    result = install(shell, script, host)

    assert result.returncode == 1
    assert "could not download" in result.stderr
    assert not installed(host).exists()


@pytest.mark.parametrize("compressed", [True, False])
def test_a_checksum_mismatch_stops(
    shell: list[str],
    release: Release,
    host: Host,
    server: Server,
    compressed: bool,
) -> None:
    script = release.make()
    served = f"/fsbpyr-{GNU}.xz" if compressed else f"/fsbpyr-{GNU}"
    if not compressed:
        host.drop("xz")
    server.files[served] = server.files[served][:-1] + b"X"

    result = install(shell, script, host)

    assert result.returncode == 1
    assert "checksum mismatch" in result.stderr
    assert not installed(host).exists()
    assert list(host.install_dir.iterdir()) == []


def test_an_executable_that_fails_inspect_verify_is_not_installed(
    shell: list[str],
    release: Release,
    host: Host,
) -> None:
    release.add(GNU, inspect="echo damaged >&2; exit 1")
    release.serve()
    script = release.make()

    result = install(shell, script, host)

    assert result.returncode == 1
    assert "inspect --verify" in result.stderr
    assert "damaged" in result.stderr
    assert list(host.install_dir.iterdir()) == []


def test_an_existing_install_survives_a_failed_one(
    shell: list[str],
    release: Release,
    host: Host,
) -> None:
    host.install_dir.mkdir(parents=True)
    installed(host).write_text("old")
    script = release.make()
    release.server.files[f"/fsbpyr-{GNU}.xz"] = b"garbage"

    result = install(shell, script, host)

    assert result.returncode == 1
    assert installed(host).read_text() == "old"


def test_an_unsupported_target_stops_naming_the_targets(
    shell: list[str],
    release: Release,
    host: Host,
    server: Server,
) -> None:
    script = release.make()
    host.fake("uname", 'case "$1" in -s) echo Linux ;; -m) echo riscv64 ;; esac')

    result = install(shell, script, host)

    assert result.returncode == 1
    assert "riscv64-linux-gnu" in result.stderr
    for target in (GNU, "aarch64-linux-gnu", MUSL):
        assert target in result.stderr
    assert server.requests == []
    assert not host.install_dir.exists()


def test_macos_on_intel_is_unsupported(shell: list[str], release: Release, host: Host) -> None:
    script = release.make()
    host.fake("uname", 'case "$1" in -s) echo Darwin ;; -m) echo x86_64 ;; esac')
    host.fake("sysctl", "echo 0")

    result = install(shell, script, host)

    assert result.returncode == 1
    assert "x86_64-macos" in result.stderr


def test_aarch64_is_detected(
    shell: list[str],
    release: Release,
    host: Host,
    server: Server,
) -> None:
    script = release.make()
    host.fake("uname", 'case "$1" in -s) echo Linux ;; -m) echo aarch64 ;; esac')

    result = install(shell, script, host)

    assert result.returncode == 0, result.stderr
    assert server.requests == ["/fsbpyr-aarch64-linux-gnu.xz"]


def test_musl_is_detected_from_ldd(
    shell: list[str],
    release: Release,
    host: Host,
    server: Server,
) -> None:
    script = release.make()
    host.fake("ldd", 'echo "musl libc (x86_64)" >&2; echo "Version 1.2.2" >&2; exit 1')

    result = install(shell, script, host)

    assert result.returncode == 0, result.stderr
    assert server.requests == [f"/fsbpyr-{MUSL}.xz"]


def test_a_too_old_glibc_stops(
    shell: list[str],
    release: Release,
    host: Host,
    server: Server,
) -> None:
    release.glibc[GNU] = "2.28"
    script = release.make()
    host.fake("ldd", 'echo "ldd (GNU libc) 2.17"')

    result = install(shell, script, host)

    assert result.returncode == 1
    assert "glibc is 2.17" in result.stderr
    assert "glibc >= 2.28" in result.stderr
    assert server.requests == []


@pytest.mark.parametrize("host_glibc", ["2.28", "2.31", "3.0"])
def test_a_new_enough_glibc_installs(
    shell: list[str],
    release: Release,
    host: Host,
    host_glibc: str,
) -> None:
    release.glibc[GNU] = "2.28"
    script = release.make()
    host.fake("ldd", f'echo "ldd (GNU libc) {host_glibc}"')

    result = install(shell, script, host)

    assert result.returncode == 0, result.stderr


def test_the_install_directory_order(
    shell: list[str],
    release: Release,
    host: Host,
    tmp_path: Path,
) -> None:
    script = release.make()
    host.env.pop("FSBPYR_INSTALL_DIR")
    xdg_data = tmp_path / "data" / "share"
    xdg_data.mkdir(parents=True)
    expected = [
        (
            {"FSBPYR_INSTALL_DIR": str(tmp_path / "a")},
            tmp_path / "a",
        ),
        (
            {"XDG_BIN_HOME": str(tmp_path / "b")},
            tmp_path / "b",
        ),
        (
            {"XDG_DATA_HOME": str(xdg_data)},
            tmp_path / "data" / "bin",
        ),
        ({}, host.home / ".local" / "bin"),
    ]
    everything = {
        "FSBPYR_INSTALL_DIR": str(tmp_path / "a"),
        "XDG_BIN_HOME": str(tmp_path / "b"),
        "XDG_DATA_HOME": str(xdg_data),
    }

    for env, directory in expected:
        # Each run sets this one and the lower ones, so only the first of them wins
        names = list(everything)
        keep = names[names.index(next(iter(env))) :] if env else []
        chosen = {k: everything[k] for k in keep}
        result = install(shell, script, host, "--no-modify-path", **chosen)
        assert result.returncode == 0, result.stderr
        assert (directory / "fsbpyr").exists(), (env, result.stdout)


def test_the_man_page_and_completions_go_where_make_install_puts_them(
    shell: list[str],
    release: Release,
    host: Host,
) -> None:
    script = release.make()
    prefix = host.home / ".local"

    result = install(shell, script, host)

    assert result.returncode == 0, result.stderr
    assert (prefix / "share/man/man1/fsbpyr.1").read_text() == ".TH FSBPYR 1\n"
    for path, shell_name in (
        ("share/bash-completion/completions/fsbpyr", "bash"),
        ("share/zsh/site-functions/_fsbpyr", "zsh"),
        ("share/fish/vendor_completions.d/fsbpyr.fish", "fish"),
    ):
        assert (prefix / path).read_text() == f"# completion for {shell_name}\n"


def test_no_man_page_outside_a_bin_directory(
    shell: list[str],
    release: Release,
    host: Host,
    tmp_path: Path,
) -> None:
    script = release.make()
    host.env["FSBPYR_INSTALL_DIR"] = str(tmp_path / "apps")

    result = install(shell, script, host)

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "share").exists()


def test_a_directory_off_path_gets_an_env_script_and_rc_lines(
    shell: list[str],
    release: Release,
    host: Host,
) -> None:
    script = release.make()
    (host.home / ".bashrc").write_text("# mine")  # no newline at the end
    (host.home / ".zshrc").write_text("# zsh\n")
    (host.home / ".config" / "fish").mkdir(parents=True)

    result = install(shell, script, host)

    assert result.returncode == 0, result.stderr
    line = '. "$HOME/.local/bin/env"'
    assert (host.home / ".profile").read_text() == line + "\n"
    assert (host.home / ".bashrc").read_text() == f"# mine\n{line}\n"
    assert (host.home / ".zshrc").read_text() == f"# zsh\n{line}\n"
    fish = host.home / ".config/fish/conf.d/fsbpyr.env.fish"
    assert fish.read_text() == 'source "$HOME/.local/bin/env.fish"\n'
    assert "source $HOME/.local/bin/env" in result.stdout
    # The env script puts the directory first on PATH, once
    sourced = subprocess.run(
        [*shell, "-c", f'. "{host.install_dir}/env"; . "{host.install_dir}/env"; echo "$PATH"'],
        env={**host.env, "PATH": "/usr/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert sourced.stdout == f"{host.install_dir}:/usr/bin\n"
    assert 'set -x PATH "$HOME/.local/bin" $PATH' in (host.install_dir / "env.fish").read_text()


def test_only_profile_is_created_and_a_second_run_adds_nothing(
    shell: list[str],
    release: Release,
    host: Host,
) -> None:
    script = release.make()

    first = install(shell, script, host)
    second = install(shell, script, host)

    assert first.returncode == second.returncode == 0
    assert (host.home / ".profile").read_text() == '. "$HOME/.local/bin/env"\n'
    assert not (host.home / ".bashrc").exists()
    assert not (host.home / ".zshrc").exists()
    assert not (host.home / ".config").exists()


def test_a_directory_outside_home_is_written_as_it_is(
    shell: list[str],
    release: Release,
    host: Host,
    tmp_path: Path,
) -> None:
    script = release.make()
    host.env["FSBPYR_INSTALL_DIR"] = str(tmp_path / "apps")

    result = install(shell, script, host)

    assert result.returncode == 0, result.stderr
    assert (host.home / ".profile").read_text() == f'. "{tmp_path}/apps/env"\n'


def test_an_existing_env_is_reused(shell: list[str], release: Release, host: Host) -> None:
    script = release.make()
    host.install_dir.mkdir(parents=True)
    (host.install_dir / "env").write_text("# uv's\n")

    result = install(shell, script, host)

    assert result.returncode == 0, result.stderr
    assert (host.install_dir / "env").read_text() == "# uv's\n"
    assert (host.home / ".profile").read_text() == '. "$HOME/.local/bin/env"\n'


def test_a_directory_on_path_is_left_alone(shell: list[str], release: Release, host: Host) -> None:
    script = release.make()
    host.env["PATH"] = f"{host.install_dir}:{host.bin}"

    result = install(shell, script, host)

    assert result.returncode == 0, result.stderr
    assert not (host.home / ".profile").exists()
    assert not (host.install_dir / "env").exists()
    assert "restart your shell" not in result.stdout


@pytest.mark.parametrize("how", ["flag", "env"])
def test_no_modify_path_leaves_shell_files_alone(
    shell: list[str],
    release: Release,
    host: Host,
    tmp_path: Path,
    how: str,
) -> None:
    script = release.make()
    (host.home / ".bashrc").write_text("# mine\n")
    github_path = tmp_path / "github_path"
    args = ["--no-modify-path"] if how == "flag" else []
    extra = {"GITHUB_PATH": str(github_path)}
    if how == "env":
        extra["FSBPYR_NO_MODIFY_PATH"] = "1"

    result = install(shell, script, host, *args, **extra)

    assert result.returncode == 0, result.stderr
    assert not (host.home / ".profile").exists()
    assert (host.home / ".bashrc").read_text() == "# mine\n"
    assert not (host.install_dir / "env").exists()
    assert not github_path.exists()
    assert installed(host).exists()
    assert "is not on your PATH" in result.stdout


def test_github_actions_gets_the_directory_through_github_path(
    shell: list[str],
    release: Release,
    host: Host,
    tmp_path: Path,
) -> None:
    script = release.make()
    github_path = tmp_path / "github_path"
    github_path.write_text("/already/there\n")

    result = install(shell, script, host, GITHUB_PATH=str(github_path), GITHUB_ACTIONS="true")

    assert result.returncode == 0, result.stderr
    assert github_path.read_text() == f"/already/there\n{host.install_dir}\n"
    assert not (host.home / ".profile").exists()


def test_verify_without_gh_stops_before_downloading(
    shell: list[str],
    release: Release,
    host: Host,
    server: Server,
) -> None:
    script = release.make()

    result = install(shell, script, host, FSBPYR_VERIFY="1")

    assert result.returncode == 1
    assert "FSBPYR_VERIFY=1 needs the GitHub CLI" in result.stderr
    assert server.requests == []
    assert not installed(host).exists()


def test_verify_runs_gh_attestation_verify(
    shell: list[str],
    release: Release,
    host: Host,
    tmp_path: Path,
) -> None:
    script = release.make()
    log = tmp_path / "gh.log"
    host.fake("gh", f'echo "$*" >> {log}')

    result = install(shell, script, host, FSBPYR_VERIFY="1")

    assert result.returncode == 0, result.stderr
    call = log.read_text()
    assert call.startswith("attestation verify ")
    assert "--repo devitops-com/fsbpyr" in call
    assert "--signer-workflow devitops-com/fsbpyr/.github/workflows/release.yml" in call
    assert f"--source-ref refs/tags/v{VERSION}" in call


def test_verify_stops_when_gh_fails(shell: list[str], release: Release, host: Host) -> None:
    script = release.make()
    host.fake("gh", "echo 'no matching attestation' >&2; exit 1")

    result = install(shell, script, host, FSBPYR_VERIFY="1")

    assert result.returncode == 1
    assert "attestation" in result.stderr
    assert not installed(host).exists()


def test_without_verify_gh_is_not_run(
    shell: list[str],
    release: Release,
    host: Host,
    tmp_path: Path,
) -> None:
    script = release.make()
    host.fake("gh", f"echo called >> {tmp_path}/gh.log")

    install(shell, script, host)

    assert not (tmp_path / "gh.log").exists()


def test_quiet_prints_nothing_and_verbose_prints_more(
    shell: list[str],
    release: Release,
    host: Host,
) -> None:
    script = release.make()

    quiet = install(shell, script, host, "-q")
    verbose = install(shell, script, host, "--verbose")

    assert quiet.returncode == verbose.returncode == 0
    assert quiet.stdout == "" and quiet.stderr == ""
    assert "sha256 of" in verbose.stdout


def test_quiet_still_prints_errors(shell: list[str], release: Release, host: Host) -> None:
    script = release.make()
    host.fake("uname", 'case "$1" in -s) echo Linux ;; -m) echo riscv64 ;; esac')

    result = install(shell, script, host, "--quiet")

    assert result.returncode == 1
    assert "error:" in result.stderr


def test_help_and_unknown_options(shell: list[str], release: Release, host: Host) -> None:
    script = release.make()

    helped = install(shell, script, host, "-h")
    wrong = install(shell, script, host, "--frobnicate")

    assert helped.returncode == 0
    assert f"The installer for fsbpyr {VERSION}" in helped.stdout
    assert "--no-modify-path" in helped.stdout
    assert wrong.returncode == 1
    assert "unknown option: --frobnicate" in wrong.stderr
    assert not host.install_dir.exists()


def test_it_runs_from_a_pipe(shell: list[str], release: Release, host: Host) -> None:
    script = release.make()

    result = subprocess.run(
        [*shell, "-s", "--", "-q", "--no-modify-path"],
        input=script.read_text(),
        env=host.env,
        capture_output=True,
        text=True,
        timeout=SCRIPT_TIMEOUT_S,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert installed(host).exists()


# --- make-install.sh ---


def run_generator(*args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, str(MAKE_INSTALL_SH), *map(str, args)],
        capture_output=True,
        text=True,
        timeout=SCRIPT_TIMEOUT_S,
        check=False,
    )


def test_the_generator_writes_version_targets_checksums_and_glibc(release: Release) -> None:
    release.glibc[GNU] = "2.28"
    release.make()

    text = (release.root / "install.sh").read_text()

    assert f"APP_VERSION='{VERSION}'" in text
    assert f"TARGETS='{GNU} aarch64-linux-gnu {MUSL}'" in text
    for target, glibc in ((GNU, "2.28"), ("aarch64-linux-gnu", "2.25"), (MUSL, "-")):
        for suffix, shown in (("", glibc), (".xz", "-")):
            data = (release.assets / f"fsbpyr-{target}{suffix}").read_bytes()
            assert f"fsbpyr-{target}{suffix} {hashlib.sha256(data).hexdigest()} {shown}\n" in text
    assert "@VERSION@" not in text and "@TARGETS@" not in text and "@ASSETS@" not in text
    assert os.access(release.root / "install.sh", os.X_OK)


def test_the_generator_without_targets_warns_and_makes_an_installer_with_none(
    tmp_path: Path,
) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "fsbpyr").write_text("not a target")
    out = tmp_path / "install.sh"

    result = run_generator(VERSION, tmp_path / "assets", out)

    assert result.returncode == 0, result.stderr
    assert "no fsbpyr-<target> executable" in result.stderr
    assert "TARGETS=''" in out.read_text()


@pytest.mark.parametrize("version", ["", "v1.2.3", "1.2 3"])
def test_the_generator_refuses_a_bad_version(tmp_path: Path, version: str) -> None:
    result = run_generator(version, tmp_path, tmp_path / "install.sh")

    assert result.returncode == 2
    assert not (tmp_path / "install.sh").exists()


def test_the_generator_stops_when_the_glibc_is_not_recorded(release: Release) -> None:
    inspector = release.root / "silent"
    inspector.write_text("#!/bin/sh\n")
    inspector.chmod(0o755)

    result = run_generator(VERSION, release.assets, release.root / "install.sh", inspector)

    assert result.returncode == 1
    assert "records no glibc" in result.stderr
    assert not (release.root / "install.sh").exists()


def test_the_template_has_no_bashisms(release: Release) -> None:
    """dash parses it (-n), and so does busybox."""
    script = release.make()
    for command in (["dash"], ["busybox", "sh"]):
        if shutil.which(command[0]) is None:
            continue
        result = subprocess.run(
            [*command, "-n", str(script)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr
