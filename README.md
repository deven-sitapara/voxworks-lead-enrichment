# Voxworks Lead Enrichment

A lead generation and enrichment pipeline powered by [Groq's compound model](https://groq.com/) with web search capabilities.

## Overview

This project automates three stages of lead prospecting:

1. **Generate** - Discover leads across specific locations via web search
2. **Enrich** - Verify and augment an existing lead list with up-to-date information
3. **Fill Gaps** - Find missing phone numbers and emails for generated leads

All scripts use Groq's `groq/compound` model for AI-driven web search, parallel processing for throughput, and Excel files for input/output.

## Scripts

### `generate_leads.py`

Searches the web for leads within specific location using targeted search categories.

Also searches a curated list of companies by name. 

Outputs deduplicated leads with name, company, role, city, phone, email, LinkedIn, and match reason.

**Output:** `generated_leads.xlsx`

### `enrich_leads.py`

Takes an existing Excel lead list and enriches each contact via web search:

- Verifies if the person still works at their listed company
- Finds current role, phone, email, and LinkedIn URL
- Assigns a confidence rating (High/Medium/Low)
- Adds notes on awards, specializations, and experience

Supports checkpointing and resume so interrupted runs can pick up where they left off.

**Input:** Excel file with columns like `Contact Name`, `Agency Name`, `Suburb`, `State`
**Output:** `enriched_leads.xlsx`

### `enrich_contacts.py`

A targeted enrichment pass that finds missing contact details (phone numbers, emails, LinkedIn URLs) for leads that were generated but lack this data.

**Input:** `generated_leads.xlsx`
**Output:** `generated_leads_enriched.xlsx`

## Setup

### Requirements

- Python 3.8+
- Dependencies: `groq`, `pandas`, `openpyxl`

```bash
pip install groq pandas openpyxl
```

### Configuration

Set your Groq API key as an environment variable or in a `.env` file:

```
GROQ_API_KEY=your_api_key_here
```

## Usage

```bash
# Step 1: Generate leads from web search
python generate_leads.py

# Step 2: Enrich contact details for generated leads
python enrich_contacts.py

# Or: Enrich an existing lead list (update INPUT_FILE in the script)
python enrich_leads.py
```

## Search Configuration (`config.json`)

`generate_leads.py` loads its search targets from `config.json`. Edit this file to change what the script searches for without modifying Python code. All three keys are required.

### `cities`

A flat array of city/region names. Every search template runs once per city, so adding cities multiplies the total number of API calls. Start small if you're testing.

```json
"cities": ["Sydney", "Melbourne", "Brisbane"]
```

### `search_templates`

Each template defines a search category and a query prompt. The query is sent to the Groq compound model (which has web search), so write it like you'd ask a research assistant — be specific about what kind of agents you want and what seniority levels to include.

- **`category`** — A short label used in the output spreadsheet to group results (e.g. "Top Performers", "BDMs & Prospectors").
- **`query`** — The search prompt. Use `{city}` as a placeholder — it gets replaced with each city at runtime. Be descriptive: mention job titles, company names, ranking lists, or any specifics that help the search return relevant agents.

```json
"search_templates": [
  {
    "category": "Project Marketing",
    "query": "Search for off-the-plan and project marketing real estate agents in {city}. Find agents who specialize in selling new apartment developments for developers."
  }
]
```

Tips for writing good queries:
- Name specific companies or ranking lists to get targeted results
- Ask for both senior and junior agents if you want a range of seniority
- Each template runs once per city, so one template across 8 cities = 8 API calls

### `boutique_agencies`

A map of city name to a list of specific agency names. Each agency gets its own dedicated search (separate from the templates above), so the script can find individual agents at that office.

Cities listed here don't need to match the `cities` array — you can search agencies in cities you're not running templates for, and vice versa.

```json
"boutique_agencies": {
  "Sydney": ["BresicWhitney", "Di Jones", "PPD Real Estate"],
  "Melbourne": ["Jellis Craig", "Marshall White"]
}
```

### Custom config path

To use a config file at a different path, set the `CONFIG_FILE` environment variable:

```bash
CONFIG_FILE=custom_config.json python generate_leads.py
```

## Configuration Options

Each script has tunable constants at the top of the file:

| Parameter | Description | Default |
|---|---|---|
| `MAX_WORKERS` | Parallel threads | 3-5 |
| `REQUEST_DELAY` | Delay between API calls (seconds) | 0.5-2 |
| `RETRY_ATTEMPTS` | Retries on failure | 3-5 |
| `CHECKPOINT_INTERVAL` | Leads between checkpoint saves | 50 |
