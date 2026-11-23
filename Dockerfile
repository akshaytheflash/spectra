FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/app/.models/hf OMP_NUM_THREADS=2
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg libzbar0 libgomp1 libgl1 libglib2.0-0 tesseract-ocr tesseract-ocr-hin curl nodejs npm \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu torch==2.4.1 \
    && pip install -r backend/requirements.txt
COPY frontend/package.json frontend/package.json
RUN cd frontend && npm install --silent
COPY . .
RUN cd frontend && npm run build
EXPOSE 8000
# On start: generate data + train + seed if missing, build the dashboard, serve
# the SPA from the same origin (no proxy). Set DATABASE_URL/SUPABASE_* in .env.
CMD ["bash", "-lc", "python scripts/bootstrap.py --seed && cd backend && ./run.sh"]
