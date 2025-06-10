# mmOIShijacker.py (Revised "Back-to-Basics" Version)

import asyncio
import json
import os
import boto3
from datetime import datetime, timezone
import decimal

# Use cloudscraper that has been patched by playwright_stealth
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
import cloudscraper

# --- Configuration (Pulled from Environment Variables) ---
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 'MM_OIS')
AWS_TARGET_REGION = os.environ.get('AWS_REGION', 'eu-north-1')
METRIC_IDS_STR = os.environ.get(
    'METRIC_IDS', 
    'MacroMicro_OIS_1M_Rate,MacroMicro_OIS_3M_Rate,MacroMicro_OIS_6M_Rate,MacroMicro_OIS_1Y_Rate,MacroMicro_OIS_2Y_Rate,MacroMicro_OIS_10Y_Rate,MacroMicro_OIS_30Y_Rate'
)
METRIC_IDS = METRIC_IDS_STR.split(',')

# Initialize Boto3 DynamoDB resource
dynamodb = boto3.resource('dynamodb', region_name=AWS_TARGET_REGION)
table = dynamodb.Table(DYNAMODB_TABLE_NAME)

def process_and_store_data(api_data):
    """Parses the JSON data from the API, takes the last 30 data points for each series, formats them, and stores them in DynamoDB."""
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
    with table.batch_writer() as batch:
        for i, series_raw_data in enumerate(list_of_series_data):
            if i >= len(METRIC_IDS):
                break

            current_metric_id = METRIC_IDS[i]
            print(f"  Processing metric: {current_metric_id}")

            last_500_points = series_raw_data[-500:]
            print(f"    - Found {len(series_raw_data)} total points, processing the last {len(last_500_points)}.")
            
            points_processed_for_metric = 0
            for log_entry in last_500_points:
                try:
                    date_str, value = log_entry[0], log_entry[1]
                    if value is None:
                        continue

                    dt_object = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    timestamp_ms = int(dt_object.timestamp() * 1000)
                    value_decimal = decimal.Decimal(str(value))

                    item_to_store = {
                        'metricId': current_metric_id,
                        'timestamp': timestamp_ms,
                        'value': value_decimal
                    }
                    
                    batch.put_item(Item=item_to_store)
                    points_processed_for_metric += 1
                except (ValueError, TypeError, IndexError) as e:
                    print(f"    - Skipping malformed data point: {log_entry}. Error: {e}")
            
            print(f"    Queued {points_processed_for_metric} data points for storage.")
            total_items_written += points_processed_for_metric
    print(f"\n  ✓ DynamoDB batch writing complete. Total items written: {total_items_written}")


async def main():
    """Main execution flow, sticking closer to the original logic."""
    print("Starting MacroMicro OIS Hijacker Script (Revised)...")

    chart_page_url = "https://en.macromicro.me/charts/115044/us-overnight-indexed-swaps"
    data_api_url = "https://en.macromicro.me/charts/data/115044"
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"

    playwright = None # Define here to ensure it's available in finally block
    try:
        print("--- Stage 1: Initializing Playwright and Harvesting Credentials ---")
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=user_agent)
        page = await context.new_page()
        await stealth_async(page)
        
        print("  Navigating to the chart page...")
        await page.goto(chart_page_url, wait_until="domcontentloaded", timeout=60000)

        print("  Waiting for application state (App.stk)...")
        await page.wait_for_function("() => typeof window.App !== 'undefined' && typeof window.App.stk === 'string'", timeout=30000)
        
        bearer_token = await page.evaluate("() => window.App.stk")
        cookies = await context.cookies()
        cookie_string = "; ".join([f"{cookie['name']}={cookie['value']}" for cookie in cookies])

        if not bearer_token or not cookies:
            raise Exception("Failed to harvest a bearer token or cookies.")

        print("  ✓ Credentials Harvested Successfully!")

        # --- PART 2: Make a direct API call ---
        print("\n--- Stage 2: Making Direct API Call with CloudScraper ---")
        
        headers = {
            'Accept': '*/*',
            'Authorization': f'Bearer {bearer_token}',
            'Cookie': cookie_string,
            'Referer': chart_page_url,
            'User-Agent': user_agent
        }
        
        # KEY CHANGE: Initialize cloudscraper right after getting credentials,
        # mimicking a tighter "session" link.
        scraper = cloudscraper.create_scraper()
        
        print(f"  Sending GET request to: {data_api_url}")
        response = scraper.get(data_api_url, headers=headers)
        response.raise_for_status()

        print(f"  ✓ API Call Success! Server responded with Status Code: {response.status_code}")
        
        api_data = response.json()
        
        # --- PART 3: Process and Store Data ---
        process_and_store_data(api_data)

        print("\nScript finished successfully.")

    except Exception as e:
        print(f"\n--- AN UNHANDLED ERROR OCCURRED ---")
        print(f"  {type(e).__name__}: {e}")
        if 'response' in locals():
            print("  Server response text:", response.text[:500])

    finally:
        # Graceful cleanup of Playwright
        if playwright:
            print("  Closing Playwright.")
            await playwright.stop()

if __name__ == "__main__":
    asyncio.run(main())
