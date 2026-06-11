"""Nightly orchestrator.

1. Sync `config/sources.yaml` into the `sources` table.
2. Run every active source's scraper.
3. Geo-filter, deduplicate, and upsert results into Supabase.
4. Print a summary report. Any source with a non-empty
   `error_category` is called out under "ACTION NEEDED" with a
   concrete hint, so a broken/misconfigured source can be fixed in
   config/sources.yaml without a debugging session.

Exit code is non-zero if any source ended in `failed` status, which
makes the nightly GitHub Action run show red for sources that need
attention while still letting all other sources write their data.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from scraper import db
from scraper.dedupe import compute_dedup_hash
from scraper.errors import NoResultsError, ScraperError
from scraper.geo import in_corridor
from scraper.sources import SCRAPER_REGISTRY

logger = logging.getLogger("whats_on_orlando")

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sources.yaml"


def load_source_configs() -> list[dict[str, Any]]:
    data = yaml.safe_load(CONFIG_PATH.read_text())
    return data["sources"]


def run_source(client, cfg: dict[str, Any], source_row: dict[str, Any]) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    found = new = updated = dropped = 0
    status = "success"
    error_category: str | None = None
    error_message: str | None = None

    scraper_cls = SCRAPER_REGISTRY.get(cfg["scraper"])
    if scraper_cls is None:
        status = "failed"
        error_category = "config_error"
        error_message = (
            f"[config_error] Unknown scraper type '{cfg['scraper']}' "
            f"-- HINT: valid types are {', '.join(sorted(SCRAPER_REGISTRY))}"
        )
    else:
        try:
            scraper = scraper_cls(source_row)
            for event in scraper.fetch_events():
                found += 1
                if not in_corridor(event.latitude, event.longitude, event.city):
                    dropped += 1
                    continue
                event.dedup_hash = compute_dedup_hash(event.title, event.venue_name, event.start_datetime)
                _, is_new = db.upsert_event(client, event, source_row["id"])
                if is_new:
                    new += 1
                else:
                    updated += 1

            if found == 0 and not cfg.get("allow_empty", False):
                raise NoResultsError(
                    "Scraper ran without errors but returned 0 events.",
                    hint=(
                        "Check that the source currently has events listed and that the "
                        "endpoint/selectors/config in config/sources.yaml are still correct. "
                        "If 0 is expected sometimes for this source, set `allow_empty: true`."
                    ),
                )
        except ScraperError as exc:
            error_category = exc.category
            error_message = exc.format()
            status = "partial" if found > 0 else "failed"
        except Exception as exc:  # noqa: BLE001 - capture & report every failure mode
            error_category = "unexpected_error"
            error_message = (
                f"[unexpected_error] {exc.__class__.__name__}: {exc} "
                "-- HINT: check the full traceback in this run's logs."
            )
            status = "partial" if found > 0 else "failed"
            logger.exception("%s: unexpected error", cfg["slug"])

    finished_at = datetime.now(timezone.utc)
    db.log_scrape_run(
        client,
        source_row["id"],
        started_at,
        finished_at,
        status,
        found,
        new,
        updated,
        error_message=error_message,
        error_category=error_category,
    )

    return {
        "slug": cfg["slug"],
        "status": status,
        "found": found,
        "new": new,
        "updated": updated,
        "dropped": dropped,
        "error_category": error_category,
        "error_message": error_message,
    }


def _print_summary(results: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 78)
    print("SCRAPE SUMMARY")
    print("=" * 78)
    print(f"{'source':35s} {'status':9s} {'found':>6s} {'new':>5s} {'updated':>8s} {'dropped':>8s}")
    total_new = total_updated = 0
    needs_attention = []
    for r in results:
        print(
            f"{r['slug']:35s} {r['status']:9s} {r['found']:6d} {r['new']:5d} "
            f"{r['updated']:8d} {r['dropped']:8d}"
        )
        total_new += r["new"]
        total_updated += r["updated"]
        if r["error_category"]:
            needs_attention.append(r)

    print("-" * 78)
    print(f"Total new: {total_new}   Total updated: {total_updated}")

    if needs_attention:
        print("\n" + "=" * 78)
        print("ACTION NEEDED")
        print("=" * 78)
        for r in needs_attention:
            print(f"\n{r['slug']}  [{r['error_category']}]")
            print(f"  {r['error_message']}")
        print(
            "\nSee config/sources.yaml to adjust these sources: switch the `scraper` "
            "type, fix endpoint/selectors, set `is_active: false`, or add "
            "`allow_empty: true` if 0 results is expected for this source."
        )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    client = db.get_client()
    configs = load_source_configs()
    source_rows = db.sync_sources(client, configs)

    results = []
    for cfg in configs:
        if not cfg.get("is_active", True):
            continue
        logger.info("Running source: %s (%s)", cfg["slug"], cfg["scraper"])
        result = run_source(client, cfg, source_rows[cfg["slug"]])
        suffix = f" | {result['error_message']}" if result["error_message"] else ""
        logger.info(
            "  -> %s | found=%d new=%d updated=%d%s",
            result["status"], result["found"], result["new"], result["updated"], suffix,
        )
        results.append(result)

    _print_summary(results)

    failed = [r for r in results if r["status"] == "failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
