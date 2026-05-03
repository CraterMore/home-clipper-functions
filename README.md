# Home Clipper Functions

This repository contains the backend logic for **Home Clipper**, powered by Firebase Cloud Functions. It provides AI-driven property data extraction and automated user profile management.

## 🚀 Overview

Home Clipper Functions serves as the processing engine for capturing and structuring real estate listings. It leverages modern crawling tools and Large Language Models (LLMs) to transform messy web data into clean, actionable JSON.

### Key Features
- **AI Extraction**: Uses Google Gemini (via `google-genai`) to parse property listing details.
- **Web Crawling**: Integrates with `crawl4ai` to handle complex web page structures.
- **Auth Integration**: Securely verifies Firebase Auth tokens for user-specific operations.
- **Automated Workflows**: Firestore triggers for seamless user onboarding.

## 🛠️ Components

### 1. `extract_property_info` (HTTPS POST)
The primary endpoint for extracting listing data.
- **Inputs**: Listing URL (query param) and raw HTML (request body).
- **Process**:
    1. Validates the user's Firebase Auth token.
    2. Cleans HTML using `crawl4ai`.
    3. Prompts Gemini to extract specific fields (Address, Price, Bed/Bath, Utilities, Policies, etc.).
- **Returns**: A structured JSON object of the property listing.

### 2. `user_created` (Firestore Trigger)
Triggered when a new document is created in the `users/{userId}` collection.
- **Action**: Populates the user document with metadata like `createdAt`, `email`, `displayName`, and default permissions (`isAdmin: false`).

## ⚙️ Setup & Deployment

### Prerequisites
- [Firebase CLI](https://firebase.google.com/docs/cli) installed (`npm install -g firebase-tools`).
- A Firebase project with Firestore and Functions enabled.
- Google Cloud Project with the Gemini API enabled.

### Local Development
To run the functions locally using the Firebase Emulator Suite:
```bash
# Initialize emulators (if not already done)
firebase init emulators

# Start the emulators
firebase emulators:start
```

### Configuration
Ensure you have the necessary environment variables configured in your Firebase environment or local `.env` files if required by the SDK.

### Deployment
Deploy the functions to your production Firebase environment:
```bash
# Login to Firebase
firebase login

# Select your project
firebase use home-clipper

# Deploy only functions
firebase deploy --only functions
```

## 📦 Dependencies
The project relies on several key libraries defined in `functions/requirements.txt`:
- `firebase-functions`: Core SDK for Cloud Functions.
- `firebase-admin`: Firebase SDK for Firestore and Auth management.
- `google-genai`: Client for interacting with Google Gemini models.
- `crawl4ai`: Advanced web crawling and HTML-to-Markdown conversion.

---
*Part of the Home Clipper ecosystem.*
