import asyncio
import json
import os
import boto3
from datetime import datetime, timezone
import decimal

from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
import cloudscraper

# --- Configuration (Pulled from Environment Variables) ---
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 'MM_OIS')
AWS_TARGET_REGION = os.environ.get('AWS_REGION', 'eu-north-1')

# The list of metricIds, corresponding to the series order from the API.
# It's better to configure this in the ECS Task Definition.
METRIC_IDS_STR = os.environ.get(
    'METRIC_IDS', 
    'MacroMicro_OIS_1M_Rate,MacroMicro_OIS_3M_Rate,MacroMicro_OIS_6M_Rate,MacroMicro_OIS_1Y_Rate,MacroMicro_OIS_2Y_Rate,MacroMicro_OIS_10Y_Rate,MacroMicro_OIS_30Y_Rate'
)
METRIC_IDS = METRIC_IDS_STR.split(',')

# Initialize Boto3 DynamoDB resource
dynamodb = boto3.resource('dynamodb', region_name=AWS_TARGET_REGION)
table = dynamodb.Table(DYNAMODB_TABLE_NAME)

async def harvest_credentials():
    """
    Launches a headless browser with Playwright to navigate to the chart page,
    waits for the necessary credentials (bearer token, cookies) to be available,
    and returns them. Returns (None, None) on failure.
    """
    print("--- Stage 1: Harvesting Credentials with Playwright ---")
    
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    )
    page = await context.new_page()
    await stealth_async(page)
    
    bearer_token = None
    cookies = []

    try:
        chart_page_url = "https://en.macromicro.me/charts/115044/us-overnight-indexed-swaps"
        print(f"  Navigating to: {chart_page_url}")
        await page.goto(chart_page_url, wait_until="networkidle", timeout=60000)

        print("  Waiting for application state (App.stk) to become available...")
        await page.wait_for_function("() => typeof window.App !== 'undefined' && typeof window.App.stk === 'string'", timeout=30000)
        
        bearer_token = await page.evaluate("() => window.App.stk")
        cookies = await context.cookies()

        if not bearer_token or not cookies:
            raise Exception("Failed to harvest a bearer token or cookies.")

        print("  ✓ Credentials Harvested Successfully!")
        print(f"    Bearer Token: {bearer_token[:20]}...")
        return bearer_token, cookies

    except Exception as e:
        print(f"  ERROR during credential harvesting: {e}")
        return None, None
    finally:
        print("  Closing Playwright browser.")
        await browser.close()
        await playwright.stop()

def fetch_data_with_credentials(bearer_token, cookies):
    """

    Makes a direct HTTP GET request to the data API endpoint using the harvested
    credentials and CloudScraper. Returns the parsed JSON data, or None on failure.
    """
    print("\n--- Stage 2: Making Direct API Call with CloudScraper ---")
    
    chart_page_url = "https://en.macromicro.me/charts/115044/us-overnight-indexed-swaps"
    data_api_url = "https://en.macromicro.me/charts/data/115044"
    
    cookie_string = "; ".join([f"{cookie['name']}={cookie['value']}" for cookie in cookies])
    headers = {
        'Accept': '*/*',
        'Authorization': f'Bearer {bearer_token}',
        'Cookie': cookie_string,
        'Referer': chart_page_url,
        'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    }
    
    scraper = cloudscraper.create_scraper()
    
    try:
        print(f"  Sending GET request to: {data_api_url}")
        response = scraper.get(data_api_url, headers=headers)
        response.raise_for_status()

        print(f"  ✓ Success! Server responded with Status Code: {response.status_code}")
        return response.json()

    except Exception as e:
        print(f"\n--- AN ERROR OCCURRED during the API call ---")
        print(f"  {type(e).__name__}: {e}")
        if 'response' in locals():
            print("  Server response text:", response.text[:500])
        return None

def process_and_store_data(api_data):
    """
    Parses the JSON data from the API, formats it, and stores it in DynamoDB.
    """
    print("\n--- Stage 3: Processing and Storing Data in DynamoDB ---")
    
    try:
        list_of_series_data = api_data['data']['c:115044']['series']
    except (KeyError, TypeError):
        print("  ERROR: The 'series' data was not found in the expected JSON path.")
        print("  Response JSON (partial):", json.dumps(api_data, indent=2)[:1000])
        return

    if len(list_of_series_data) != len(METRIC_IDS):
        print(f"  WARNING: Mismatch! Received {len(list_of_series_data)} data series, but have {len(METRIC_IDS)} metric IDs configured.")

    total_items_written = 0
    # Use batch_writer for efficient writes to DynamoDB
    with table.batch_writer() as batch:
        for i, series_raw_data in enumerate(list_of_series_data):
            if i >= len(METRIC_IDS):
                print(f"  Skipping extra data series #{i+1} as there is no corresponding metricId.")
                break

            current_metric_id = METRIC_IDS[i]
            print(f"  Processing metric: {current_metric_id}")
            
            # Decide whether to fetch all data or just recent data
            # For a daily full refresh, processing all points is robust.
            # To only add the latest, you'd need to check the last stored timestamp.
            # Let's process all points for simplicity and robustness.
            points_processed_for_metric = 0
            for log_entry in series_raw_data:
                try:
                    # log_entry is a list: ["YYYY-MM-DD", value]
                    date_str = log_entry[0]
                    value = log_entry[1]

                    # Skip entries where value is null
                    if value is None:
                        continue

                    # Convert date string to a datetime object at midnight UTC
                    dt_object = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    # Convert to UTC millisecond epoch timestamp for DynamoDB sort key
                    timestamp_ms = int(dt_object.timestamp() * 1000)

                    # Use Decimal for floating point numbers to maintain precision in DynamoDB
                    value_decimal = decimal.Decimal(str(value))

                    # Prepare the item for DynamoDB
                    item_to_store = {
                        'metricId': current_metric_id,
                        'timestamp': timestamp_ms,
                        'value': value_decimal
                    }
                    
                    # Add the item to the batch
                    batch.put_item(Item=item_to_store)
                    points_processed_for_metric += 1

                except (ValueError, TypeError, IndexError) as e:
                    print(f"    - Skipping malformed data point: {log_entry}. Error: {e}")
            
            print(f"    Queued {points_processed_for_metric} data points for storage.")
            total_items_written += points_processed_for_metric

    print(f"\n  ✓ DynamoDB batch writing complete. Total items written: {total_items_written}")

async def main():
    """Main execution flow."""
    print("Starting MacroMicro OIS Hijacker Script...")

    # Stage 1
    bearer_token, cookies = await harvest_credentials()
    if not bearer_token:
        print("\nScript cannot continue without credentials. Exiting.")
        return

    # Stage 2
    api_data = fetch_data_with_credentials(bearer_token, cookies)
    if not api_data:
        print("\nScript cannot continue without API data. Exiting.")
        return

    # Stage 3
    process_and_store_data(api_data)

    print("\nScript finished successfully.")

if __name__ == "__main__":
    asyncio.run(main())
