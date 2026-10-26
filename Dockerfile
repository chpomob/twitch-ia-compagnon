# The one image (phase 10, R3): a build stage that turns the repository into
# the project's own wheel, and a runtime stage that installs only that wheel.
# The base is pinned by tag and digest; both stages use the same one.

FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS build
WORKDIR /src
# Only the files the wheel needs, by name; .dockerignore keeps every other
# file out of the context.
COPY pyproject.toml LICENSE ./
COPY core ./core
COPY modules ./modules
RUN python -m pip wheel . --no-deps --no-cache-dir --wheel-dir /wheels

FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
# All state lives under the /data volume (A3), owned by the numeric user.
RUN groupadd --gid 10001 companion \
    && useradd --uid 10001 --gid 10001 --home-dir /data --no-create-home --shell /usr/sbin/nologin companion \
    && mkdir -m 0700 /data \
    && chown 10001:10001 /data
ENV XDG_CONFIG_HOME=/data HOME=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8765
USER 10001
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD ["twitch-ia-compagnon-config-ui", "--health"]
# Container mode is a command line (A2): the per-user default location, a
# wildcard bind the published port reaches, no browser, the runtime started.
CMD ["twitch-ia-compagnon-config-ui", "--host", "0.0.0.0", "--allow-non-loopback", "--no-browser", "--start-runtime"]
