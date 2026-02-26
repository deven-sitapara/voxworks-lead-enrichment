#!/usr/bin/env python3
"""
Lead Enrichment Agent - Parallel Processing Version
Processes leads from Excel, enriches via Groq compound (web search), and saves results.
"""

import os
import json
import time
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from groq import Groq
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

load_dotenv()

# Configuration
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
INPUT_FILE = os.environ.get("INPUT_FILE", "input_leads.xlsx")
TODAY = datetime.now().strftime("%Y-%m-%d")
OUTPUT_FILE = f"output/enriched_leads_{TODAY}.xlsx"
CHECKPOINT_FILE = "output/enrichment_checkpoint.json"
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", 5))
CHECKPOINT_INTERVAL = 50  # Save checkpoint every N completed leads
RETRY_ATTEMPTS = 5  # More retries for rate limits
RETRY_DELAY = 10  # Longer delay between retries
REQUEST_DELAY = int(os.environ.get("REQUEST_DELAY", 2))
GROQ_MODEL = os.environ.get("GROQ_MODEL", "groq/compound-mini")

# Thread-safe lock for checkpoint
checkpoint_lock = threading.Lock()
progress_lock = threading.Lock()

# Progress tracking
progress = {
    'completed': 0,
    'success': 0,
    'errors': 0,
    'total': 0
}


def get_client():
    """Get a Groq client (thread-local)."""
    return Groq(api_key=GROQ_API_KEY)


def build_search_prompt(row):
    """Build a search prompt for a single lead."""
    name = row.get('Contact Name', '')
    company = row.get('Agency Name', '')
    mobile = row.get('Mobile', '')
    phone = row.get('Phone', '')
    email = row.get('Email Address', '')
    suburb = row.get('Suburb', '')
    state = row.get('State', '')

    suburb_str = str(suburb) if pd.notna(suburb) else ""
    state_str = str(state) if pd.notna(state) else ""
    location = f"{suburb_str} {state_str}".strip() or "Australia"

    prompt = f"""Search the web to verify and enrich information about this real estate agent:

Name: {name}
Company: {company}
Location: {location}
Current Mobile: {mobile if pd.notna(mobile) else 'Unknown'}
Current Phone: {phone if pd.notna(phone) else 'Unknown'}
Current Email: {email if pd.notna(email) else 'Unknown'}

Please search for this person and provide:
1. VERIFIED: Is this person currently working at {company}? (Yes/No/Unknown)
2. CURRENT_COMPANY: Their current company name (if different from above)
3. CURRENT_ROLE: Their current job title/position
4. VERIFIED_PHONE: Any phone number found for them
5. VERIFIED_EMAIL: Any email found for them
6. LINKEDIN_URL: Their LinkedIn profile URL if found
7. CONFIDENCE: How confident are you in this data? (High/Medium/Low)
8. NOTES: Any other relevant info (awards, specializations, years experience)

Respond in this exact JSON format:
{{
    "verified_at_company": "Yes/No/Unknown",
    "current_company": "company name or null",
    "current_role": "role or null",
    "verified_phone": "phone or null",
    "verified_email": "email or null",
    "linkedin_url": "url or null",
    "confidence": "High/Medium/Low",
    "notes": "additional info or null"
}}

Only return the JSON, no other text."""

    return prompt


def enrich_lead(args):
    """Enrich a single lead using Groq compound model. Thread-safe."""
    index, row = args
    name = row.get('Contact Name', f'Lead {index}')
    prompt = build_search_prompt(row)
    client = get_client()

    # Small delay to avoid rate limits
    time.sleep(REQUEST_DELAY)

    for attempt in range(RETRY_ATTEMPTS):
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1
            )

            content = response.choices[0].message.content.strip()

            # Try to parse JSON from response
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            data = json.loads(content)
            data['last_enriched'] = datetime.now().isoformat()
            data['enrichment_status'] = 'success'

            with progress_lock:
                progress['completed'] += 1
                progress['success'] += 1
                pct = (progress['completed'] / progress['total']) * 100
                print(f"[{progress['completed']}/{progress['total']} ({pct:.1f}%)] ✓ {name[:30]} - {data.get('confidence', 'N/A')}")

            return index, data

        except json.JSONDecodeError as e:
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
                continue

            with progress_lock:
                progress['completed'] += 1
                progress['errors'] += 1
                print(f"[{progress['completed']}/{progress['total']}] ✗ {name[:30]} - JSON parse error")

            return index, {
                'enrichment_status': 'parse_error',
                'enrichment_error': str(e),
                'raw_response': content[:500] if 'content' in dir() else None,
                'last_enriched': datetime.now().isoformat()
            }

        except Exception as e:
            if attempt < RETRY_ATTEMPTS - 1:
                # Rate limit or transient error - wait and retry
                if "rate" in str(e).lower() or "429" in str(e):
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                time.sleep(RETRY_DELAY)
                continue

            with progress_lock:
                progress['completed'] += 1
                progress['errors'] += 1
                print(f"[{progress['completed']}/{progress['total']}] ✗ {name[:30]} - {str(e)[:50]}")

            return index, {
                'enrichment_status': 'api_error',
                'enrichment_error': str(e),
                'last_enriched': datetime.now().isoformat()
            }


