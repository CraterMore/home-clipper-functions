from firebase_functions.options import set_global_options
# The Cloud Functions for Firebase SDK to create Cloud Functions and set up triggers.
from firebase_functions import firestore_fn, https_fn, options
# The Firebase Admin SDK to access Cloud Firestore.
from firebase_admin import initialize_app, firestore, auth
import json
import os
from google import genai
from google.genai import types
import asyncio
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import CrawlerRunConfig, CacheMode, BrowserConfig
from playwright.async_api import async_playwright

set_global_options(max_instances=10)

initialize_app()

async def crawl_raw_html(html_content):
    raw_html_url = f"raw:{html_content}"
    browserless_api_key = os.getenv("BROWSERLESS_API_KEY")
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"wss://production-sfo.browserless.io?token={browserless_api_key}")

        config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, excluded_tags=['form', 'footer', 'nav'], excluded_selector='.subMarketSection, .mapSection, .nearbySection, .schoolsSection, .profileV2TransportationSection, .walkScoreSection, .profileV2NearbyAmenitiesSection, .profileFooterWrapper')

        try:
            async with AsyncWebCrawler(browser=browser) as crawler:
                result = await crawler.arun(url=raw_html_url, config=config)
                if result.success:
                    return result.markdown
                else:
                    raise Exception("Failed to crawl raw HTML: " + result.error_message)
        finally:
            browser.close()

@https_fn.on_request(memory=512, timeout_sec=30, cors=options.CorsOptions(cors_origins="*", cors_methods=["post"]))
def extract_property_info(req: https_fn.Request) -> https_fn.Response:
    """
    Receives an HTML file, url, and userId.
    Parses HTML using unstructured, extracts property info using Gemini.
    """
    # auth_header = req.headers.get("Authorization")
    # if not auth_header:
    #     return https_fn.Response("Missing 'Authorization' header.", status=401)

    # token = auth_header.split("Bearer ")[1]
    # try:
    #     user_data = auth.verify_id_token(token, check_revoked=True)
    # except Exception as e:
    #     return https_fn.Response("Invalid 'Authorization' header.", status=403)

    # Initialize Gemini client
    client = genai.Client()

    # 1. Parse parameters (URL)
    url = req.args.get("url")

    if not url:
        return https_fn.Response("Missing 'url' parameter.", status=400)

    # 2. Get HTML content
    html_content = ""
    try:
        # Check for file upload
        # if req.files and 'file' in req.files:
        #     file_storage = req.files['file']
        #     html_content = file_storage.read().decode('utf-8', errors='ignore')
        # # Check for file in a different key if 'file' missing but files exist
        # elif req.files:
        #     # Just take the first file
        #     for key in req.files:
        #         html_content = req.files[key].read().decode('utf-8', errors='ignore')
        #         break
        # # Fallback to raw body if content type suggests text/html or generic
        # elif req.data:
        html_content = req.data.decode('utf-8', errors='ignore')
        
    except Exception as e:
        print(f"Error reading HTML content: {e}")
        return https_fn.Response(f"Error reading content: {str(e)}", status=500)

    if not html_content:
        return https_fn.Response("No HTML content received.", status=400)

    # 3. Partition HTML using unstructured
    try:
        clean_text = asyncio.run(crawl_raw_html(html_content))
    except Exception as e:
        return https_fn.Response(f"Error crawling HTML: {str(e)}", status=500)
        print(str(e))
        # Build a safe fallback if unstructured fails (though it shouldn't for simple HTML)
        clean_text = html_content[:50000] # truncate if raw

    # 4. Call Gemini
    try:
        
        prompt = f"""
        You are an expert data extractor. given the text from a property listing website below, extract the following fields:
        - Address (string): Full address or street name or town name
        - Price (number)
        - Lease length (number | null): Length in months
        - Type (string): String must be one of "Apartment", "Condo", "Townhouse", or "House"
        - Bedrooms (number)
        - Bathrooms (number | null)
        - Square Footage (number | null)
        - Available Date (string): Date string in format "YYYY-MM-DD" or "Available Now" or "Available Soon"
        - Description (string)
        - Utilities Included (List[string]): Strings in list may only include "Water", "Sewer", "Heat", "Hot Water", "Trash", "Electricity", "Internet/Cable", "Air Conditioning"
        - Dog Policy (string | null): String must be one of "Allowed", "Allowed with restrictions", or "Not allowed"
        - Cat Policy (string | null): String must be one of "Allowed", "Allowed with restrictions", or "Not allowed"
        - Pet fee (number | null): Leave null if no pets are allowed or not specified
        - Pet fee frequency (string | null): String must be one of "One time", "Monthly", or "Yearly"
        - Parking Availability (string | null): String must be one of "Garage", "Parking Lot", "Street Parking", "No Parking", or "Other"
        - Parking fee (number | null): Leave null if no parking is available or not specified
        - Parking fee frequency (string | null): String must be one of "One time", "Monthly", or "Yearly"
        - Laundry (string | null): String must be one of "In unit", "In building", or "Not on-site/Other"

        Source URL: {url}

        Return the data in the following JSON format ONLY:
        {{
            "sourceURL": "{url}",
            "address": "27 Main St, Anytown, AB 12345",
            "price": 2500,
            "leaseLength": 12,
            "type": "Apartment",
            "bedrooms": 2,
            "bathrooms": 2,
            "squareFootage": 1000,
            "availableDate": "Available Now",
            "description": "A beautiful apartment...",
            "utilitiesIncluded": ["Water", "Heat"],
            "dogPolicy": "Allowed",
            "catPolicy": "Not Allowed",
            "petFee": 60,
            "petFeeFrequency": "Monthly",
            "parkingAvailability": "Garage",
            "parkingFee": 50,
            "parkingFeeFrequency": "Monthly",
            "laundry": "In unit"
        }}

        The extracted values must match the allowed values for each field as specified above.
        If a field is missing, use null (or empty list for list types). If the content does not appear to be a property listing, return null for address.

        Listing Content:
        {clean_text}
        """

        response = client.models.generate_content(model='gemini-2.5-flash-lite', contents=prompt, config=types.GenerateContentConfig(response_mime_type="application/json"))
        
        try:
            result_json = json.loads(response.text)
        except json.JSONDecodeError:
            # Fallback if model didn't output strict JSON (though mime_type helps)
            text_resp = response.text.strip()
            if text_resp.startswith("```json"):
                text_resp = text_resp[7:-3]
            try:
                result_json = json.loads(text_resp)
            except:
                result_json = {"error": "Failed to parse JSON from AI response", "raw": response.text}
        
        return https_fn.Response(json.dumps(result_json), mimetype='application/json', status=200)                

    except Exception as e:
        print(f"Error calling Gemini: {e}")
        return https_fn.Response(f"AI processing error: {str(e)}", status=500)


@firestore_fn.on_document_created(document="users/{userId}")
def user_created(event: firestore_fn.DocumentSnapshot) -> None:
    """
    Add additional fields to a newly created user document
    """
    try:
        
        user_data = {
            "email": event.data.get("email"),
            "displayName": event.data.get("displayName"),
            "createdAt": firestore.SERVER_TIMESTAMP,
            "isAdmin": False
        }
        
        event.data.reference.set(user_data)

    except Exception as e:
        print(f"Error creating user document: {e}")