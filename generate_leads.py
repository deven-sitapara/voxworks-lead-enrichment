#!/usr/bin/env python3
"""
Smart Lead Generator
Generates targeted real estate agent leads using Groq compound (web search)
"""

import json
import os
import sys
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
OUTPUT_FILE = f"output/generated_leads_{TODAY}.xlsx"
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", 3))
REQUEST_DELAY = int(os.environ.get("REQUEST_DELAY", 2))
GROQ_MODEL = os.environ.get("GROQ_MODEL", "groq/compound-mini")


def load_config():
    """Load search configuration from config.json (path overridable via CONFIG_FILE env var)."""
    config_path = os.environ.get("CONFIG_FILE", "config.json")
    try:
        with open(config_path) as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"Error: Config file not found: {config_path}")
        print("Create a config.json or set CONFIG_FILE env var to the correct path.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {config_path}: {e}")
        sys.exit(1)

    required = ["cities", "search_templates", "boutique_agencies"]
    missing = [k for k in required if k not in config]
    if missing:
        print(f"Error: config.json missing required keys: {', '.join(missing)}")
        sys.exit(1)

    return config


config = load_config()
CITIES = config["cities"]
SEARCH_TEMPLATES = config["search_templates"]
BOUTIQUE_AGENCIES = config["boutique_agencies"]

lock = threading.Lock()
all_leads = []
stats = {"queries": 0, "leads": 0, "errors": 0}


def search_leads(query, category, city):
    """Use Groq compound to search and extract structured lead data"""
    time.sleep(REQUEST_DELAY)

    client = Groq(api_key=GROQ_API_KEY)

    prompt = f"""{query}

Return JSON array only:
[{{"name":"","company":"","role":"","city":"","phone":null,"email":null,"linkedin":null,"source":"","match_reason":""}}]

Find 5-10 agents. No markdown, just JSON array."""

    leads = []

    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1
            )

            content = resp.choices[0].message.content.strip()

            # Parse JSON - try multiple extraction methods
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            # Try to find JSON array in content
            if not content.startswith("["):
                start = content.find("[")
                end = content.rfind("]") + 1
                if start != -1 and end > start:
                    content = content[start:end]

            leads = json.loads(content)
            break  # Success, exit retry loop

        except json.JSONDecodeError:
            if attempt < 2:
                time.sleep(REQUEST_DELAY)
                continue
            # Final attempt failed
            with lock:
                stats["queries"] += 1
                stats["errors"] += 1
                print(f"[{stats['queries']:3d}] {city:12} | {category:25} | JSON parse error")
            return []

        except Exception as e:
            if "429" in str(e) and attempt < 2:
                time.sleep(REQUEST_DELAY * 3)
                continue
            # Non-retryable error or final attempt
            with lock:
                stats["queries"] += 1
                stats["errors"] += 1
                print(f"[{stats['queries']:3d}] {city:12} | {category:25} | Error: {str(e)[:40]}")
            return []

    # Add metadata to successful leads
    for lead in leads:
        lead["search_category"] = category
        lead["search_city"] = city
        lead["generated_at"] = datetime.now().isoformat()

    with lock:
        stats["queries"] += 1
        stats["leads"] += len(leads)
        all_leads.extend(leads)
        print(f"[{stats['queries']:3d}] {city:12} | {category:25} | Found {len(leads):2d} leads")

    return leads


def search_agency_agents(agency, city):
    """Search for agents at a specific agency - both senior and junior"""
    query = f"Find real estate agents at {agency} in {city}, Australia. Include principals/directors AND sales associates, BDMs, and junior agents who do prospecting. Look for agents who handle high volumes of calls and inquiries."
    return search_leads(query, f"Agency: {agency[:20]}", city)


def deduplicate_leads(leads):
    """Remove duplicate leads based on name + company"""
    seen = set()
    unique = []

    for lead in leads:
        name = str(lead.get("name") or "").lower().strip()
        company = str(lead.get("company") or "").lower().strip()

        # Skip empty names, admin entries, etc.
        if not name or name == "nan" or "admin" in name or "reception" in name:
            continue

        key = (name, company)
        if key not in seen:
            seen.add(key)
            unique.append(lead)

    return unique


def main():
    print("=" * 70)
    print("SMART LEAD GENERATOR")
    print("=" * 70)
    print(f"Cities: {len(CITIES)}")
    print(f"Search categories: {len(SEARCH_TEMPLATES)}")
    print(f"Boutique agencies to search: {sum(len(v) for v in BOUTIQUE_AGENCIES.values())}")
    print("=" * 70)

    start_time = time.time()

    # Build all search tasks
    tasks = []

    # Category searches for each city
    for city in CITIES:
        for template in SEARCH_TEMPLATES:
            query = template["query"].format(city=city)
            tasks.append((query, template["category"], city))

    # Specific agency searches
    for city, agencies in BOUTIQUE_AGENCIES.items():
        for agency in agencies:
            tasks.append((None, agency, city))  # None query = agency search

    print(f"Total search tasks: {len(tasks)}")
    print("=" * 70)
    print()

    # Execute searches
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []
        for task in tasks:
            if task[0] is None:
                # Agency-specific search
                futures.append(executor.submit(search_agency_agents, task[1], task[2]))
            else:
                # Category search
                futures.append(executor.submit(search_leads, task[0], task[1], task[2]))

        # Wait for completion
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"Task error: {e}")

    elapsed = time.time() - start_time

    print()
    print("=" * 70)
    print("DEDUPLICATING")
    print("=" * 70)

    unique_leads = deduplicate_leads(all_leads)
    print(f"Raw leads: {len(all_leads)}")
    print(f"After deduplication: {len(unique_leads)}")

    # Convert to DataFrame
    df = pd.DataFrame(unique_leads)

    # Reorder columns
    column_order = ["name", "company", "role", "city", "phone", "email", "linkedin",
                    "match_reason", "search_category", "source", "search_city", "generated_at"]
    df = df.reindex(columns=[c for c in column_order if c in df.columns])

    # Save
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    df.to_excel(OUTPUT_FILE, index=False)

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Time: {elapsed/60:.1f} minutes")
    print(f"Queries: {stats['queries']}")
    print(f"Errors: {stats['errors']}")
    print(f"Unique leads: {len(unique_leads)}")
    print()

    # Breakdown by city
    print("Leads by city:")
    for city in CITIES:
        count = len([l for l in unique_leads if l.get("city", "").lower() == city.lower() or l.get("search_city", "").lower() == city.lower()])
        print(f"  {city:15} {count:4d}")

    print()
    print("Leads by category:")
    categories = {}
    for lead in unique_leads:
        cat = lead.get("search_category", "Unknown")
        categories[cat] = categories.get(cat, 0) + 1
    for cat, count in sorted(categories.items(), key=lambda x: -x[1])[:10]:
        print(f"  {cat:30} {count:4d}")

    print()
    print(f"Saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
