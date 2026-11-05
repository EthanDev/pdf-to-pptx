FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
RUN useradd --uid 10001 --create-home converter && mkdir /data && chown 10001:10001 /data
ARG SOURCE_REVISION=local
ENV SOURCE_REVISION=$SOURCE_REVISION
COPY LICENSE NOTICE ./
COPY app app
COPY tests tests
COPY deploy deploy
COPY scripts scripts
ENV DATA_DIR=/data PYTHONPATH=/srv/app PYTHONDONTWRITEBYTECODE=1
VOLUME /data
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).read()"]
# Keep signed links out of access logs. One process owns the disk-backed queue.
CMD ["uvicorn", "main:app", "--app-dir", "app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
