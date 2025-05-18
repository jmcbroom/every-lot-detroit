import re
import os

from atproto import Client
from typing import List, Dict

def parse_urls(text: str) -> List[Dict]:
    spans = []
    # partial/naive URL regex based on: https://stackoverflow.com/a/3809435
    # tweaked to disallow some training punctuation
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
                    # NOTE: URI ("I") not URL ("L")
                    "uri": u["url"],
                }
            ],
        })
    return facets

def post_to_bluesky(username, password, text, image_paths=None, image_alt_texts=[], reply_to=[]):
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

    Returns:
    - Response object from the Bluesky API
    """
    # Validate inputs
    if not username or not password:
        raise ValueError("Username and password are required")

    if not text and not image_paths:
        raise ValueError("Either text or at least one image is required for a post")

    # Initialize the client and login
    client = Client()
    client.login(username, password)

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
        
        # For a reply thread deeper than 1 level, you would need to distinguish 
        # between root and parent, but this handles the common case of direct replies

    # Create the post
    response = client.com.atproto.repo.create_record(
        {
            "repo": client.me.did,
            "collection": "app.bsky.feed.post",
            "record": record,
        }
    )

    return response