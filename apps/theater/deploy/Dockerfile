# Theater Controls — single-container image.
#
# Builds a thin runtime layer over a Python 3.13 base. The Bazel image rule
# under //apps/theater/deploy:image should produce this same shape, but the
# Dockerfile is here for two reasons:
#   1. Reference for what the image needs to contain.
#   2. Local `docker build` works without Bazel for emergencies.

FROM python:3.13-slim AS base

# wakeonlan needs nothing native; pylutron_caseta is pure-Python. All deps
# are pure-Python; no apt installs required.

WORKDIR /app

# 1. Install dependencies first (cache-friendly).
COPY service/requirements.lock.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# 2. Copy the service source.
COPY service /app/apps/theater/service
RUN touch /app/apps/__init__.py /app/apps/theater/__init__.py

# 3. Copy the prebuilt frontend bundle. Built upstream by the Bazel frontend
#    target; in CI the artifact is dropped into service/frontend/dist.
COPY frontend/dist /app/apps/theater/frontend/dist

EXPOSE 8000

# Credentials are NEVER baked into the image. Mount /root/.config/theater
# from a host volume at runtime — see RUNBOOK.md §5b.
VOLUME ["/root/.config/theater"]

ENV PYTHONPATH=/app
ENV THEATER_REAL_DEVICES=1
ENV THEATER_CONFIG=/root/.config/theater/config.toml

CMD ["uvicorn", "apps.theater.service.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--log-config", "/app/apps/theater/service/logging.json"]
