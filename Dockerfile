FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY frontend ./frontend
RUN addgroup --system --gid 10001 recall && adduser --system --uid 10001 --ingroup recall recall \
    && mkdir -p /app/.data && chown -R recall:recall /app/.data
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER recall
EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