def load_checkpoint():
    """Load checkpoint if exists."""
    if Path(CHECKPOINT_FILE).exists():
        with open(CHECKPOINT_FILE, 'r') as f:
            return json.load(f)
    return {'processed_indices': [], 'enrichments': {}}


def save_checkpoint(checkpoint):
    """Save checkpoint (thread-safe)."""
    with checkpoint_lock:
        with open(CHECKPOINT_FILE, 'w') as f:
            json.dump(checkpoint, f)


def main():
    print(f"{'='*60}")
    print(f"LEAD ENRICHMENT AGENT - PARALLEL PROCESSING")
    print(f"{'='*60}")
    print(f"Loading {INPUT_FILE}...")

    df = pd.read_excel(INPUT_FILE)
    total_leads = len(df)
    print(f"Loaded {total_leads} leads")
    print(f"Workers: {MAX_WORKERS} parallel threads")

    # Load checkpoint
    checkpoint = load_checkpoint()
    processed_indices = set(checkpoint.get('processed_indices', []))
    enrichments = checkpoint.get('enrichments', {})

    # Find leads that still need processing (skip rows with no name)
    to_process = []
    skipped = 0
    for i in range(total_leads):
        if i in processed_indices:
            continue
        row = df.iloc[i]
        name = row.get('Contact Name')
        if pd.isna(name) or str(name).strip() == '' or str(name).lower() == 'nan':
            skipped += 1
            continue
        to_process.append((i, row))

    if skipped > 0:
        print(f"Skipped {skipped} leads with missing names")

    if processed_indices:
        print(f"Resuming: {len(processed_indices)} already done, {len(to_process)} remaining")

    if not to_process:
        print("All leads already processed!")
        return

    # Set up progress tracking
    progress['total'] = len(to_process)
    progress['completed'] = 0
    progress['success'] = 0
    progress['errors'] = 0

    print(f"\nStarting enrichment of {len(to_process)} leads...")
    print(f"{'='*60}\n")

    start_time = time.time()
    checkpoint_counter = 0

    # Process in parallel
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(enrich_lead, item): item[0] for item in to_process}

        for future in as_completed(futures):
            index, result = future.result()

            enrichments[str(index)] = result
            processed_indices.add(index)
            checkpoint_counter += 1

            # Periodic checkpoint save
            if checkpoint_counter >= CHECKPOINT_INTERVAL:
                checkpoint['processed_indices'] = list(processed_indices)
                checkpoint['enrichments'] = enrichments
                save_checkpoint(checkpoint)
                checkpoint_counter = 0

    # Final checkpoint save
    checkpoint['processed_indices'] = list(processed_indices)
    checkpoint['enrichments'] = enrichments
    save_checkpoint(checkpoint)

    elapsed = time.time() - start_time
    rate = len(to_process) / elapsed if elapsed > 0 else 0

    print(f"\n{'='*60}")
    print(f"ENRICHMENT COMPLETE")
    print(f"{'='*60}")
    print(f"Processed: {len(to_process)} leads in {elapsed:.1f}s ({rate:.1f} leads/sec)")
    print(f"Success: {progress['success']}")
    print(f"Errors: {progress['errors']}")

    # Apply enrichments to dataframe
    print(f"\nSaving to {OUTPUT_FILE}...")

    enrich_cols = [
        'verified_at_company', 'current_company', 'current_role',
        'verified_phone', 'verified_email', 'linkedin_url',
        'confidence', 'notes', 'last_enriched', 'enrichment_status', 'enrichment_error', 'raw_response'
    ]

    for col in enrich_cols:
        df[col] = None

    for idx_str, data in enrichments.items():
        idx = int(idx_str)
        for col in enrich_cols:
            if col in data:
                df.at[idx, col] = data[col]

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    df.to_excel(OUTPUT_FILE, index=False)
    print(f"✓ Saved to {OUTPUT_FILE}")

    # Summary stats
    success_count = sum(1 for e in enrichments.values() if e.get('enrichment_status') == 'success')
    verified_yes = sum(1 for e in enrichments.values() if e.get('verified_at_company') == 'Yes')
    verified_no = sum(1 for e in enrichments.values() if e.get('verified_at_company') == 'No')

    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Total leads: {total_leads}")
    print(f"Successfully enriched: {success_count}")
    print(f"Still at listed company: {verified_yes}")
    print(f"No longer at company: {verified_no}")
    print(f"High confidence: {sum(1 for e in enrichments.values() if e.get('confidence') == 'High')}")
    print(f"Medium confidence: {sum(1 for e in enrichments.values() if e.get('confidence') == 'Medium')}")
    print(f"Low confidence: {sum(1 for e in enrichments.values() if e.get('confidence') == 'Low')}")


if __name__ == "__main__":
    main()
