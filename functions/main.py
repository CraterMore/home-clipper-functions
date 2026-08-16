from firebase_functions.options import set_global_options
# The Cloud Functions for Firebase SDK to create Cloud Functions and set up triggers.
from firebase_functions import firestore_fn, https_fn, options
# The Firebase Admin SDK to access Cloud Firestore.
from firebase_admin import initialize_app, firestore, auth
import json
import os
import pathlib
import asyncio
from typing import List, Optional
from pydantic import BaseModel, Field
from crawl4ai.docker_client import Crawl4aiDockerClient
from crawl4ai.async_configs import CrawlerRunConfig, CacheMode, LLMConfig
from crawl4ai.extraction_strategy import LLMExtractionStrategy

set_global_options(max_instances=10)

initialize_app()

# --- GLOBAL SCOPE: Runs once per instance ---
# Fake browser is used to bypass playwright browser check.
# Cloud Functions for Firebase uses a sandbox environment that doesn't support browser automation.
# This fix involves creating a dummy browser executable in the expected location, tricking Playwright into thinking a browser is present.
def setup_fake_browser():
    custom_pw_path = os.getenv('PLAYWRIGHT_BROWSERS_PATH')
    
    # Target path for the "fake" chrome
    fake_dir = pathlib.Path(custom_pw_path) / "chromium-1208" / "chrome-linux64"
    fake_exe = fake_dir / "chrome"
    
    # Only create if it's not already there from a previous warm start
    if not fake_exe.exists():
        fake_dir.mkdir(parents=True, exist_ok=True)
        with open(fake_exe, "w") as f:
            f.write("#!/bin/sh\nexit 0")
        os.chmod(fake_exe, 0o755)

# Trigger the setup immediately when the function container starts
setup_fake_browser()

class PropertyInfo(BaseModel):
    sourceURL: Optional[str] = Field(default=None, description="Source URL of the listing")
    address: Optional[str] = Field(default=None, description="Full address or street name or town name")
    price: Optional[float] = Field(default=None, description="Price in dollars")
    leaseLength: Optional[int] = Field(default=None, description="Length in months")
    type: Optional[str] = Field(default=None, description="Must be one of 'Apartment', 'Condo', 'Townhouse', or 'House'")
    bedrooms: Optional[float] = Field(default=None, description="Number of bedrooms")
    bathrooms: Optional[float] = Field(default=None, description="Number of bathrooms")
    squareFootage: Optional[int] = Field(default=None, description="Square footage")
    availableDate: Optional[str] = Field(default=None, description="Date string in format 'YYYY-MM-DD' or 'Available Now' or 'Available Soon'")
    description: Optional[str] = Field(default=None, description="Property description")
    utilitiesIncluded: List[str] = Field(default_factory=list, description="Strings in list may only include 'Water', 'Sewer', 'Heat', 'Hot Water', 'Trash', 'Electricity', 'Internet/Cable', 'Air Conditioning'")
    dogPolicy: Optional[str] = Field(default=None, description="Must be one of 'Allowed', 'Allowed with restrictions', or 'Not allowed'")
    catPolicy: Optional[str] = Field(default=None, description="Must be one of 'Allowed', 'Allowed with restrictions', or 'Not allowed'")
    petFee: Optional[float] = Field(default=None, description="Pet fee amount")
    petFeeFrequency: Optional[str] = Field(default=None, description="Must be one of 'One time', 'Monthly', or 'Yearly'")
    parkingAvailability: Optional[str] = Field(default=None, description="Must be one of 'Garage', 'Parking Lot', 'Street Parking', 'No Parking', or 'Other'")
    parkingFee: Optional[float] = Field(default=None, description="Parking fee amount. If parking is included in rent, set this to 0")
    parkingFeeFrequency: Optional[str] = Field(default=None, description="Must be one of 'One time', 'Monthly', or 'Yearly'")
    laundry: Optional[str] = Field(default=None, description="Must be one of 'In unit', 'In building', or 'Not on-site/Other'")

async def crawl_and_extract_property_info(html_content: str, url: str) -> str:
    raw_html_url = f"raw:{html_content}"
    
    llm_strategy = LLMExtractionStrategy(
        llm_config=LLMConfig(
            provider="gemini/gemini-2.5-flash-lite",
            api_token=os.getenv("GEMINI_API_KEY")
        ),
        schema=PropertyInfo.model_json_schema(),
        extraction_type="schema",
        instruction=f"""
        You are an expert data extractor. Given the property listing website content, extract the property fields into the specified JSON schema.
        
        The extracted values must match the allowed values for each field.
        If a field is missing, use null (or empty list for list types). If the content does not appear to be a property listing, return null for address.
        Source URL: {url}
        """,
        input_format="html"
    )

    config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        excluded_tags=['form', 'footer', 'nav'],
        excluded_selector='.subMarketSection, .mapSection, .nearbySection, .schoolsSection, .profileV2TransportationSection, .walkScoreSection, .profileV2NearbyAmenitiesSection, .profileFooterWrapper',
        extraction_strategy=llm_strategy
    )

    async with Crawl4aiDockerClient(base_url="https://crawl4ai-1027404764786.us-east4.run.app", verbose=True) as client:
        result = await client.crawl([raw_html_url], crawler_config=config)
        if result.success:
            return result.extracted_content
        else:
            raise Exception("Failed to extract property info via Crawl4AI: " + (result.error_message or "Unknown error"))

@https_fn.on_request(memory=512, timeout_sec=30, cors=options.CorsOptions(cors_origins="*", cors_methods=["post"]))
def extract_property_info(req: https_fn.Request) -> https_fn.Response:
    """
    Receives an HTML payload, url, and userId.
    Extracts property info using Crawl4AI LLMExtractionStrategy.
    """
    auth_header = req.headers.get("Authorization")
    if not auth_header:
        return https_fn.Response("Missing 'Authorization' header.", status=401)

    token = auth_header.split("Bearer ")[1]
    try:
        user_data = auth.verify_id_token(token, check_revoked=True)
    except Exception as e:
        return https_fn.Response("Invalid 'Authorization' header.", status=403)

    # 1. Parse parameters (URL)
    url = req.args.get("url")

    if not url:
        return https_fn.Response("Missing 'url' parameter.", status=400)

    # 2. Get HTML content
    html_content = ""
    try:
        html_content = req.data.decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"Error reading HTML content: {e}")
        return https_fn.Response(f"Error reading content: {str(e)}", status=500)

    if not html_content:
        return https_fn.Response("No HTML content received.", status=400)

    # 3. Perform LLM extraction using Crawl4aiDockerClient
    try:
        extracted_raw = asyncio.run(crawl_and_extract_property_info(html_content, url))
        
        try:
            result_json = json.loads(extracted_raw)
            if isinstance(result_json, list) and len(result_json) > 0:
                result_json = result_json[0]
            if isinstance(result_json, dict):
                result_json["sourceURL"] = url
        except json.JSONDecodeError:
            text_resp = extracted_raw.strip() if extracted_raw else ""
            if text_resp.startswith("```json"):
                text_resp = text_resp[7:-3]
            try:
                result_json = json.loads(text_resp)
                if isinstance(result_json, dict):
                    result_json["sourceURL"] = url
            except:
                result_json = {"error": "Failed to parse JSON from AI response", "raw": extracted_raw}

        return https_fn.Response(json.dumps(result_json), mimetype='application/json', status=200)

    except Exception as e:
        print(f"Error extracting property info: {e}")
        return https_fn.Response(f"AI extraction error: {str(e)}", status=500)


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