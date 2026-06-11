alter table scrape_runs add column error_category text;
comment on column scrape_runs.error_category is 'Machine-readable failure category (e.g. not_found, blocked, no_results, missing_credentials, parse_error, config_error, unexpected_error) used to triage scrapers that need attention.';
