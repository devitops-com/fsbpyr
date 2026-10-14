# A Debian trixie build environment with clang and lld 22: `make bootstrap` uses it on hosts
# other than Debian trixie (tools/bootstrap.sh). The checkout is mounted at /src and
# dist/fsbpyr lands in it; a volume keeps uv's cache, its Pythons and fsbpyr's runtime
# between runs.
FROM docker.io/library/debian:trixie

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
