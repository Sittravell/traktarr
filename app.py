#!/usr/bin/env python3
"""
Trakt list chunking service

Endpoint:
  GET /list/<list_id>?start=YYYY-MM-DD&step=<interval_days>&chunk=<limit>

Reads and stores tokens in a JSON config file (default: /app/config.json).
Token refresh / auth flows use the Trakt OAuth token endpoint.

Author: Generated for user
"""

import os
import time
import json
import math
import logging
from datetime import datetime, timezone, timedelta
from threading import Lock

import requests
from flask import Flask, jsonify, request, abort

CONFIG_PATH = os.environ.get("CONFIG_PATH", "config.json")
TRAKT_TOKEN_URL = "https://api.trakt.tv/oauth/token"
TRAKT_LIST_URL_TEMPLATE = "https://api.trakt.tv/lists/{list_id}/items"

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("trakt-chunker")

app = Flask(__name__)
_config_lock = Lock()


def load_config():
    if not os.path.exists(CONFIG_PATH):
        logger.warning("Config file not found at %s", CONFIG_PATH)
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            logger.exception("Failed to parse config file")
            return {}


def save_config(cfg: dict):
    dirname = os.path.dirname(CONFIG_PATH)
    if dirname and not os.path.exists(dirname):
        os.makedirs(dirname, exist_ok=True)
    # atomic-ish write
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)
    logger.debug("Config saved to %s", CONFIG_PATH)


def token_is_valid(cfg: dict) -> bool:
    access = cfg.get("access_token")
    expires_at = cfg.get("expires_at") 
    if not access or not expires_at:
        return False
    try:
        return time.time() < float(expires_at) - 5 
    except Exception:
        return False


def store_tokens(cfg: dict, resp_json: dict):
    access = resp_json.get("access_token")
    refresh = resp_json.get("refresh_token")
    expires_in = resp_json.get("expires_in")
    created_at = resp_json.get("created_at", int(time.time()))
    if access:
        cfg["access_token"] = access
    if refresh:
        cfg["refresh_token"] = refresh
    if expires_in is not None:
        try:
            cfg["expires_at"] = int(created_at) + int(expires_in)
        except Exception:
            cfg["expires_at"] = int(time.time()) + int(expires_in)
    cfg["_last_token_response"] = {
        k: v for k, v in resp_json.items() if k not in ("access_token", "refresh_token")
    }


def request_token_via_refresh(cfg: dict) -> dict:
    logger.info("Attempting token refresh with refresh_token.")
    body = {
        "grant_type": "refresh_token",
        "client_id": cfg.get("client_id"),
        "client_secret": cfg.get("client_secret"),
        "refresh_token": cfg.get("refresh_token"),
        "redirect_uri": cfg.get("redirect_uri"),
    }
    r = requests.post(TRAKT_TOKEN_URL, json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def ensure_access_token():
    with _config_lock:
        cfg = load_config()
        if token_is_valid(cfg):
            logger.debug("Access token found and valid.")
            return cfg.get("access_token"), cfg

        refresh = cfg.get("refresh_token")
        if refresh:
            try:
                resp = request_token_via_refresh(cfg)
                store_tokens(cfg, resp)
                save_config(cfg)
                logger.info("Token refreshed successfully.")
                return cfg.get("access_token"), cfg
            except requests.HTTPError as e:
                logger.warning("Refresh token flow failed: %s", e)
            except Exception:
                logger.exception("Unexpected error during refresh flow")

        # No way to obtain tokens
        logger.error("No valid access or refresh token available.")
        raise RuntimeError(
            "No valid access or refresh token available. Please update config with refresh_token."
        )


def fetch_list_items(list_id: str, access_token: str):
    url = TRAKT_LIST_URL_TEMPLATE.format(list_id=list_id)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "trakt-api-version": "2",
    }
    cfg = load_config()
    client_id = cfg.get("client_id")
    if client_id:
        headers["trakt-api-key"] = client_id

    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def transformResponse(items, mediaType):
    transformed = []
    for item in items:
        try:
            if item.get("type") != mediaType:
                continue

            media = item.get(mediaType, {})
            idDict = media.get("ids", {})

            transformed.append({ 
                "id": int(idDict.get("tmdb") or 0),
                "title": media.get('title'),
                "tmdbId": int(idDict.get("tmdb") or 0),
                "tvdbId": int(idDict.get("tvdb") or 0),
                "imdbId": idDict.get("imdb"),
            })
        except Exception:
            logger.exception("Skipping item due to unexpected structure: %s", item)
    return transformed


def compute_chunk_for_now(start_date_str: str, interval_days: int, limit: int, total_len: int):
    try:
        start_dt = datetime.fromisoformat(start_date_str)
    except Exception:
        start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
    start_dt = start_dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)

    if interval_days <= 0:
        raise ValueError("interval (step) must be positive")

    delta = now - start_dt
    intervals_elapsed = math.floor(delta.total_seconds() / (interval_days * 86400))
    if intervals_elapsed < 0:
        intervals_elapsed = 0

    start_index = 0
    end_index = (intervals_elapsed * limit) + limit
    return start_index, min(end_index, total_len - 1)


@app.route("/list/<list_id>", methods=["GET"])
def list_handler(list_id):
    """
    Query params:
      - start (required) : YYYY-MM-DD (first day of retrieval)
      - step  (required) : interval (in days) between chunk changes
      - chunk (required) : how many ids per chunk (limit)
      - type (required) : movie or show
    """
    start = request.args.get("start")
    step = request.args.get("step")
    chunk = request.args.get("chunk")
    mediaType = request.args.get("type")
    sortBy = "added"
    direction = request.args.get("dir") or "desc"

    if not start or not step or not chunk or not mediaType:
        return (
            jsonify(
                {
                    "error": "Missing required query params. Required: start (YYYY-MM-DD), step (interval days), chunk (limit), type (movie/show)"
                }
            ),
            400,
        )

    try:
        step_int = int(step)
        chunk_int = int(chunk)
        if chunk_int <= 0 or step_int <= 0:
            raise ValueError
    except ValueError:
        return jsonify({"error": "step and chunk must be positive integers (step=days, chunk=limit)"}), 400

    try:
        access_token, cfg = ensure_access_token()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    except Exception:
        logger.exception("Unexpected error ensuring access token")
        return jsonify({"error": "Failed to ensure access token"}), 500

    try:
        items = fetch_list_items(list_id, access_token)
        if sortBy == "added":
            items.sort(key=lambda x: datetime.fromisoformat(x["listed_at"].replace("Z", "+00:00")), reverse=direction == "asc")
    except requests.HTTPError as e:
        logger.exception("Error fetching trakt list items")
        return jsonify({"error": f"Trakt API error: {e}"}), 502
    except Exception:
        logger.exception("Unexpected error fetching list")
        return jsonify({"error": "Failed to fetch list items"}), 500

    transformed = transformResponse(items, mediaType)
    total = len(transformed)

    start_idx, end_idx = compute_chunk_for_now(start, step_int, chunk_int, total)
    result = transformed[start_idx:end_idx]

    return jsonify(result)


if __name__ == "__main__":
    # Simple dev server
    app.run(host="0.0.0.0", port=5252)
