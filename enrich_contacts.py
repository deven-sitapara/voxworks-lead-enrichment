#!/usr/bin/env python3
"""
Contact Enrichment Script
Finds phone numbers and emails for leads missing contact data
"""

import glob
import json
import os
import time
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from groq import Groq
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

load_dotenv()

# Configuration
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
TODAY = datetime.now().strftime("%Y-%m-%d")

# Find the latest generated_leads file in output/ (match dated filenames only)
_candidates = sorted(glob.glob("output/generated_leads_????-??-??.xlsx"))
INPUT_FILE = _candidates[-1] if _candidates else "output/generated_leads.xlsx"
OUTPUT_FILE = f"output/generated_leads_enriched_{TODAY}.xlsx"
MAX_WORKERS = 5
REQUEST_DELAY = 1.5

lock = threading.Lock()
stats = {"completed": 0, "found_phone": 0, "found_email": 0, "errors": 0, "total": 0}
results = {}


def enrich_contact(args):
    """Search for contact details for a single lead"""
    idx, row = args
    time.sleep(REQUEST_DELAY)

    name = str(row.get('name', ''))
    company = str(row.get('company', ''))
    city = str(row.get('search_city', '')) or str(row.get('city', ''))
    role = str(row.get('role', ''))

    client = Groq(api_key=GROQ_API_KEY)

    prompt = f"""Search for the contact details of this real estate agent:

Name: {name}
Company: {company}
City: {city}, Australia
Role: {role}

Search their agency website, LinkedIn, RateMyAgent, Domain, RealEstate.com.au, and any other sources.

Find their:
1. Mobile phone number (Australian format starting with 04xx)
2. Office phone number
3. Email address
4. LinkedIn URL

Return ONLY a JSON object:
{{"phone": "mobile or office number", "email": "email address", "linkedin": "linkedin url", "source": "where you found it"}}

If you cannot find a piece of information, use null for that field.
Return ONLY the JSON, no other text."""

    try:
        resp = client.chat.completions.create(
            model="groq/compound",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )

        content = resp.choices[0].message.content.strip()

        # Parse JSON
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        if not content.startswith("{"):
            start = content.find("{")
            end = content.rfind("}") + 1
            if start != -1 and end > start:
                content = content[start:end]

        data = json.loads(content)

        # Check what we found
        found_phone = data.get('phone') and str(data['phone']).strip() not in ['null', 'None', '', 'N/A']
        found_email = data.get('email') and str(data['email']).strip() not in ['null', 'None', '', 'N/A'] and '@' in str(data.get('email', ''))

        with lock:
            stats["completed"] += 1
            if found_phone:
                stats["found_phone"] += 1
            if found_email:
                stats["found_email"] += 1
            results[idx] = data

            pct = stats["completed"] / stats["total"] * 100
            phone_display = str(data.get('phone', ''))[:15] if found_phone else "—"
            status = "✓" if found_phone else "○"
            print(f"[{stats['completed']:3d}/{stats['total']}] {status} {name[:28]:28} | {phone_display:15} | +{stats['found_phone']} phones")

        return idx, data

    except Exception as e:
        with lock:
            stats["completed"] += 1
            stats["errors"] += 1
            results[idx] = {"error": str(e)}
            print(f"[{stats['completed']:3d}/{stats['total']}] ✗ {name[:28]:28} | Error: {str(e)[:30]}")
        return idx, {"error": str(e)}


def main():
    print("=" * 70)
    print("CONTACT ENRICHMENT - Finding Missing Phone Numbers")
    print("=" * 70)

    # Load leads
    df = pd.read_excel(INPUT_FILE)
    df = df.fillna("")

    # Find leads missing phone numbers
    def is_missing_phone(x):
        val = str(x).strip().lower()
        return val in ['', 'null', 'none', 'nan', 'n/a', 'if found or null']

    missing_phone_mask = df['phone'].apply(is_missing_phone)
    missing_indices = df[missing_phone_mask].index.tolist()

    print(f"Total leads: {len(df)}")
    print(f"Missing phone: {len(missing_indices)}")
    print(f"Workers: {MAX_WORKERS}")
    print("=" * 70)
    print()

    stats["total"] = len(missing_indices)

    # Build tasks
    tasks = [(idx, df.loc[idx]) for idx in missing_indices]

    start_time = time.time()

    # Process in parallel
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(enrich_contact, task) for task in tasks]
        for f in as_completed(futures):
            pass

    elapsed = time.time() - start_time

    # Apply results to dataframe
    for idx, data in results.items():
        if "error" in data:
            continue

        # Update phone if found
        if data.get('phone') and str(data['phone']).strip() not in ['null', 'None', '', 'N/A']:
            df.at[idx, 'phone'] = data['phone']

        # Update email if found and we didn't have one
        current_email = str(df.at[idx, 'email']).strip().lower()
        if data.get('email') and '@' in str(data.get('email', '')):
            if current_email in ['', 'null', 'none', 'nan', 'n/a']:
                df.at[idx, 'email'] = data['email']

        # Update linkedin if found and we didn't have one
        current_linkedin = str(df.at[idx, 'linkedin']).strip().lower()
        if data.get('linkedin') and 'linkedin' in str(data.get('linkedin', '')).lower():
            if current_linkedin in ['', 'null', 'none', 'nan', 'n/a'] or 'linkedin' not in current_linkedin:
                df.at[idx, 'linkedin'] = data['linkedin']

        # Add source
        if data.get('source'):
            df.at[idx, 'contact_source'] = data['source']

    # Save
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    df.to_excel(OUTPUT_FILE, index=False)

    # Final stats
    print()
    print("=" * 70)
    print("ENRICHMENT COMPLETE")
    print("=" * 70)
    print(f"Time: {elapsed/60:.1f} minutes")
    print(f"Processed: {stats['completed']}")
    print(f"Errors: {stats['errors']}")
    print()
    print(f"NEW phones found: {stats['found_phone']}")
    print(f"NEW emails found: {stats['found_email']}")
    print()

    # New totals
    has_phone = df['phone'].apply(lambda x: str(x).strip().lower() not in ['', 'null', 'none', 'nan', 'n/a', 'if found or null']).sum()
    has_email = df['email'].apply(lambda x: '@' in str(x)).sum()

    print(f"TOTAL phones now: {has_phone} / {len(df)} ({has_phone/len(df)*100:.1f}%)")
    print(f"TOTAL emails now: {has_email} / {len(df)} ({has_email/len(df)*100:.1f}%)")
    print()
    print(f"Saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
