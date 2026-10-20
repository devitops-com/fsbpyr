# A Debian trixie build environment with clang and lld 22: `make bootstrap` uses it on hosts
# other than Debian trixie (tools/bootstrap.sh). The checkout is mounted at /src and
# dist/fsbpyr lands in it; a volume keeps uv's cache, its Pythons and fsbpyr's runtime
# between runs.
FROM docker.io/library/debian:trixie@sha256:913f6706df59a68922d1dd08f78c2476560a8d367897200a6005b00e5f67c2d5 AS build

# Everything but uv and Python from Debian's own packages (trixie ships clang and lld 22 since 13.7).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git make clang-22 lld-22 \
    && rm -rf /var/lib/apt/lists/*

# The newest uv, from astral.sh; it installs the Python pinned in .python-version.
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh

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
