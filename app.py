"""
app.py — candidate implementation file

Complete the Flask routes for the URL shortener API. The service logic belongs
in shortener.py; the route layer should parse requests, call URLShortener, and
map service outcomes to the required HTTP responses.
"""

from flask import Flask, jsonify, redirect, request
from pydantic import BaseModel, Field, HttpUrl, ValidationError as PydanticValidationError, field_validator
from typing import Annotated
from fastapi import status


from shortener import (
    CodeGenerationError,
    ConflictError,
    ExpiredLinkError,
    NotFoundError,
    PermissionDeniedError,
    URLShortener,
    ValidationError,
    Link,
)

app = Flask(__name__)
app.url_map.strict_slashes = False
u = URLShortener()


def error(message: str, status: int):
    return jsonify({"error": message}), status


def short_url_for(short_code: str) -> str:
    return request.host_url.rstrip("/") + "/" + short_code


class CreateLinkReq(BaseModel):
    long_url: HttpUrl
    custom_code: Annotated[
        str | None, Field(default=None, pattern=r"^[A-Za-z0-9_-]{3,32}$")
    ]
    ttl_seconds: Annotated[float | None, Field(default=None, gt=0)]

    @field_validator("ttl_seconds", mode="before")
    @classmethod
    def reject_boolean_ttl(cls, value):
        if isinstance(value, bool):
            raise ValueError("ttl_seconds must be integer")
        return value

class DeleteLinkReq(BaseModel):
    short_code: Annotated[str, Field(default=None, pattern=r"^[A-Za-z0-9_-]{3,32}$")]
    tok_secret: str


@app.post("/links")
def create_link():
    """
    Create a short link.

    JSON body:
      long_url: required string starting with http:// or https://
      custom_code: optional [A-Za-z0-9_-]{3,32}
      ttl_seconds: optional positive number

    Success: 201 with link metadata, short_url, and owner_token.
    """
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return error("Invalid JSON body", 400)
    try:
        payload = CreateLinkReq.model_validate(data)
    except PydanticValidationError:
        return error("Invalid JSON body", 400)

    try:
        l: Link = u.create(
            payload.long_url.__str__(), payload.custom_code, payload.ttl_seconds
        )
        return {
            "short_code": l.short_code,
            "short_url": short_url_for(l.short_code),
            "long_url": l.long_url,
            "created_at": l.created_at,
            "expires_at": l.expires_at,
            "hit_count": l.hit_count,
            "is_expired": l.is_expired,
            "owner_token": l.owner_token,
        }, 201
    except ValidationError as e:
        return error(str(e), 477)
    except ConflictError as e:
        return error(str(e), 409)
    except CodeGenerationError as e:
        return error(str(e), 500)


@app.get("/<short_code>")
def redirect_link(short_code: str):
    """
    Redirect to the original URL.

    Success: 302 with Location header.
    Missing code: 404.
    Expired code: 410.
    A successful redirect must atomically increment hit_count exactly once.
    """
    try:
        res: str = u.resolve_redirect(short_code)
        return redirect(res, code=302)
    except NotFoundError as e:
        return error("Not Found", 404)
    except ExpiredLinkError as e:
        return error("Gone", 410)


@app.get("/links/<short_code>")
def get_link(short_code: str):
    """Return metadata for a short link, or 404 when it does not exist."""
    l: Link | None = u.get(short_code)
    if l is None:
        return error("Not Found", 404)
    return {
        "short_code": l.short_code,
        "long_url": l.long_url,
        "created_at": l.created_at,
        "expires_at": l.expires_at,
        "hit_count": l.hit_count,
        "is_expired": l.is_expired,
    }


@app.delete("/links/<short_code>")
def delete_link(short_code: str):
    """
    Delete a short link.

    JSON body must contain owner_token.
    Missing token: 400.
    Wrong token: 403.
    Missing code: 404.
    Success: 200 with {"deleted": short_code}.
    """
    data: dict = request.get_json(silent=True)
    sectok = data.get("owner_token")
    try:
        b = u.delete(short_code, sectok)
        if b:
            return {"deleted": short_code}
        else:
            return error("Not Found", 404)
    except ValidationError as e:
        return error("Bad Request", 400)
    except PermissionDeniedError as e:
        return error("Permission Denied", 403)


@app.get("/stats")
def stats():
    """Return aggregate statistics across non-deleted links."""
    return u.get_stats()


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(409)
@app.errorhandler(410)
@app.errorhandler(405)
def handle_http_error(exc):
    return error(getattr(exc, "description", "request failed"), exc.code)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
