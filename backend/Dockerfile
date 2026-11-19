FROM python:3.11-slim AS builder

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV HOME=/home/app
ENV FASTEMBED_CACHE_PATH=/home/app/.cache/fastembed

WORKDIR /build

RUN python -m venv ${VIRTUAL_ENV}

COPY backend/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Pre-download the embedding model during image build.
RUN mkdir -p /home/app/.cache/fastembed \
    && python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5', cache_dir='/home/app/.cache/fastembed')"


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV HOME=/home/app
ENV FASTEMBED_CACHE_PATH=/home/app/.cache/fastembed

WORKDIR /workspace/backend

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /home/app app \
    && mkdir -p /home/app/.cache/fastembed /workspace/data \
    && chown -R app:app /home/app /workspace

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder --chown=app:app /home/app/.cache/fastembed /home/app/.cache/fastembed

COPY --chown=app:app backend/app ./app
COPY --chown=app:app backend/tests ./tests
COPY --chown=app:app data /workspace/data

USER app

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
