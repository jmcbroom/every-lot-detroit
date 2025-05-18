import asyncio
import argparse
import os

from playwright.async_api import async_playwright

from everylot import PROJECT_PATH

async def take_screenshot(html_content, output_path="screenshot.png"):
    """
    Load custom HTML content in a headless browser and take a screenshot using Playwright.
    
    Args:
        html_content (str): HTML content to load
        output_path (str): Path where the screenshot will be saved
    """
    # Create a temporary HTML file
    temp_html_path = os.path.join(os.getcwd(), "temp_page.html")
    with open(temp_html_path, "w") as f:
        f.write(html_content)
    
    async with async_playwright() as p:
        # Launch a headless browser
        browser = await p.chromium.launch(headless=True)
        
        # Create a new browser page
        page = await browser.new_page(viewport={"width": 700, "height": 700})
        
        try:
            # Load the temporary HTML file
            file_url = f"file://{os.path.abspath(temp_html_path)}"
            await page.goto(file_url)
            
            # Wait for network to be idle (ensures JavaScript has executed)
            await page.wait_for_load_state("networkidle")
            
            # Wait a bit longer for the Mapillary viewer to fully load
            await asyncio.sleep(4)
            
            # Take a screenshot
            await page.screenshot(path=output_path)
            print(f"Screenshot saved to {output_path}")
        finally:
            # Clean up
            await browser.close()
            os.remove(temp_html_path)

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
        <link rel="stylesheet" href="https://unpkg.com/mapillary-js@4.0.0/dist/mapillary.css">
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
    
    html_content = create_mapillary_html(args.image_key, args.centerx, args.centery)
    await take_screenshot(html_content, args.output)

if __name__ == "__main__":
    # Run the async function
    asyncio.run(main())