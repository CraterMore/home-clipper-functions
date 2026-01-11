from firebase_functions.options import set_global_options
# The Cloud Functions for Firebase SDK to create Cloud Functions and set up triggers.
from firebase_functions import firestore_fn, https_fn
# The Firebase Admin SDK to access Cloud Firestore.
from firebase_admin import initialize_app, firestore
import json
from google import genai
from google.genai import types
from unstructured.partition.html import partition_html

# For cost control, you can set the maximum number of containers that can be
# running at the same time. This helps mitigate the impact of unexpected
# traffic spikes by instead downgrading performance. This limit is a per-function
# limit. You can override the limit for each function using the max_instances
# parameter in the decorator, e.g. @https_fn.on_request(max_instances=5).
set_global_options(max_instances=10)

initialize_app()

@https_fn.on_request(memory=512, timeout_sec=60)
def extract_property_info(req: https_fn.Request) -> https_fn.Response:
    """
    Receives an HTML file, url, and userId.
    Parses HTML using unstructured, extracts property info using Gemini.
    """
    client = genai.Client()

    # 1. Parse parameters (URL, userId)
    url = req.args.get("url")
    user_id = req.args.get("userId")

    if not url or not user_id:
        return https_fn.Response("Missing 'url' or 'userId' parameter.", status=400)

    # 2. Get HTML content
    html_content = ""
    try:
        # Check for file upload
        if req.files and 'file' in req.files:
            file_storage = req.files['file']
            html_content = file_storage.read().decode('utf-8', errors='ignore')
        # Check for file in a different key if 'file' missing but files exist
        elif req.files:
            # Just take the first file
            for key in req.files:
                html_content = req.files[key].read().decode('utf-8', errors='ignore')
                break
        # Fallback to raw body if content type suggests text/html or generic
        elif req.data:
            html_content = req.data.decode('utf-8', errors='ignore')
        
    except Exception as e:
        print(f"Error reading HTML content: {e}")
        return https_fn.Response(f"Error reading content: {str(e)}", status=500)

    if not html_content:
        return https_fn.Response("No HTML content received.", status=400)

    # 3. Partition HTML using unstructured
    try:
        # partition_html can take text directly
        elements = partition_html(text=html_content)
        # Combine elements into a single string for the context
        clean_text = "\n\n".join([str(el) for el in elements])
    except Exception as e:
        print(f"Error partitioning HTML: {e}")
        # Build a safe fallback if unstructured fails (though it shouldn't for simple HTML)
        clean_text = html_content[:50000] # truncate if raw

    # 4. Call Gemini
    try:
        
        prompt = f"""
        You are an expert data extractor. given the text from a property listing website below, extract the following fields:
        - Price (output as a number)
        - Number of bedrooms (output as a number)
        - Number of bathrooms (output as a number)
        - Address (output as a string)
        
        Source URL: {url}

        Return the data in the following JSON format ONLY:
        {{
            "sourceURL": "{url}",
            "address": "...",
            "price": 100000,
            "bedrooms": 2,
            "bathrooms": 1
        }}

        If a field is missing, use null or 0.

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

        # 5. Store in Firestore
        if "error" not in result_json:
            try:
                db = firestore.client()
                db.collection(f"users/{user_id}/properties").add(result_json)
            except Exception as fe:
                print(f"Error saving to Firestore: {fe}")

        # 6. Return Usage
        usage = response.usage_metadata
        
        return https_fn.Response(json.dumps({
            "total_tokens": usage.total_token_count,
            "input_tokens": usage.prompt_token_count,
            "output_tokens": usage.candidates_token_count
        }), mimetype='application/json')

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
        print(f"Created user document for {event.data.get('email')}")

    except Exception as e:
        print(f"Error creating user document: {e}")