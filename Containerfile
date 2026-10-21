# A Debian trixie build environment with clang and lld 22, for the architecture it is built
# for (amd64 or arm64): `make bootstrap` uses it on hosts other than Debian trixie
# (tools/bootstrap.sh). The checkout is mounted at /src and dist/fsbpyr lands in it; a volume
# keeps uv's cache, its Pythons and fsbpyr's runtime between runs.
FROM docker.io/library/debian:trixie@sha256:913f6706df59a68922d1dd08f78c2476560a8d367897200a6005b00e5f67c2d5 AS build

# Everything but uv and Python from Debian's own packages (trixie ships clang and lld 22 since 13.7;
# zstd is for bootstrap.sh's opt-in clang 23, FSBPYR_LLVM23=1; libclang-rt-22-dev has the
# compiler-rt builtins that the aarch64 link needs, runtime/stub.py outlined_atomics).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git make clang-22 lld-22 libclang-rt-22-dev zstd \
    && rm -rf /var/lib/apt/lists/*

# uv of the container's architecture (TARGETARCH: amd64 or arm64; `podman build --arch arm64`),
# at the version tools/release/uv.version names, from its release tarball and checked against
# the SHA-256 that astral-sh/uv publishes for it. It installs the Python pinned in
# .python-version.
ARG TARGETARCH
ARG UV_VERSION=0.13.1
ARG UV_SHA256_AMD64=eadd9d27c134ab5ac1ffd4473b97a741407f3ea238288f03121fc4deddf90220
ARG UV_SHA256_ARM64=9041c73c99fdc5892cf128140beb354eca9231d502068cc36e6cb53a8e59ad14
RUN case "$TARGETARCH" in \
        amd64) name=uv-x86_64-unknown-linux-gnu; sha256=$UV_SHA256_AMD64 ;; \
        arm64) name=uv-aarch64-unknown-linux-gnu; sha256=$UV_SHA256_ARM64 ;; \
        *) echo "unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac \
    && curl -fsSL -o /tmp/uv.tar.gz \
        "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$name.tar.gz" \
    && echo "$sha256  /tmp/uv.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/uv.tar.gz --strip-components=1 -C /usr/local/bin "$name/uv" "$name/uvx" \
    && rm /tmp/uv.tar.gz

# The environment outside the checkout: the host's .venv is left alone.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_INSTALL_DIR=/root/.cache/uv-python
WORKDIR /src

# The full test suite's environment (`make test-container`): the build stage plus what
# tools/test-setup.sh adds, with uv's Pythons in the home of `tester`, whom the suite runs as.
# No podman here: the tests that need one skip (CONTRIBUTING.md). The project environment is
# the checkout's own .venv, as the tests expect: the build stage's /opt/venv is unset, since
# uv runs the tests start for their fixture projects would otherwise sync into it.
FROM build AS test
COPY tools/test-setup.sh /usr/local/sbin/test-setup.sh
RUN bash /usr/local/sbin/test-setup.sh \
    && mkdir /home/tester/downloads /home/tester/.cache \
    && chown tester: /home/tester/downloads /home/tester/.cache
ENV UV_PROJECT_ENVIRONMENT= \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/home/tester/.cache/uv-python
USER tester
WORKDIR /home/tester/src
