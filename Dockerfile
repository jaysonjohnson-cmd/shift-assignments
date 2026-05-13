# ---------- Stage 1: build the Next.js static export ----------
FROM node:22-slim AS frontend
WORKDIR /build/shift-assignments
COPY shift-assignments/package.json shift-assignments/package-lock.json ./
RUN npm ci
COPY shift-assignments/ ./
RUN npm run build
# Next.js writes the static export to `out/`.

# ---------- Stage 2: Python runtime serving both API + static UI ----------
FROM python:3.12-slim
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Drop the Next.js source — only the build output is needed at runtime.
RUN rm -rf shift-assignments
# Copy the built static site into /app/frontend so Flask can serve it.
COPY --from=frontend /build/shift-assignments/out ./frontend

RUN chown -R appuser:appuser /app
USER appuser

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "0", "main:app"]
