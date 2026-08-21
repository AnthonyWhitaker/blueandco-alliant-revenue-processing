FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# msodbcsql18 ODBC driver, required by pyodbc to reach Azure SQL in production.
# The Microsoft repo config package is picked to match the base image's Debian
# release (python:3.12-slim tracks the current stable).
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl unixodbc-dev gcc g++ && \
    curl -fsSL -o /tmp/packages-microsoft-prod.deb \
        "https://packages.microsoft.com/config/debian/$(. /etc/os-release && echo $VERSION_ID)/packages-microsoft-prod.deb" && \
    dpkg -i /tmp/packages-microsoft-prod.deb && \
    rm /tmp/packages-microsoft-prod.deb && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 && \
    apt-mark manual libgssapi-krb5-2 && \
    apt-get purge -y --auto-remove curl gcc g++ && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "ui/app.py", \
    "--server.port=8501", \
    "--server.headless=true", \
    "--server.address=0.0.0.0"]
