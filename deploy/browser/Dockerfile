# Agents Hub browser service: headless Chromium behind a token-protected API.
# See deploy/browser/app.py and docs/tools-and-capabilities.md.
#
# Build context is the repository root, so the image can take the hub's own
# SSRF check (common/ssrf.py) instead of a re-typed copy:
#   docker build -f deploy/browser/Dockerfile -t agents-hub-browser .
# docker-compose.yml builds it that way under the `browser` profile.
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BROWSER_PORT=3000

WORKDIR /srv

COPY deploy/browser/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

# The hub's SSRF guard, vendored at build time under a name that cannot clash
# with anything on the image's path. policy.py imports it as hub_ssrf.
COPY common/ssrf.py /srv/hub_ssrf.py
COPY deploy/browser/policy.py deploy/browser/app.py /srv/

# The base image ships a non-root user for exactly this.
USER pwuser

EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('BROWSER_PORT','3000'))" || exit 1

CMD ["python", "app.py"]
