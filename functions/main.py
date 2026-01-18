from firebase_functions.options import set_global_options
# The Cloud Functions for Firebase SDK to create Cloud Functions and set up triggers.
from firebase_functions import firestore_fn, https_fn, options
# The Firebase Admin SDK to access Cloud Firestore.
from firebase_admin import initialize_app, firestore, auth
import json
from google import genai
from google.genai import types
import asyncio
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import CrawlerRunConfig, CacheMode

set_global_options(max_instances=10)

initialize_app()

async def crawl_raw_html(html_content):
    raw_html_url = f"raw:{html_content}"
    config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS)

    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=raw_html_url, config=config)
        if result.success:
            return result.markdown
        else:
            raise Exception("Failed to crawl raw HTML: " + result.error_message)

@https_fn.on_request(memory=512, timeout_sec=30, cors=options.CorsOptions(cors_origins="*", cors_methods=["post"]))
def extract_property_info(req: https_fn.Request) -> https_fn.Response:
    """
    Receives an HTML file, url, and userId.
    Parses HTML using unstructured, extracts property info using Gemini.
    """
    auth_header = req.headers.get("Authorization")
    if not auth_header:
        return https_fn.Response("Missing 'Authorization' header.", status=401)

    token = auth_header.split("Bearer ")[1]
    try:
        user_data = auth.verify_id_token(token, check_revoked=True)
    except Exception as e:
        return https_fn.Response("Invalid 'Authorization' header.", status=403)

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
        print(str(e))
        # Build a safe fallback if unstructured fails (though it shouldn't for simple HTML)
        clean_text = html_content[:50000] # truncate if raw

    # 4. Call Gemini
    try:
        
        prompt = f"""
        You are an expert data extractor. given the text from a property listing website below, extract the following fields:
        - Address (output as a string)
        - Price (output as a number)
        - Number of bedrooms (output as a number)
        - Number of bathrooms (output as a number)

        Source URL: {url}

        Return the data in the following JSON format ONLY:
        {{
            "sourceURL": "{url}",
            "address": "27 Main St, Anytown, AB 12345",
            "price": 100000,
            "bedrooms": 2,
            "bathrooms": 1
        }}

        If a field is missing, use null for strings or 0 for numbers. If the content does not appear to be a property listing, return null for all fields.

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