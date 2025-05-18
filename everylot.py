import datetime
import os
import random
import requests
import subprocess
import sys
from pathlib import Path

from shapely.geometry import shape

from bearings import compute_viewer_center
from bluesky import post_to_bluesky

# Use system Python and set PROJECT_PATH to current directory
PYTHON_PATH = sys.executable
PROJECT_PATH = str(Path(__file__).parent.absolute())
FEATURE_SERVICE_URL = "https://services2.arcgis.com/qvkbeam7Wirps6zC/arcgis/rest/services/parcel_file_current/FeatureServer/0/query"

def get_random_parcel():
    # Parameters to get the min and max object_id
    params_min_max = {
        "outFields": "ObjectId",
        "where": "1=1",
        "f": "json",
        "orderByFields": "ObjectId ASC",
        "resultRecordCount": 1,
    }

    # Fetch the minimum object_id
    response_min = requests.get(FEATURE_SERVICE_URL, params=params_min_max)
    data_min = response_min.json()
    min_object_id = data_min["features"][0]["attributes"]["ObjectId"]

    # Fetch the maximum object_id
    params_min_max["orderByFields"] = "ObjectId DESC"
    response_max = requests.get(FEATURE_SERVICE_URL, params=params_min_max)
    data_max = response_max.json()
    max_object_id = data_max["features"][0]["attributes"]["ObjectId"]

    # Generate a random object_id within the range
    random_object_id = random.randint(min_object_id, max_object_id)

    # Parameters to fetch the random parcel
    params_random = {
        "outFields": "*",
        "where": f"ObjectId={random_object_id}",
        "f": "geojson",
    }

    # Fetch the random parcel
    response_random = requests.get(FEATURE_SERVICE_URL, params=params_random)
    data_random = response_random.json()
    return data_random["features"][0]


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
        return []

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
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        if "data" in data and len(data["data"]) > 0:
            print(f"Found {len(data['data'])} Mapillary images within {degree_distance}deg of parcel centroid")
            return data["data"]
        else:
            print(
                f"No Mapillary images found within {degree_distance}deg of parcel centroid"
            )
            return []
    except requests.exceptions.RequestException as e:
        print(f"Error querying Mapillary API: {e}")
        return []


def get_closest_images(images, centroid):
    """
    Get the closest image for each sequence and the overall closest image.

    Parameters:
    - images: List of Mapillary images
    - parcel_centroid: Shapely Point object

    Returns:
    - Dictionary with the closest image for each sequence
    - Distance to the overall closest image
    """
    # Create a dictionary to store the closest image for each sequence
    sequences = {}
    closest_image_distance = None

    # Loop through the images and find the closest image for each sequence
    for i in images:
        # Compute distance from parcel centroid; assign to image & update closest image if needed
        distance = centroid.distance(shape(i.get("computed_geometry", i.get("geometry", {}))))
        i["distance"] = distance
        if closest_image_distance is None or distance < closest_image_distance:
            closest_image_distance = distance

        # Add image to dictionary if it's the closest for its sequence
        if i["sequence"] not in sequences.keys():
            sequences[i["sequence"]] = i
        # If the image is closer than the current closest image for the sequence, update
        else:
            if distance < sequences[i["sequence"]]["distance"]:
                sequences[i["sequence"]] = i
            else:
                continue

    return sequences, closest_image_distance


