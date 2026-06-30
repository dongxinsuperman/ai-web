FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY aiweb ./aiweb
RUN pip install --no-cache-dir .

ENV AIWEB_HOST=0.0.0.0 AIWEB_PORT=8009 AIWEB_BROWSER_SLOTS={}
EXPOSE 8009

CMD ["python", "-m", "aiweb.main"]
