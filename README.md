
#  URL shortener service

## What you should know

This is a question i found in leetcode projects. The API specifications are same. The implementation is mine. 
Additionally I dockerized it so that I can host it for myself.
Adding a very basic frontend is an ongoing thought but a basic disdain for GUI is appreciated
It was a 1hr sandboxed test and submitted but eventually I tweaked and improvd things afterwards.
Ready to go!
---

##  What it supports

- creating short links
- redirecting short links
- querying link metadata
- deleting short links
- querying aggregate statistics


## API

### Create a short link

`POST /links`

Request body:

```json
{
  "long_url": "https://example.com/a/very/long/page",
  "custom_code": "docs_2026",
  "ttl_seconds": 3600
}
```

- `long_url` is required and must:
  - be a string
  - use `http://` or `https://`
  - include a valid host
- `custom_code` is optional. When provided, it must match: `^[A-Za-z0-9_-]{3,32}$`
- `ttl_seconds` is optional. When provided, it must be a positive non-boolean number.

Processing rules:

- A duplicate custom code returns `409 Conflict`.
- Automatically generated codes matches: `^[A-Za-z0-9]{6,10}$`
- If an auto-generated code collides with an existing short code in the database, retries up to five times. If all attempts fail, returns `500`.
- The same long URL may be shortened more than once. Each request creates a separate short link.
- When `ttl_seconds` is provided, the expiration time is `expires_at = created_at + ttl_seconds`.

> A link is expired when: `expires_at IS NOT NULL AND current_time >= expires_at`

Success response: `201 Created`

```json
{
  "short_code": "docs_2026",
  "short_url": "http://localhost:8080/docs_2026",
  "long_url": "https://example.com/a/very/long/page",
  "created_at": 1760000000.0,
  "expires_at": 1760003600.0,
  "hit_count": 0,
  "is_expired": false,
  "owner_token": "tok_secret"
}
```

### Redirect

`GET /<short_code>`

Return `410 Gone` for an expired link without changing `hit_count`. For an active link, add 1 to `hit_count` and return `302 Found` with the original URL in the `Location` header.

### Get link metadata

`GET /links/<short_code>`

Success response: `200 OK`

```json
{
  "short_code": "docs_2026",
  "long_url": "https://example.com/a/very/long/page",
  "created_at": 1760000000.0,
  "expires_at": 1760003600.0,
  "hit_count": 12,
  "is_expired": false
}
```

> The metadata response must not include `owner_token`.

### Delete a short link

`DELETE /links/<short_code>`

Request body:

```json
{
  "owner_token": "tok_secret"
}
```

| Case | Status code |
|------|-------------|
| Missing `owner_token` | `400 Bad Request` |
| Wrong `owner_token` | `403 Forbidden` |
| Short code not found | `404 Not Found` |

Deletion removes the link completely from the public behavior of the service. It can no longer be fetched or redirected, and it no longer contributes to stats.

Success response: `200 OK`

```json
{
  "deleted": "docs_2026"
}
```

### Stats

`GET /stats`

Success response: `200 OK`

- `total_links`: count of all non-deleted links
- `total_hits`: sum of hit counts across all non-deleted links
- `top_5_links` sort order: `hit_count` descending, then `short_code` ascending for ties

> Expired but non-deleted links are included in stats.

```json
{
  "total_links": 10,
  "total_hits": 384,
  "top_5_links": [
    {
      "short_code": "abc123",
      "long_url": "https://example.com",
      "hit_count": 120
    }
  ]
}
```

## Notes

- Returns every API error as JSON, including malformed request bodies and unsupported methods. The object must have exactly this shape:
  ```json
  {
    "error": "description"
  }
  ```

## Files

The starter project contains:

- `app.py`: Flask route skeleton (complete the implementation)
- `shortener.py`: service layer (complete the implementation)
- `db.py`: completed SQLite connection helper, provided for reference
- `test_client.py`: public end-to-end smoke test
- `test_shortener.py`: public unit tests for the service layer
- `README.md`: this file
- `requirements.txt`: local dependencies

Only edit `app.py` and `shortener.py`.

## Running locally

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Run the unit tests:

```bash
python3 test_shortener.py
```

Run the public end-to-end smoke test:

```bash
python3 test_client.py
```

> The end-to-end test uses Flask's local test client and runs without a separate server process.

To try the API manually, run:

```bash
python3 app.py
```

Be Normal and use
```bash
docker compose up --build
``` (or add yourself to docker group if it yells)


Then send requests to `http://127.0.0.1:8080`.

Example:

```bash
curl -X POST http://127.0.0.1:8080/links \
  -H 'Content-Type: application/json' \
  -d '{"long_url":"https://example.com","custom_code":"demo"}'
```
