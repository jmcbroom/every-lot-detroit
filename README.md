# Every Lot Detroit (before and after)

This repo contains code for the [everylot.det.city](https://bsky.app/profile/everylot.det.city) Bluesky account, which posts a randomly-selected parcel in the city of Detroit, Michigan, along with two photos of the parcel that show change over time.

## Running the code

1. Set environment variables for the following:
   - `BLUESKY_USERNAME`: Your Bluesky username
   - `BLUESKY_PASSWORD`: Your Bluesky password
   - `MAPILLARY_ACCESS_TOKEN`: Your Mapillary access token

2. Adjust parameters in `everylot.py`:
  - FEATURE_SERVICE_URL: The URL of the feature service containing the parcels.

3. Run the script: `python everylot.py`.

4. You can also deploy this with GitHub Actions: see `.github/workflows/everylot.yml` for an example that posts every 30 minutes. Note that Actions will stop running after 60 days of inactivity.

## License

This project is licensed under the MIT License. See the [LICENSE.md](LICENSE.md) file for details
