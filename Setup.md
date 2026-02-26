# Setup

## Copy the repo using git clone command

```bash
git clone <repo-url>
cd voxworks-lead-enrichment
```

## OR Download the zip file and extract it, then navigate to the extracted directory

## Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

## Install dependencies

```bash
pip install groq pandas python-dotenv openpyxl
```

## Configure environment

Copy the example env file and add your Groq API key:

```bash
cp .env.example .env
```

## Obtail Groq API key from [Groq Console](https://console.groq.com/keys) and add it to the .env file

Edit `.env`:

```
GROQ_API_KEY=your_groq_api_key_here
CONFIG_FILE=config.json
INPUT_FILE=input_leads.xlsx
```

## Search Configuration (`config.json`)

`generate_leads.py` loads its search targets from `config.json`. Edit this file to change what the script searches for without modifying Python code. All three keys are required.

### `cities` - List of cities to target in the search queries.

### `industries` - List of industries to target in the search queries.

### `job_titles` - List of job titles to target in the search queries.

## Usage

```bash
# Step 1: Generate leads from web search
python generate_leads.py
# Step 2: Enrich contact details for generated leads
python enrich_contacts.py
# Or: Enrich an existing lead list (update INPUT_FILE in the script)
python enrich_leads.py
```