if __name__ == "__main__":

    # Get a random parcel and log information about it
    parcel = get_random_parcel()

    print(f"Parcel ID: {parcel['properties']['ObjectId']}")
    print(f"Address: {parcel['properties']['address']}")

    # build up the reply text
    reply_text = [
        f"Parcel info: https://base-unit-tools.netlify.app/map?id={parcel['properties']['parcel_id']}&layer=parcel"
    ]

    # Compute the parcel's centroid and get Mapillary images near it
    shapely_geometry = shape(parcel["geometry"])
    centroid = shapely_geometry.centroid

    images = get_mapillary_images(centroid.x, centroid.y)

    # sort images by capture date
    images = sorted(images, key=lambda x: -1 * x["captured_at"])

    # Create a dictionary to store the closest image for each sequence, and track overall closest image
    sequences, closest_image_distance = get_closest_images(images, centroid)

    print("Number of sequences:", len(sequences))

    # sort sequences by distance
    max_dist_filtered = dict(
        sorted(sequences.items(), key=lambda x: x[1]["distance"])
    )

    # filter down to 2x closest image distance
    max_dist_filtered = {
        k: v for k, v in max_dist_filtered.items() if v["distance"] < (closest_image_distance * 2)
    }

    # filter down to the closest 66% of sequences
    max_dist_filtered = dict(list(max_dist_filtered.items())[:int(len(max_dist_filtered) / 1.5)])

    # re-sort by captured date
    max_dist_filtered = dict(
        sorted(max_dist_filtered.items(), key=lambda x: x[1]["captured_at"] * -1)
    )

    sequence_keys = max_dist_filtered.keys()
    print(sequence_keys)
    first_key = list(sequence_keys)[0]

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
            print(f"New closest distance: {distance} on image {i['id']}")
            closest_distance = distance
            closest_key = s
    
    print(f"Closest iamge to first image: {closest_key}")
    print(f"Mapillary link: https://www.mapillary.com/app/?pKey={max_dist_filtered[closest_key]['id']}")

    for s, i in max_dist_filtered.items():
        
        print("\n")
        print(f"Sequence: {s}")
        print(f"Image ID: {i['id']}")
        print(f"Captured at: {datetime.datetime.fromtimestamp(i['captured_at'] / 1000).strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Distance: {i['distance']}")
        print(f"Computed geometry: {i['computed_geometry']['coordinates']}")

        # Compute the center coordinates for the Mapillary viewer
        computed_center = compute_viewer_center(
            i, i["computed_geometry"]["coordinates"], [centroid.x, centroid.y]
        )

        print(f"Mapillary link: https://www.mapillary.com/app/?pKey={i['id']}&focus=photo&x={str(computed_center[0])}&y={str(computed_center[1])}")

        # skip screenshotting except for comparison photos
        if s not in [first_key, closest_key]:
            continue

        # call screenshot.py with imagekey, centerx, centery params
        subprocess.run(
            [
                PYTHON_PATH,
                f"{PROJECT_PATH}/screenshot.py",
                "--image-key",
                i["id"],
                "--centerx",
                str(computed_center[0]),
                "--centery",
                str(computed_center[1]),
                "--output",
                f"{parcel['properties']['ObjectId']}_{i['captured_at']}.png",
            ]
        )

        # create image date & add mapillary link to the reply
        formatted_date = datetime.datetime.fromtimestamp(
            i["captured_at"] / 1000
        ).strftime("%Y-%m-%d")
        mapillary_link = f"{formatted_date}: https://www.mapillary.com/app/?pKey={i['id']}&focus=photo&x={str(computed_center[0])}&y={str(computed_center[1])}"
        reply_text.append(mapillary_link)

    # Format attributes for main message text
    after_capture_date = datetime.datetime.fromtimestamp(
        max_dist_filtered[first_key]["captured_at"] / 1000
    ).strftime("%b %d %Y")
    before_capture_date = datetime.datetime.fromtimestamp(
        max_dist_filtered[closest_key]["captured_at"] / 1000
    ).strftime("%b %d %Y")
    address = parcel["properties"]["address"]
    parcel_id = parcel["properties"]["parcel_id"]
    year_built = parcel["properties"]["year_built"]
    zoning_district = parcel["properties"]["zoning_district"]
    tax_status = parcel["properties"]["tax_status"]

    # Create the main message text
    message_text = f"""{address}
Parcel ID: {parcel_id}
Year built: {year_built}
Zoned {zoning_district}
Tax status: {tax_status}
Image dates: {before_capture_date} on left; {after_capture_date} on right"""
    print(message_text)

    print("\n".join(reply_text))

    # Create image paths & alt text
    image_paths = [
        f"{PROJECT_PATH}/{parcel['properties']['ObjectId']}_{max_dist_filtered[closest_key]['captured_at']}.png",
        f"{PROJECT_PATH}/{parcel['properties']['ObjectId']}_{max_dist_filtered[first_key]['captured_at']}.png",
    ]
    image_alt_texts = [
        f"Street view imagery of {parcel['properties']['address']} captured on {before_capture_date}",
        f"Street view imagery of {parcel['properties']['address']} captured on {after_capture_date}",
    ]

    # Post to Bluesky
    response = post_to_bluesky(
        username=os.environ.get("BLUESKY_USERNAME"),
        password=os.environ.get("BLUESKY_PASSWORD"),
        text=message_text,
        image_paths=image_paths,
        image_alt_texts=image_alt_texts,
    )

    # print(response)
    print("Initial post to Bluesky successful...")

    # Post a reply using the information in `response`
    reply_to = {
        "uri": response["uri"],
        "cid": response["cid"],
    }
    reply_response = post_to_bluesky(
        username=os.environ.get("BLUESKY_USERNAME"),
        password=os.environ.get("BLUESKY_PASSWORD"),
        text="\n".join(reply_text),
        reply_to=reply_to,
    )

    print("Reply post to Bluesky successful...")

    # Clean up images
    for image_path in image_paths:
        os.remove(image_path)
