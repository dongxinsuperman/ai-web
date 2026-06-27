# 基于官方 Playwright 镜像（已内置 Chromium 及系统依赖）
FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

WORKDIR /app

COPY pyproject.toml ./
# 多引擎：安装 chromium / firefox / webkit（platform 真实生效）
RUN pip install --no-cache-dir . && python -m playwright install chromium firefox webkit

COPY aiweb ./aiweb

ENV AIWEB_HOST=0.0.0.0 AIWEB_PORT=8009 AIWEB_BROWSER_SLOTS=chrome:2,firefox:1,webkit:1
EXPOSE 8009

CMD ["python", "-m", "aiweb.main"]
