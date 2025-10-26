# Traktarr

**Traktarr** is a lightweight Python + Flask microservice that connects to your [Trakt.tv](https://trakt.tv) lists and exposes them chunk-by-chunk through a simple API.

It’s designed for use with **Radarr** and **Sonarr** as a **Custom List Import Source**, so your lists are imported _gradually_ over time — preventing huge queue spikes or overwhelming your downloader.

---

## Features

- ✅ Fetch movies or shows from any public Trakt list
- 🔁 Handles Trakt OAuth tokens automatically (refresh, re-auth, store in config)
- ⏳ Returns list items in configurable **chunks** and **intervals** (e.g. 10 items per week)
- 🧩 Simple REST API endpoint
- 🐳 Fully Dockerized — deploy anywhere
- 💾 Persistent `config.json` storage for tokens and client credentials

---

## How It Works

When Radarr/Sonarr calls the Traktarr endpoint, it receives only a portion of your Trakt list based on:

- **`start`** → The first date the list started chunking
- **`step`** → Interval in days between chunks
- **`chunk`** → Number of items to return per interval
- **`type`** → Filter items by `movie` or `show`

The service automatically rotates through the list chunks over time.  
As days pass, it exposes the next batch of TMDB IDs from your Trakt list.

---

## Configuration (config.json)

Create config.json in repository root (or mount via Docker):

```json
{
  "client_id": "YOUR_TRAKT_CLIENT_ID",
  "client_secret": "YOUR_TRAKT_CLIENT_SECRET",
  "redirect_uri": "YOUR_TRAKT_REDIRECT_URI",
  "refresh_token": "REFRESH_TOKEN"
}
```

## Setup & run (local)

1. Clone repo:

```bash
git clone https://github.com/Sittravell/traktarr.git
cd traktarr
```

2. Create config.json as above.

3. Create virtualenv and install:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

4. Run:

```bash
python app.py
```

5. Access:

```bash
GET /list/{LIST_ID}?start=2025-10-01&step=7&chunk=10&type=movie
```

## Docker

Create docker-compose.yml:

```yaml
version: "3.8"
services:
  maltrackarr:
    image: sittravell/traktarr:latest
    container_name: traktarr
    ports:
      - "5252:5252"
    volumes:
      - ./config.json:/app/config.json
```

Run:

```bash
docker compose up -d
```

## Example Output

```json
[{ "id": 1399 }, { "id": 1668 }, { "id": 66732 }]
```
