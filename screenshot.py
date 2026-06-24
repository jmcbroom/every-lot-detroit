import argparse
import asyncio
import logging
import os

from playwright.async_api import async_playwright

logger = logging.getLogger("everylot.screenshot")


async def _shoot(page, image_key, center_x, center_y, output_path):
    """Render one Mapillary image in the given page and screenshot it."""
    html_content = create_mapillary_html(image_key, center_x, center_y)

    # Unique temp file per image so sequential shots don't clash.
    temp_html_path = os.path.join(os.getcwd(), f"temp_page_{image_key}.html")
    with open(temp_html_path, "w") as f:
        f.write(html_content)

    try:
        file_url = f"file://{os.path.abspath(temp_html_path)}"
        await page.goto(file_url)

        # Wait for the viewer to report that the image loaded and the
        # center/zoom were applied (set via window.__mlyReady), rather than
        # blindly sleeping. Then give the panorama tiles a short settle.
        await page.wait_for_function("window.__mlyReady === true", timeout=30000)
        await asyncio.sleep(2)

        await page.screenshot(path=output_path)
        logger.info(f"Screenshot saved to {output_path}")
    finally:
        os.remove(temp_html_path)


async def capture_screenshots(shots):
    """Screenshot a list of Mapillary images, reusing a single browser.

    Args:
        shots: iterable of (image_key, center_x, center_y, output_path) tuples.
    """
    async with async_playwright() as p:
        # One browser launch covers every shot, instead of one per image.
        browser = await p.chromium.launch(headless=True)
        try:
            for image_key, center_x, center_y, output_path in shots:
                page = await browser.new_page(viewport={"width": 700, "height": 700})
                try:
                    await _shoot(page, image_key, center_x, center_y, output_path)
                finally:
                    await page.close()
        finally:
            await browser.close()

def create_mapillary_html(image_key="1012138957500240", center_x=0.5, center_y=0.5):
    """
    Create HTML content with the Mapillary viewer for the specified image.
    
    Args:
        api_key (str): Mapillary API key
        image_key (str): Mapillary image ID to display
    
    Returns:
        str: HTML content with the Mapillary viewer
    """

    api_key=os.environ.get("MAPILLARY_ACCESS_TOKEN")

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Mapillary Viewer</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="https://unpkg.com/mapillary-js@4.1.2/dist/mapillary.js"></script>
        <link rel="stylesheet" href="https://unpkg.com/mapillary-js@4.1.2/dist/mapillary.css">
        <style>
            body {{
                margin: 0;
                padding: 0;
                font-family: Arial, sans-serif;
            }}
            #mly {{
                width: 100%;
                height: 100vh;
            }}
        </style>
    </head>
    <body>
        <div id="mly"></div>
        <script>
            const apiKey = "{api_key}";
            const imageKey = "{image_key}";
            
            const mly = new mapillary.Viewer({{
                accessToken: apiKey,
                container: "mly",
                imageId: imageKey,
                component: {{
                  marker: true,
                  bearing: false,
                  cover: false,
                  attribution: false,
                  sequence: false,
                  cache: true,
                  direction: false,
                }}
            }});

            // Wait for the viewer to be fully loaded before setting center and zoom
            mly.on("image", () => {{

                // Set the center position (x, y in normalized coordinates 0-1)
                mly.setCenter([{center_x}, {center_y}]);

                // Set the zoom level (0 is fully zoomed out)
                mly.setZoom(0.7);

                // Signal to Playwright that the image loaded and the view was
                // positioned, so it can wait for this instead of a fixed sleep.
                window.__mlyReady = true;
            }});
        </script>
    </body>
    </html>
    """ 

async def main():
    parser = argparse.ArgumentParser(description="Take a screenshot of a Mapillary image")
    parser.add_argument("--image-key", required=True, help="Mapillary image ID to display")
    parser.add_argument("--centerx", type=float, default=0.5, help="X coordinate for centering the image")
    parser.add_argument("--centery", type=float, default=0.5, help="Y coordinate for centering the image")
    parser.add_argument("--output", default="screenshot.png", help="Output filename")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    await capture_screenshots(
        [(args.image_key, args.centerx, args.centery, args.output)]
    )

if __name__ == "__main__":
    # Run the async function
    asyncio.run(main())