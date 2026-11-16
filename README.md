# NullOps: Full Working App with Docker Compose

A small production-like stack run with Docker Compose: a Flask web app backed by
PostgreSQL, served through an Nginx reverse proxy and monitored with cAdvisor.

## Architecture

```
            host :80                      host :8081
               |                              |
          +---------+                   +-----------+
          |  nginx  |                   |  cadvisor |  (container metrics)
          +---------+                   +-----------+
               |  frontend network
          +---------+
          |   app   |  Flask + gunicorn, non-root user
          +---------+
               |  backend network
          +---------+
          |   db    |  PostgreSQL 16, data in named volume "dbdata"
          +---------+
```

| Service    | Image                              | Purpose                                   |
|------------|------------------------------------|-------------------------------------------|
| `db`       | `postgres:16-alpine`               | Stores visits; data persists in `dbdata`  |
| `app`      | built from `./app`                 | Flask API (`/`, `/health`)                |
| `nginx`    | `nginx:1.27-alpine`                | Reverse proxy, the only public web entry  |
| `cadvisor` | `gcr.io/cadvisor/cadvisor:v0.49.1` | CPU / memory / network per container      |

Design choices:

- **Healthchecks on every service.** Startup order uses `depends_on: condition: service_healthy`,
  so `app` waits until Postgres accepts connections and `nginx` waits until the app is healthy.
  Plain `depends_on` only orders the start, it does not wait for readiness.
- **Two networks.** `db` is only on `backend`, so neither Nginx nor the host can reach the database.
- **Secrets in `.env`.** Credentials are never hard-coded; `.env` is git-ignored and
  `.env.example` documents the variables.
- **Non-root app container** (`appuser`, uid 10001).
- **`restart: unless-stopped`** so services come back after a crash or a Docker restart.

## Quick start

```bash
cp .env.example .env        # then change DB_PASS
docker compose up --build -d
docker compose ps           # every service should show (healthy)
```

| URL                          | What you see                                   |
|------------------------------|------------------------------------------------|
| http://localhost/            | JSON with a visit counter stored in Postgres  |
| http://localhost/health      | `{"status": "ok", "database": "reachable"}`   |
| http://localhost:8081/       | cAdvisor dashboard                             |

Ports can be changed with `NGINX_PORT` and `CADVISOR_PORT` in `.env`, and the number of
gunicorn workers per app container with `APP_WORKERS` (default 2).

Stop the stack (data is kept):

```bash
docker compose down
```

Stop and delete the database volume too:

```bash
docker compose down -v
```

## Labs

### Lab 1: Reverse proxy
`nginx/nginx.conf` forwards every request to the `app` upstream and sets the
`X-Real-IP` / `X-Forwarded-*` headers. Check it:

```bash
curl -i http://localhost/          # Server: nginx, body comes from Flask
docker compose logs app | tail     # the request appears in gunicorn's access log
```

### Lab 2: Healthchecks

```bash
docker compose ps                  # STATUS column shows (healthy)
docker inspect --format '{{json .State.Health}}' nullops-app-1
```

Failure test: stop the database and watch the app become unhealthy.

```bash
docker compose stop db
curl -i http://localhost/health    # 503, database error
docker compose start db
```

### Lab 3: Monitoring
Open http://localhost:8081, then **Docker Containers** and pick a container to see its
CPU, memory and network graphs. A quick text view of the same data:

```bash
docker stats --no-stream
```

### Persistence check

```bash
curl http://localhost/                       # note total_visits
docker compose down && docker compose up -d
curl http://localhost/                       # counter continues, data survived
```

### Scaling the app

```bash
docker compose up -d --scale app=3
for i in 1 2 3 4 5 6; do curl -s http://localhost/; echo; done   # served_by changes
```

Nginx resolves `app` once at startup, so restart it after scaling:
`docker compose restart nginx`.

## Project layout

```
.
├── app/
│   ├── app.py            # Flask app
│   ├── Dockerfile        # python:3.12-slim, non-root
│   └── requirements.txt
├── nginx/
│   └── nginx.conf        # reverse proxy config
├── docker-compose.yml
├── .env.example
└── README.md
```

## Notes

- On Docker Desktop (Windows/macOS) cAdvisor shows fewer host-level metrics than on a
  Linux host, because it runs inside Docker's VM; per-container metrics still work.
