# Marketplace plan quotes

Collects publicly available ACA Marketplace plan quotes (healthCare.gov states, plus Virginia's and Maryland's state platform) for a grid of counties (FIPS codes) and households (scenarios). Includes a notebook to analyze and map the results. Research data only: not insurance advice or eligibility guidance.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Use

1. **Configure** `marketplaces.json`: the scenario grid and the HTTP request settings.
2. **Pull** with `run_scrapers.ipynb`
- Uses the states in `STATES` variable along with what module (e.g., "federal").
- The notebook resolves each state's counties (ZIPs --> counties)
- ZIPs to counties is used since pricing responses depend on the county, not the ZIP
- For each county, quotes are pulled every scenario (i.e. household configuration)
- States run one after another
- Each writes `<timestamp>_<state>.raw.jsonl` to `output_dir`
3. **Compile** with `compile_csvs.ipynb`
- Turns the raw files into `data/csv/<run_id>/<county_fips>.csv` (one file per county)
- One row per plan per scenario and county
- The row format is defined in that notebook.
4. **Analyze** with `analysis.ipynb`
- Downloads the Census TIGER boundaries on first use
- Aggregates the CSVs per county and shows them on a Leaflet map
- An optional last section maps the same values back onto ZIP codes

## Configuration (`marketplaces.json`)

The states to run, and the module each uses (`federal`, `virginia`, `maryland`), are listed in
`STATES` at the top of `run_scrapers.ipynb`.

| Key | Meaning |
|---|---|
| `scenario` | The grid: `zip_codes` (`"*"` = every ZIP in the state), `ages`, `num_dependents`, `spouse`, `genders`, `fpl_multipliers` (income as a multiple of the poverty level) |
| `http` | `delay_seconds` before each request, `max_concurrency` requests at once, retries and User-Agent |
| `output_dir` | Where runs go |

Scenarios = counties × ages × dependents × spouse × genders × incomes, and each takes one to three
requests or more (one per 10 plans on HealthCare.gov), so check the count the run notebook prints
before starting. On HealthCare.gov, gender, ages above 65 and more than three dependents do not
change the quotes.

## Changing the CSV columns

Each site in `scrapers/` has a `parser.py` that iterates the plan objects in its responses
(`scrapers.iter_raw(path)` reads a raw file, `scrapers.iter_plans(entry)` yields the plans in one
response). The CSV columns are built from those plans in `compile_csvs.ipynb` under **Row format**
(`scenario_columns` and one `<site>_rows` function per site). Edit them, check the preview, and compile with `OVERWRITE = True`. No re-scraping is needed.
