ARG DOCKER_REGISTRY=ghcr.io
ARG VERSION=0.7.23
FROM ${DOCKER_REGISTRY}/kagent-dev/kagent/kagent-adk:${VERSION}

WORKDIR /app

COPY coach_agent/ coach_agent/
COPY pyproject.toml pyproject.toml
COPY .python-version .python-version

RUN uv sync --refresh

CMD ["coach_agent"]