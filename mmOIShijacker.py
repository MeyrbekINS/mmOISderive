import asyncio
import json
import cloudscraper # Make sure you have this installed: pip install cloudscraper
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

# --- Simplified Main Execution ---

async def main():
    chart_page_url = "https://en.macromicro.me/charts/115044/us-overnight-indexed-swaps"
    data_api_url = "https://en.macromicro.me/charts/data/115044" # This is our real target
    chart_id_numeric = "115044"
    terms = ["1 Month", "3 Months", "6 Months", "1 Year", "2 Years", "10 Years", "30 Years"]
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"

    # --- PART 1: Use Playwright to harvest session credentials ---
    print("--- Stage 1: Harvesting Credentials with Playwright ---")
    
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True) # Keep it visible for debugging
    context = await browser.new_context(user_agent=user_agent)
    page = await context.new_page()
    await stealth_async(page)
    
    bearer_token = None
    cookies = []

    try:
        print("  Navigating to the chart page...")
        await page.goto(chart_page_url, wait_until="domcontentloaded", timeout=60000)

        print("  Waiting for the application state (App.stk) to become available...")
        await page.wait_for_function("() => typeof window.App !== 'undefined' && typeof window.App.stk === 'string'", timeout=20000)
        
        # Now that we know it exists, extract the credentials
        bearer_token = await page.evaluate("() => window.App.stk")
        cookies = await context.cookies()

        if not bearer_token or not cookies:
            raise Exception("Failed to harvest a bearer token or cookies.")

        print("  ✓ Credentials Harvested Successfully!")
        print(f"    Bearer Token: {bearer_token[:20]}...")

    except Exception as e:
        print(f"  ERROR during credential harvesting: {e}")
        await browser.close()
        await playwright.stop()
        return # Exit if we fail this stage
    finally:
        # We are done with the browser
        print("  Closing browser, proceeding to direct HTTP request.")
        await browser.close()
        await playwright.stop()


    # --- PART 2: Make a direct API call using the harvested credentials ---
    print("\n--- Stage 2: Making Direct API Call with CloudScraper ---")
    
    if not bearer_token:
        print("  Cannot proceed, bearer token was not acquired.")
        return

    # Format the cookies from Playwright's list format into a single string for the header
    cookie_string = "; ".join([f"{cookie['name']}={cookie['value']}" for cookie in cookies])

    # Build the headers exactly as a real browser would, using our harvested credentials
    headers = {
        'Accept': '*/*',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Accept-Language': 'en-US,en;q=0.9',
        'Authorization': f'Bearer {bearer_token}',
        'Cookie': cookie_string,
        'Referer': chart_page_url,
        'User-Agent': user_agent
    }
    
    scraper = cloudscraper.create_scraper()
    
    try:
        print(f"  Sending GET request to: {data_api_url}")
        response = scraper.get(data_api_url, headers=headers)
        response.raise_for_status() # This will raise an error for 4xx or 5xx responses

        print(f"  ✓ Success! Server responded with Status Code: {response.status_code}")
        
        parsed_json = response.json()
        
        list_of_series_data = parsed_json.get('data', {}).get(f"c:{chart_id_numeric}", {}).get('series')
        
        if list_of_series_data:
            print("\n--- Final Results ---")
            for i, series_raw_data in enumerate(list_of_series_data):
                term_name = terms[i]
                print(f"\n--- {term_name} ---")
                
                last_30_logs = series_raw_data[-30:]
                if not last_30_logs:
                    print("  No data points found for this term.")
                else:
                    for log_idx, log_entry in enumerate(last_30_logs):
                        print(f"  {log_idx + 1}. Date: {log_entry[0]}, Value: {log_entry[1]}")
        else:
             print("  ERROR: The JSON was received, but the 'series' data was not found inside.")
             print("  Response JSON (partial):", json.dumps(parsed_json, indent=2)[:1000])


    except Exception as e:
        print(f"\n--- AN ERROR OCCURRED during the API call ---")
        print(f"{type(e).__name__}: {e}")
        if 'response' in locals():
            print("  Server response text:", response.text[:500])


if __name__ == "__main__":
    asyncio.run(main())
