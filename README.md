# Daily PLM-PPI Literature Alert

This repository sends a daily email digest for recent papers related to:

- protein language models
- protein-protein interactions
- pair-aware / partner-conditioned PLMs
- sparse autoencoders and interpretable PLM features
- AlphaFold-Multimer interaction screening
- proximity labeling, PUP-IT, TurboID, BioID

The workflow searches PubMed, arXiv, bioRxiv, medRxiv, and OpenAlex, scores papers by relevance, removes papers that have already been sent, and emails an HTML digest.

## Quick Start

1. Create a new GitHub repository.
2. Copy these files into the repository:
   - `.github/workflows/daily-literature-alert.yml`
   - `scripts/daily_literature_alert.py`
   - `queries.yaml`
   - `requirements.txt`
   - `data/seen_articles.json`
3. In GitHub, go to:

   `Settings -> Secrets and variables -> Actions -> New repository secret`

4. Add these secrets:

   ```text
   RESEND_API_KEY
   EMAIL_FROM
   EMAIL_TO
   ```

   Example:

   ```text
   RESEND_API_KEY = re_xxxxxxxxx
   EMAIL_FROM = Literature Alert <alerts@yourdomain.com>
   EMAIL_TO = your.email@example.com
   ```

5. Open the `Actions` tab and manually run:

   `Daily PLM-PPI Literature Alert -> Run workflow`

6. If the test email works, the workflow will run every morning automatically.

## Email Service

The default email provider is [Resend](https://resend.com/).

Resend normally requires a verified sending domain. For testing, check Resend's current onboarding instructions, because sandbox rules may change.

## Schedule

The workflow currently runs at:

```text
06:00 UTC every day
```

That is 08:00 in Copenhagen during summer time. During winter time, it will be 07:00 Copenhagen time unless you change the cron expression.

GitHub Actions cron schedules always use UTC.

## Manual Run

You can run it manually from GitHub Actions. The workflow also supports inputs:

- `days_back`: number of recent days to search
- `min_score`: minimum relevance score to send
- `max_results`: maximum number of papers in the email
- `dry_run`: generate output without sending email

## Customize Queries

Edit `queries.yaml`.

The script combines:

- source-specific search queries
- scoring keywords
- high-priority phrase boosts
- exclusion terms

For example, you can add more plant-specific terms:

```yaml
positive_keywords:
  - Arabidopsis
  - plant interactome
  - stress response
```

## State File

The script updates:

```text
data/seen_articles.json
```

This prevents sending the same article repeatedly. The GitHub workflow commits this file back to the repository after each successful run.

## Local Test

```bash
python -m pip install -r requirements.txt
python scripts/daily_literature_alert.py --dry-run --days-back 7 --min-score 4
```

For a real email:

```bash
export RESEND_API_KEY="re_xxxxx"
export EMAIL_FROM="Literature Alert <alerts@yourdomain.com>"
export EMAIL_TO="your.email@example.com"
python scripts/daily_literature_alert.py --days-back 7 --min-score 4
```

