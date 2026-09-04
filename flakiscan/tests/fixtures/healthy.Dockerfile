FROM python:3.11.8@sha256:9d24c284f6f27daf986aa39c3d76a4c14ba0da42c19c9cd28b7bafcd2a1cdd6

ARG BUILD_ENV=production

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl=7.81.0-1ubuntu1.15 \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fLsS https://example.com/install.sh -o install.sh \
    && sha256sum -c install.sh.sha256 \
    && bash install.sh

RUN git clone https://github.com/example/repo.git \
    && cd repo \
    && git checkout a1b2c3d4e5f6

COPY . /app
WORKDIR /app
CMD ["python3", "app.py"]
