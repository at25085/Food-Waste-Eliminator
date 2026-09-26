# Deploy on Vultr (MLH "Best Use of Vultr") + .tech domain

1. Run the pipeline locally so `artifacts/` holds the models, ledger DB and recommendations.
2. Build the image: `docker build -t forecaster .` (from the repo root).
3. Vultr: create a Cloud Compute instance (Ubuntu, 2 vCPU / 4 GB is enough; the image serves
   precomputed forecasts, it does not train). Install Docker, then either push the image to
   Vultr Container Registry or `docker save forecaster | ssh root@IP docker load`.
4. On the server: `docker run -d --restart unless-stopped -p 80:8000 --env-file .env forecaster`
   (`.env` holds GEMINI_API_KEY / ELEVENLABS_API_KEY / DATABASE_URL — never bake keys into the image).
5. Point the `.tech` domain's A record at the instance IP. Optional HTTPS: put Caddy in front
   (`caddy reverse-proxy --from yourname.tech --to :8000`).
6. For Tiger Data: set `DATABASE_URL` to the Tiger Cloud connection string and run
   `python -m forecaster.db.copy_to_postgres $DATABASE_URL` then `python -m forecaster.db.timescale` once.
