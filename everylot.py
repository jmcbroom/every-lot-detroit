import asyncio
import datetime
import logging
import os
import random
import requests
import sys
import time
from pathlib import Path

from shapely.geometry import shape, Point

from bearings import compute_viewer_center
from bluesky import post_to_bluesky
from screenshot import capture_screenshots

logger = logging.getLogger("everylot")

# Resolve the project directory so screenshot output paths are absolute.
PROJECT_PATH = str(Path(__file__).parent.absolute())
FEATURE_SERVICE_URL = "https://services2.arcgis.com/qvkbeam7Wirps6zC/arcgis/rest/services/parcel_file_current/FeatureServer/0/query"

# Detroit BaseUnit services used to find a better vantage point for a parcel:
# geocode the address -> street_id + building_id, then pull the matching street
# centerline segment and building footprint. See plan: aim the camera at the
# building, and rank Mapillary images by their distance to the street frontage
# (so we favor front-of-house images over alley/cross-street ones).
GEOCODER_URL = "https://opengis.detroitmi.gov/opengis/rest/services/BaseUnits/BaseUnitGeocoder/GeocodeServer/findAddressCandidates"
CENTERLINE_URL = "https://services2.arcgis.com/qvkbeam7Wirps6zC/ArcGIS/rest/services/BaseUnitFeatures/FeatureServer/1/query"
BUILDINGS_URL = "https://services2.arcgis.com/qvkbeam7Wirps6zC/ArcGIS/rest/services/BaseUnitFeatures/FeatureServer/2/query"

# Geocoder candidates scoring below this are treated as a miss (we fall back to
# the parcel centroid rather than trusting a weak match).
GEOCODE_MIN_SCORE = 80

# How many random parcels to try before giving up for this run. Most random
# parcels won't have a Mapillary before/after pair, so we keep sampling until
# one does (or we run out of attempts).
MAX_PARCEL_ATTEMPTS = 15

# Hard ceiling on the headless-browser screenshot step so a hung Mapillary
# viewer can't stall the whole run (the missing-file check then skips the parcel).
SCREENSHOT_TIMEOUT = 120


class SkipParcel(Exception):
    """Raised when a randomly chosen parcel can't yield a valid before/after pair.

    This is an expected, non-fatal outcome: the caller should simply try another
    random parcel rather than failing the run.
    """


def parcel_attr(props, key, default="Unknown"):
    """Return a parcel attribute for display, substituting a default for
    missing or blank values so a sparse parcel still produces a clean post."""
    value = props.get(key)
    if value in (None, ""):
        return default
    return value


