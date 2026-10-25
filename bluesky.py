import logging
import os
import random
import re
import time

import httpx
from atproto import Client
from atproto_client.exceptions import BadRequestError, InvokeTimeoutError, NetworkError
from typing import List, Dict

logger = logging.getLogger("everylot.bluesky")

# The atproto/Bluesky API occasionally times out (e.g. while uploading an image
# blob). These are transient, so retry a few times with backoff before failing.
RETRYABLE_ERRORS = (
    InvokeTimeoutError,
    NetworkError,
    httpx.TimeoutException,
    httpx.NetworkError,
)
MAX_POST_ATTEMPTS = 4

# Public (unauthenticated) AppView endpoint for reading an account's posts.
AUTHOR_FEED_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"

# Base32-sortable alphabet used by atproto timestamp identifiers (TIDs).
TID_ALPHABET = "234567abcdefghijklmnopqrstuvwxyz"


def generate_tid() -> str:
    """Generate an atproto TID (13-char, time-sortable record key).

    53 bits of microseconds since the epoch followed by a 10-bit random clock id,
    encoded big-endian in TID_ALPHABET.
    """
    value = ((time.time_ns() // 1000) << 10) | random.getrandbits(10)
    chars = []
    for _ in range(13):
        chars.append(TID_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def get_post_texts_since(actor: str, since: str, max_pages: int = 50) -> List[str]:
    """Return the text of actor's own top-level posts created on or after since.

    actor is a handle or DID; since is a 'YYYY-MM-DD' date (UTC). Reads the
    public author feed newest-first, so paging stops at the first page reaching
    back past since. Reposts and replies are excluded. Raises httpx.HTTPError
    on network/API failure.
    """
    texts = []
    cursor = None
    for _ in range(max_pages):
        params = {"actor": actor, "limit": 100, "filter": "posts_no_replies"}
        if cursor:
            params["cursor"] = cursor
        response = httpx.get(AUTHOR_FEED_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        reached_since = False
        for item in data.get("feed", []):
            if "reason" in item:  # a repost of someone else's post
                continue
            record = item["post"]["record"]
            # createdAt is ISO 8601 UTC, so the date prefix compares as a string.
            if record.get("createdAt", "")[:10] < since:
                reached_since = True
                continue
            texts.append(record.get("text", ""))

        cursor = data.get("cursor")
        if reached_since or not cursor:
            break
    return texts


def parse_urls(text: str) -> List[Dict]:
    spans = []
    # partial/naive URL regex based on: https://stackoverflow.com/a/3809435
    # tweaked to disallow some trailing punctuation
    url_regex = rb"[$|\W](https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*[-a-zA-Z0-9@%_\+~#//=])?)"
    text_bytes = text.encode("UTF-8")
    for m in re.finditer(url_regex, text_bytes):
        spans.append({
            "start": m.start(1),
            "end": m.end(1),
            "url": m.group(1).decode("UTF-8"),
        })
    return spans

# Parse facets from text and resolve the handles to DIDs
def parse_facets(text: str) -> List[Dict]:
    facets = []
    for u in parse_urls(text):
        facets.append({
            "index": {
                "byteStart": u["start"],
                "byteEnd": u["end"],
            },
            "features": [
                {
                    "$type": "app.bsky.richtext.facet#link",
                    "uri": u["url"],
                }
            ],
        })
    return facets

def post_to_bluesky(username, password, text, image_paths=None, image_alt_texts=None, reply_to=None):
    """Post to Bluesky, retrying on transient timeout/network errors.

    A timed-out request may still have created the post, so every attempt uses
    the same record key: a retry first checks whether that record already exists
    (and returns it), and the PDS refuses to create a second record under the
    same key, so a retry can never double-post.

    See _post_to_bluesky for the full parameter documentation.
    """
    rkey = generate_tid()
    last_error = None
    for attempt in range(1, MAX_POST_ATTEMPTS + 1):
        try:
            return _post_to_bluesky(
                username, password, text, image_paths, image_alt_texts, reply_to,
                rkey=rkey, check_existing=attempt > 1,
            )
        except RETRYABLE_ERRORS as e:
            last_error = e
            if attempt == MAX_POST_ATTEMPTS:
                break
            wait = 2 ** attempt
            logger.warning(
                f"Bluesky post attempt {attempt}/{MAX_POST_ATTEMPTS} failed "
                f"({type(e).__name__}); retrying in {wait}s..."
            )
            time.sleep(wait)

    raise last_error


def _get_existing_post(client, rkey):
    """Return the post stored under rkey in our repo, or None if there isn't one."""
    try:
        return client.com.atproto.repo.get_record(
            {"repo": client.me.did, "collection": "app.bsky.feed.post", "rkey": rkey}
        )
    except BadRequestError:
        # The PDS answers a missing record with 400 RecordNotFound.
        return None


def _post_to_bluesky(username, password, text, image_paths=None, image_alt_texts=None, reply_to=None, rkey=None, check_existing=False):
    """
    Post to Bluesky with text and up to two images.

    Parameters:
    - username: Bluesky handle (e.g., 'username.bsky.social')
    - password: Your Bluesky app password
    - text: The text content of your post
    - image_paths: List of paths to image files
    - image_alt_texts: List of alt text for images
    - reply_to: Dictionary containing reply information with keys:
                - 'uri': The URI of the post to reply to
                - 'cid': The CID of the post to reply to
                - 'author': The DID of the author of the post to reply to
    - rkey: Record key to create the post under (generated if omitted)
    - check_existing: If True, return the post already stored under rkey
                      (left by an earlier attempt that timed out) instead of
                      creating a new one

    Returns:
    - Response object from the Bluesky API
    """
    # Validate inputs
    if not username or not password:
        raise ValueError("Username and password are required")

    if not text and not image_paths:
        raise ValueError("Either text or at least one image is required for a post")

    image_alt_texts = image_alt_texts or []

    # Initialize the client and login
    client = Client()
    client.login(username, password)

    rkey = rkey or generate_tid()
    if check_existing:
        existing = _get_existing_post(client, rkey)
        if existing is not None:
            logger.info(f"Post {rkey} already exists from an earlier attempt; not reposting")
            return existing

    # Prepare images if provided
    image_uploads = []
    if image_paths:
        for idx, image_path in enumerate(image_paths):
            if not os.path.exists(image_path):
                raise FileNotFoundError(f"Image file not found: {image_path}")

            # Upload the image to Bluesky
            with open(image_path, "rb") as f:
                image_data = f.read()
                upload = client.com.atproto.repo.upload_blob(image_data)
                image_uploads.append(
                    {
                        "image": upload.blob,
                        "alt": image_alt_texts[idx] if idx < len(image_alt_texts) else None,
                    }
                )

    # Prepare the record
    record = {
        "text": text,
        "createdAt": client.get_current_time_iso()
    }

    facets = parse_facets(text)
    if facets:
        record["facets"] = facets

    if image_uploads:
        record["embed"] = {
            "$type": "app.bsky.embed.images",
            "images": image_uploads
        }
    
    # Add reply data if replying to a post
    if reply_to:
        if not all(key in reply_to for key in ['uri', 'cid']):
            raise ValueError("Reply must include 'uri' and 'cid' keys")
            
        record["reply"] = {
            "root": {
                "uri": reply_to["uri"],
                "cid": reply_to["cid"]
            },
            "parent": {
                "uri": reply_to["uri"],
                "cid": reply_to["cid"]
            }
        }
        
    # Create the post
    response = client.com.atproto.repo.create_record(
        {
            "repo": client.me.did,
            "collection": "app.bsky.feed.post",
            "rkey": rkey,
            "record": record,
        }
    )

    return response