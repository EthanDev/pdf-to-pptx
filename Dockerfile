FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --uid 10001 --create-home converter && mkdir /data && chown 10001:10001 /data
COPY app app
COPY tests tests
ENV DATA_DIR=/data PYTHONPATH=/srv/app PYTHONDONTWRITEBYTECODE=1
VOLUME /data
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).read()"]
# Keep signed links out of access logs. One process owns the disk-backed queue.
CMD ["uvicorn", "main:app", "--app-dir", "app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