def get_parcel_count():
    """Return the total number of parcels in the feature service."""
    params = {"where": "1=1", "returnCountOnly": "true", "f": "json"}
    response = requests.get(FEATURE_SERVICE_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()["count"]


def get_parcel_count_with_retry(attempts=3):
    """Fetch the parcel count, retrying transient network errors with backoff.

    This is the one fetch that runs before the per-parcel retry loop, so a single
    transient hiccup here would otherwise crash the whole run before it starts.
    """
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return get_parcel_count()
        except requests.exceptions.RequestException as e:
            last_error = e
            logger.warning(f"Parcel count fetch attempt {attempt}/{attempts} failed: {e}")
            if attempt < attempts:
                time.sleep(2 ** attempt)
    raise last_error


def get_random_parcel(parcel_count):
    """Fetch a parcel at a random offset within the feature service.

    Selecting by offset (rather than guessing a possibly-nonexistent ObjectId)
    guarantees a real parcel, so every attempt is spent on the part that matters:
    whether the parcel has a usable before/after image pair. ArcGIS requires an
    orderByFields for stable paging when resultOffset is used.
    """
    offset = random.randint(0, parcel_count - 1)

    params = {
        "outFields": "*",
        "where": "1=1",
        "orderByFields": "ObjectId ASC",
        "resultOffset": offset,
        "resultRecordCount": 1,
        "f": "geojson",
    }

    response = requests.get(FEATURE_SERVICE_URL, params=params, timeout=30)
    response.raise_for_status()

    features = response.json().get("features", [])
    if not features:
        raise SkipParcel(f"no parcel at offset {offset}")
    return features[0]


def geocode_parcel(address):
    """Geocode a parcel address via the Detroit BaseUnit geocoder.

    Returns a dict {street_id, building_id, location, score} for the top
    candidate, or None if there is no candidate clearing GEOCODE_MIN_SCORE or on
    any network/parse error. Callers fall back to the parcel centroid on None.
    """
    params = {"SingleLine": address, "outFields": "*", "f": "json"}

    try:
        response = requests.get(GEOCODER_URL, params=params, timeout=30)
        response.raise_for_status()
        candidates = response.json().get("candidates", [])
    except (requests.exceptions.RequestException, ValueError) as e:
        logger.warning(f"Geocoder error for {address!r}: {e}")
        return None

    if not candidates:
        logger.info(f"No geocoder candidates for {address!r}")
        return None

    top = candidates[0]
    score = top.get("score", 0)
    if score < GEOCODE_MIN_SCORE:
        logger.info(f"Geocoder match for {address!r} too weak (score {score})")
        return None

    attributes = top.get("attributes", {})
    location = top.get("location", {})
    return {
        "street_id": attributes.get("street_id"),
        "building_id": attributes.get("building_id"),
        "location": (location.get("x"), location.get("y")),
        "score": score,
    }


def get_building_centroid(building_id):
    """Return the WGS84 centroid (shapely Point) of a building polygon, or None."""
    params = {
        "where": f"building_id={building_id}",
        "outFields": "building_id",
        "f": "geojson",
    }

    try:
        response = requests.get(BUILDINGS_URL, params=params, timeout=30)
        response.raise_for_status()
        features = response.json().get("features", [])
    except (requests.exceptions.RequestException, ValueError) as e:
        logger.warning(f"Building lookup error for building_id={building_id}: {e}")
        return None

    if not features:
        return None
    return shape(features[0]["geometry"]).centroid


def get_street_segment(street_id, near_point):
    """Return the centerline segment (shapely LineString) for street_id nearest
    to near_point, or None if nothing is returned / on error.

    A street_id can span several block segments, so we pick the one closest to
    near_point (the building or parcel centroid).
    """
    params = {
        "where": f"street_id={street_id}",
        "outFields": "full_street_name",
        "f": "geojson",
    }

    try:
        response = requests.get(CENTERLINE_URL, params=params, timeout=30)
        response.raise_for_status()
        features = response.json().get("features", [])
    except (requests.exceptions.RequestException, ValueError) as e:
        logger.warning(f"Centerline lookup error for street_id={street_id}: {e}")
        return None

    # Collect individual LineStrings; flatten MultiLineStrings defensively.
    segments = []
    for feature in features:
        geometry = shape(feature["geometry"])
        if geometry.geom_type == "MultiLineString":
            segments.extend(geometry.geoms)
        else:
            segments.append(geometry)

    if not segments:
        return None
    return min(segments, key=lambda seg: seg.distance(near_point))


def frontage_point(segment, near_point):
    """Nearest point on the street segment to near_point (the building/parcel centroid)."""
    return segment.interpolate(segment.project(near_point))


def get_mapillary_images(lon: float, lat: float, max_results: int = 1000):
    """
    Query Mapillary API for images near a given point.

    Args:
        lon: Longitude
        lat: Latitude
        max_results: Maximum number of images to return

    Returns:
        List of Mapillary images near the parcel centroid
    """

    # Mapillary API requires an access token
    access_token = os.environ.get("MAPILLARY_ACCESS_TOKEN", None)
    if not access_token:
        raise Exception("Error: MAPILLARY_ACCESS_TOKEN environment variable not set")

    # Mapillary API endpoint for image search
    url = "https://graph.mapillary.com/images"

    # a very small distance in degrees to search around
    degree_distance = 0.0005

    # Parameters for the Mapillary Image API request
    params = {
        "access_token": access_token,
        "fields": "id,captured_at,computed_geometry,geometry,computed_compass_angle,computed_rotation,sequence",
        "is_pano": "true",
        "limit": max_results,

        # bbox = centroid +- degree_distance
        "bbox": f"{lon-degree_distance},{lat-degree_distance},{lon+degree_distance},{lat+degree_distance}",
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if "data" in data and len(data["data"]) > 0:
            logger.info(f"Found {len(data['data'])} Mapillary images within {degree_distance}deg of parcel centroid")
            return data["data"]
        else:
            logger.info(
                f"No Mapillary images found within {degree_distance}deg of parcel centroid"
            )
            return []
    except requests.exceptions.RequestException as e:
        logger.warning(f"Error querying Mapillary API: {e}")
        return []


def get_closest_images(images, anchor):
    """
    Get the closest image for each sequence and the overall closest image.

    Parameters:
    - images: List of Mapillary images
    - anchor: Shapely Point to measure distance from (the street frontage point
      when available, otherwise the building or parcel centroid)

    Returns:
    - Dictionary with the closest image for each sequence
    - Distance to the overall closest image
    """
    # Create a dictionary to store the closest image for each sequence
    sequences = {}
    closest_image_distance = None

    # Loop through the images and find the closest image for each sequence
    for i in images:
        # Compute distance from the anchor; assign to image & update closest image if needed
        distance = anchor.distance(shape(i.get("computed_geometry", i.get("geometry", {}))))
        i["distance"] = distance
        if closest_image_distance is None or distance < closest_image_distance:
            closest_image_distance = distance

        # Keep the closest image seen so far for each sequence.
        if i["sequence"] not in sequences or distance < sequences[i["sequence"]]["distance"]:
            sequences[i["sequence"]] = i

    return sequences, closest_image_distance


def image_coordinates(image):
    """Return an image's coordinates, preferring computed_geometry over geometry."""
    geometry = image.get("computed_geometry", image.get("geometry", {}))
    return geometry["coordinates"]


def prepare_post(parcel_count):
    """Pick a random parcel and assemble the before/after post data.

    Raises SkipParcel if the parcel can't produce a valid before/after pair.
    Returns a dict with message_text, reply_text, image_paths and image_alt_texts.
    """
    # Get a random parcel and log information about it
    parcel = get_random_parcel(parcel_count)
    props = parcel["properties"]

    # The ObjectId is the selection key and is used to name the screenshot
    # files; without it we can't proceed, so skip rather than build bad paths.
    object_id = props.get("ObjectId")
    if object_id is None:
        raise SkipParcel("parcel has no ObjectId")

    # Address can be missing/blank on some parcels; keep the raw value for
    # geocoding (only worth attempting when present) and a display version for
    # the post text and alt text.
    address = props.get("address") or ""
    display_address = address or "Unknown address"

    logger.info(f"Parcel ID: {object_id}")
    logger.info(f"Address: {display_address}")

    # build up the reply text
    reply_text = []
    parcel_id = props.get("parcel_id")
    if parcel_id:
        reply_text.append(
            f"Parcel info: https://baseunits.detroitmi.gov/map?id={parcel_id}&layer=parcel"
        )

    # Compute the parcel's centroid. This is the universal fallback anchor for
    # both jobs below if the geocode/lookups don't pan out.
    shapely_geometry = shape(parcel["geometry"])
    centroid = shapely_geometry.centroid

    # Resolve two purpose-built anchors from a single geocode of the address:
    #   aim_target       - where the camera points (the building, ideally)
    #   selection_anchor - what image proximity is ranked against (the street
    #                      frontage, so front-of-house images beat alley ones)
    # Each step degrades gracefully to the centroid so we always still post.
    aim_target = centroid
    selection_anchor = centroid

    geo = geocode_parcel(address) if address else None
    if geo:
        if geo["building_id"] is not None:
            building_centroid = get_building_centroid(geo["building_id"])
            if building_centroid is not None:
                aim_target = building_centroid

        # Project the (building, else parcel) centroid onto the matched street
        # segment to get the on-street point in front of the property.
        project_from = aim_target
        if geo["street_id"] is not None:
            segment = get_street_segment(geo["street_id"], project_from)
            if segment is not None:
                selection_anchor = frontage_point(segment, project_from)

    logger.info(f"Aim target: {aim_target.x}, {aim_target.y}")
    logger.info(f"Selection anchor: {selection_anchor.x}, {selection_anchor.y}")

    # The Mapillary bbox stays centered on the parcel centroid: it is a coarse
    # retrieval net (~55m), and the frontage re-anchoring happens during ranking
    # below. (A deep lot whose frontage is >~55m from the centroid is the rare
    # case where expanding/recentering this bbox could help.)
    images = get_mapillary_images(centroid.x, centroid.y)
    if not images:
        raise SkipParcel("no Mapillary images near parcel")

    # sort images by capture date
    images = sorted(images, key=lambda x: -1 * x["captured_at"])

    # Rank/select images by proximity to the selection anchor (street frontage
    # when available). Pure re-ranking — the existing relative 2x-distance filter
    # below then naturally drops far alley/cross-street images. (A hard
    # `segment.distance(image) < threshold` drop could be added here if
    # re-ranking ever proves insufficient.)
    sequences, closest_image_distance = get_closest_images(images, selection_anchor)

    logger.info(f"Number of sequences: {len(sequences)}")
    if not sequences:
        raise SkipParcel("no usable image sequences near parcel")

    # sort sequences by distance
    max_dist_filtered = dict(
        sorted(sequences.items(), key=lambda x: x[1]["distance"])
    )

    # filter down to 2x closest image distance
    max_dist_filtered = {
        k: v for k, v in max_dist_filtered.items() if v["distance"] < (closest_image_distance * 2)
    }

    # filter down to the closest 66% of sequences, but always keep at least one
    keep = max(1, int(len(max_dist_filtered) / 1.5))
    max_dist_filtered = dict(list(max_dist_filtered.items())[:keep])

    # re-sort by captured date
    max_dist_filtered = dict(
        sorted(max_dist_filtered.items(), key=lambda x: x[1]["captured_at"] * -1)
    )

    sequence_keys = list(max_dist_filtered.keys())
    logger.info(sequence_keys)
    if not sequence_keys:
        raise SkipParcel("no candidate sequences after filtering")
    first_key = sequence_keys[0]

    # find the closest key to the first key using the distance between their coordinates
    closest_key = None
    closest_distance = None

    first_key_shape = shape(max_dist_filtered[first_key].get("computed_geometry", max_dist_filtered[first_key].get("geometry", {})))

    for s, i in max_dist_filtered.items():

        image_geometry = shape(i.get("computed_geometry", i.get("geometry", {})))

        if s == first_key:
            continue

        # it should be at least 3 years apart
        if abs(i["captured_at"] - max_dist_filtered[first_key]["captured_at"]) < (3 * 365 * 24 * 60 * 60 * 1000):
            continue

        distance = image_geometry.distance(first_key_shape)

        if closest_distance is None or distance < closest_distance:
            logger.info(f"New closest distance: {distance} on image {i['id']}")
            closest_distance = distance
            closest_key = s

    logger.info(f"Closest image to first image: {closest_key}")

    # No image at least 3 years apart from the first; this parcel can't make a
    # before/after comparison, so move on to another parcel.
    if closest_key is None:
        raise SkipParcel("no before/after pair at least 3 years apart")

    logger.info(f"Mapillary link: https://www.mapillary.com/app/?pKey={max_dist_filtered[closest_key]['id']}")

    # Collect the two comparison shots to capture together in one browser below.
    shots = []

    for s, i in max_dist_filtered.items():

        coordinates = image_coordinates(i)

        logger.info(f"Sequence: {s}")
        logger.info(f"Image ID: {i['id']}")
        logger.info(f"Captured at: {datetime.datetime.fromtimestamp(i['captured_at'] / 1000).strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Distance: {i['distance']}")
        logger.info(f"Computed geometry: {coordinates}")

        # Compute the center coordinates for the Mapillary viewer, aiming the
        # panorama at the building (falls back to the parcel centroid). An image
        # without a compass angle can't be aimed: skip the whole parcel if it's
        # one of the two comparison images, otherwise just ignore that image.
        try:
            computed_center = compute_viewer_center(
                i, coordinates, [aim_target.x, aim_target.y]
            )
        except ValueError as e:
            if s in (first_key, closest_key):
                raise SkipParcel(f"image {i['id']} has no compass angle: {e}")
            continue

        logger.info(f"Mapillary link: https://www.mapillary.com/app/?pKey={i['id']}&focus=photo&x={str(computed_center[0])}&y={str(computed_center[1])}")

        # skip screenshotting except for comparison photos
        if s not in [first_key, closest_key]:
            continue

        # queue this image for the shared-browser screenshot pass below
        shots.append(
            (
                i["id"],
                computed_center[0],
                computed_center[1],
                f"{PROJECT_PATH}/{object_id}_{i['captured_at']}.png",
            )
        )

        # create image date & add mapillary link to the reply
        formatted_date = datetime.datetime.fromtimestamp(
            i["captured_at"] / 1000
        ).strftime("%Y-%m-%d")
        mapillary_link = f"{formatted_date}: https://www.mapillary.com/app/?pKey={i['id']}&focus=photo&x={str(computed_center[0])}&y={str(computed_center[1])}"
        reply_text.append(mapillary_link)

    # Capture both comparison screenshots in a single browser session. Failures
    # (timeouts, render errors) are tolerated here; the missing-file check below
    # turns a missing screenshot into a SkipParcel so we try another parcel.
    try:
        asyncio.run(
            asyncio.wait_for(capture_screenshots(shots), timeout=SCREENSHOT_TIMEOUT)
        )
    except Exception as e:
        logger.warning(f"Screenshot capture failed: {e}")

    # Format attributes for main message text
    after_capture_date = datetime.datetime.fromtimestamp(
        max_dist_filtered[first_key]["captured_at"] / 1000
    ).strftime("%b %d %Y")
    before_capture_date = datetime.datetime.fromtimestamp(
        max_dist_filtered[closest_key]["captured_at"] / 1000
    ).strftime("%b %d %Y")
    year_built = parcel_attr(props, "year_built")
    zoning_district = parcel_attr(props, "zoning_district")
    tax_status = parcel_attr(props, "tax_status")

    # Create the main message text
    message_text = f"""{display_address}
Parcel ID: {parcel_attr(props, "parcel_id")}
Year built: {year_built}
Zoned {zoning_district}
Tax status: {tax_status}
Image dates: {before_capture_date} on left; {after_capture_date} on right"""
    logger.info(message_text)

    logger.info("\n".join(reply_text))

    # Create image paths & alt text
    image_paths = [
        f"{PROJECT_PATH}/{object_id}_{max_dist_filtered[closest_key]['captured_at']}.png",
        f"{PROJECT_PATH}/{object_id}_{max_dist_filtered[first_key]['captured_at']}.png",
    ]

    # Screenshot capture can fail (e.g. a Mapillary/network hiccup or timeout),
    # leaving us without the images we need. Treat that as a skip so we try
    # another parcel rather than failing on a missing file at post time.
    missing = [p for p in image_paths if not os.path.exists(p)]
    if missing:
        raise SkipParcel(f"screenshot(s) not produced: {missing}")

    image_alt_texts = [
        f"Street view imagery of {display_address} captured on {before_capture_date}",
        f"Street view imagery of {display_address} captured on {after_capture_date}",
    ]

    return {
        "message_text": message_text,
        "reply_text": reply_text,
        "image_paths": image_paths,
        "image_alt_texts": image_alt_texts,
    }


if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    # The parcel count doesn't change within a run, so fetch it once and reuse
    # it across attempts.
    parcel_count = get_parcel_count_with_retry()

    post_data = None
    for attempt in range(1, MAX_PARCEL_ATTEMPTS + 1):
        logger.info(f"\n=== Attempt {attempt}/{MAX_PARCEL_ATTEMPTS} ===")
        try:
            post_data = prepare_post(parcel_count)
            break
        except SkipParcel as e:
            logger.info(f"Skipping parcel: {e}")
        except requests.exceptions.RequestException as e:
            logger.warning(f"Network error while preparing parcel: {e}")

    if post_data is None:
        # Couldn't find a postable parcel this run. This is an expected outcome
        # (most parcels have no before/after pair), not a failure, so exit 0 so
        # the scheduled run isn't marked as errored.
        logger.info(
            f"\nNo postable parcel found after {MAX_PARCEL_ATTEMPTS} attempts; "
            "nothing to post this run."
        )
        sys.exit(0)

    try:
        # Post to Bluesky
        response = post_to_bluesky(
            username=os.environ.get("BLUESKY_USERNAME"),
            password=os.environ.get("BLUESKY_PASSWORD"),
            text=post_data["message_text"],
            image_paths=post_data["image_paths"],
            image_alt_texts=post_data["image_alt_texts"],
        )

        logger.info("Initial post to Bluesky successful...")

        # Post a reply using the information in `response`
        reply_to = {
            "uri": response["uri"],
            "cid": response["cid"],
        }
        post_to_bluesky(
            username=os.environ.get("BLUESKY_USERNAME"),
            password=os.environ.get("BLUESKY_PASSWORD"),
            text="\n".join(post_data["reply_text"]),
            reply_to=reply_to,
        )

        logger.info("Reply post to Bluesky successful...")
    finally:
        # Clean up images
        for image_path in post_data["image_paths"]:
            if os.path.exists(image_path):
                os.remove(image_path)
