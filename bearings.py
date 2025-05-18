import math

def wrap_value(value, min_val, max_val):
    """
    Wrap a value to stay within the given range.

    Args:
        value: The value to wrap
        min_val: Minimum allowed value
        max_val: Maximum allowed value

    Returns:
        A value within the specified range
    """
    width = max_val - min_val

    # Use modulo to handle wrapping
    wrapped = ((value - min_val) % width) + min_val

    return wrapped

def calculate_bearing(start_point, end_point):
    """
    Calculate the bearing between two points.
    Equivalent to turf.js bearing function.

    Args:
        start_point: Starting point as [longitude, latitude]
        end_point: Ending point as [longitude, latitude]

    Returns:
        Bearing in degrees (0-360)
    """
    # Convert to radians
    lon1, lat1 = math.radians(start_point[0]), math.radians(start_point[1])
    lon2, lat2 = math.radians(end_point[0]), math.radians(end_point[1])

    # Calculate bearing
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(
        lon2 - lon1
    )

    initial_bearing = math.degrees(math.atan2(y, x))

    # Normalize to 0-360
    compass_bearing = (initial_bearing + 360) % 360

    return compass_bearing

def bearing_to_basic(desired_bearing, node_bearing):
    """
    Convert a desired bearing to a basic X image coordinate for
    a specific node bearing.

    Works only for a full 360 panorama.

    Args:
        desired_bearing: The bearing you want to look at (in degrees)
        node_bearing: The bearing of the node/image (in degrees)

    Returns:
        A basic coordinate on the [0, 1] interval
    """
    # 1. Take difference of desired bearing and node bearing in degrees.
    # 2. Scale to basic coordinates.
    # 3. Add 0.5 because node bearing corresponds to the center
    #    of the image. See
    #    https://mapillary.github.io/mapillary-js/classes/viewer.html
    #    for explanation of the basic coordinate system of an image.
    basic = (desired_bearing - node_bearing) / 360 + 0.5

    # Wrap to a valid basic coordinate (on the [0, 1] interval).
    # Needed when difference between desired bearing and node
    # bearing is more than 180 degrees.
    return wrap_value(basic, 0, 1)

def compute_viewer_center(image, start_point, end_point):
    """
    Compute the center coordinates for a Mapillary viewer based on desired bearing.

    Args:
        node: Mapillary node/image data containing compass angle information
        start_point: Starting point as [longitude, latitude]
        end_point: Ending point as [longitude, latitude]

    Returns:
        A list [x, y] representing the basic coordinates for the viewer center
    """
    # Get the node's compass angle (bearing)
    node_bearing = image.get("computed_compass_angle")
    if node_bearing is None:
        node_bearing = image.get("properties", {}).get("compass_angle")

    if node_bearing is None:
        raise ValueError("Node does not have a compass angle")

    # Calculate bearing between start and end points
    desired_bearing = calculate_bearing(start_point, end_point)

    # Convert to basic coordinates
    basic_x = bearing_to_basic(desired_bearing, node_bearing)
    basic_y = 0.45  # Tilt slightly up

    return [basic_x, basic_y]

