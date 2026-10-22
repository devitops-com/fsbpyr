# A Debian trixie build environment, for the architecture it is built for (amd64 or arm64):
# `make bootstrap` uses it on hosts other than Debian trixie (tools/bootstrap.sh). The checkout
# is mounted at /src and dist/fsbpyr lands in it; a volume keeps uv's cache, its Pythons,
# fsbpyr's runtime and the clang/lld 23 bootstrap.sh fetches between runs.
FROM docker.io/library/debian:trixie@sha256:4c25275446ca371766b8b093c696782eabce06263fccacb8d5d813b3dc44b308 AS build

# Everything but uv, Python and clang/lld 23 from Debian's own packages. The runtime links with
# LLVM's clang/lld 23, which bootstrap.sh fetches with curl and unpacks with zstd
# (tools/release/llvm23); trixie's clang, lld and compiler-rt 22 are not used for it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git make clang-22 lld-22 libclang-rt-22-dev zstd \
    && rm -rf /var/lib/apt/lists/*

# uv of the container's architecture (TARGETARCH: amd64 or arm64; `podman build --arch arm64`),
# at the version tools/release/uv.version names, from its release tarball and checked against
# the SHA-256 that astral-sh/uv publishes for it. It installs the Python pinned in
# .python-version.
ARG TARGETARCH
ARG UV_VERSION=0.13.5
ARG UV_SHA256_AMD64=f68d291c75783122dad58ab77def0e13f3233f358731db5d6d7e8715603b928f
ARG UV_SHA256_ARM64=4bb69cf95ed13f23713bb7bc09f241896ea0c75b7c557e615a20604b82f9c403
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
