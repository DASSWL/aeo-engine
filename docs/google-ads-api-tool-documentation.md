# Vivu AEO Engine — Keyword Research Tool
## Google Ads API Tool Documentation (Basic Access Application)

**Company:** Vivu (vivu.ai)
**Tool name:** AEO Engine — keyword_volume
**Tool type:** Internal-use only, non-commercial automation script
**Google Ads account:** 416-830-6862 (single account, owned by Vivu)

---

## 1. What the tool does

Vivu is a video search product (vivu.ai). Our marketing team maintains a small
internal library of search queries that prospective customers use when looking
for solutions like ours. This tool enriches that internal keyword library with
**historical search volume metrics** from the Google Ads API, so that our
content team can prioritize which topics to write about.

The tool is a single Python script that runs on a schedule (once per week):

1. It reads a curated list of keywords (typically 30–100) from our internal
   database (Notion).
2. It calls the Google Ads API to retrieve average monthly search volume for
   those keywords.
3. It writes the metrics back into the same internal database.

That is the entire functionality. It is a **read-only reporting integration**.

## 2. Google Ads API services used

| Service | Method | Purpose |
|---|---|---|
| `KeywordPlanIdeaService` | `GenerateKeywordHistoricalMetrics` | Retrieve avg. monthly searches for our curated keyword list |

The tool does **not**:

- create, modify, or manage campaigns, ad groups, ads, budgets, or bids;
- access any Google Ads account other than our own (416-830-6862);
- serve ads or make automated bidding decisions;
- expose any functionality to third parties.

## 3. Expected API usage volume

- **1 API request per week** (one `GenerateKeywordHistoricalMetrics` call
  containing the full keyword list), plus occasional manual re-runs.
- Estimated total: **fewer than 20 API calls per month.**

## 4. Who uses the tool

Internal use only. The tool is operated by Vivu's founder/marketing team
(fewer than 5 people). It is not offered, sold, licensed, or exposed to any
external user. There is no user interface; it is a command-line script whose
output goes to an internal Notion workspace.

## 5. Data handling

- Retrieved metrics are stored in a private internal Notion database used for
  content planning.
- Data is not resold, redistributed, published, or co-mingled with data from
  other advertisers.
- OAuth credentials and the developer token are stored locally in an
  environment file on a single company machine, outside version control.

## 6. Architecture

```
[Internal keyword library (Notion)]
        │  read keyword list
        ▼
[keyword_volume.py — Python script, runs weekly on one company machine]
        │  GenerateKeywordHistoricalMetrics (OAuth 2.0, own account only)
        ▼
[Google Ads API]
        │  avg. monthly searches
        ▼
[Internal keyword library (Notion)] — metrics written back
```

Authentication: OAuth 2.0 desktop-app flow with a refresh token for a Google
account that has access to our own Ads account. No service accounts, no MCC.

## 7. Contact

- Website: https://vivu.ai
- API contact email: (the email on file in the API Center)
