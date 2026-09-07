"""Configuration, read from the environment.

Nothing in here has a hardcoded secret. Local development reads a .env file;
Render and GitHub Actions inject the same names as real environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Minimal .env loader.

    Deliberately not python-dotenv: this is ten lines, has no dependency, and
    an environment variable that is already set always wins, which is what we
    want in production where there is no .env file at all.
    """
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    # --- Database ---------------------------------------------------------
    # Neon pooled connection string. Render Postgres is deliberately NOT used:
    # its free tier expires 30 days after creation and this URL has to survive
    # until May 2027.
    database_url: str = field(
        default_factory=lambda: os.environ.get("DATABASE_URL", "")
    )

    # --- Socrata ----------------------------------------------------------
    socrata_domain: str = field(
        default_factory=lambda: os.environ.get(
            "SOCRATA_DOMAIN", "data.cityofnewyork.us"
        )
    )
    socrata_dataset: str = field(
        default_factory=lambda: os.environ.get("SOCRATA_DATASET", "erm2-nwe9")
    )
    # Optional. Unauthenticated requests are throttled and share a pool; an
    # app token raises the limit substantially and costs nothing to obtain.
    socrata_app_token: str | None = field(
        default_factory=lambda: os.environ.get("SOCRATA_APP_TOKEN") or None
    )

    # Socrata caps $limit at 50000 for the JSON endpoint. 5000 keeps each
    # response small enough that a mid-page failure is cheap to retry.
    page_size: int = field(default_factory=lambda: _int("PAGE_SIZE", 5000))
    # Safety rail so a bad watermark cannot try to pull the whole 40M-row
    # dataset into a 0.5 GB database.
    max_pages_per_run: int = field(
        default_factory=lambda: _int("MAX_PAGES_PER_RUN", 40)
    )

    # --- Retention (Neon free tier is 0.5 GB; see docs/retention) ----------
    # raw_requests stores the full Socrata JSON payload per row (~1.6 KB), so
    # this window is what decides whether the 0.5 GB budget holds. 7 days, not
    # 30: a 30-day window at ~8k rows/day filled Neon on the first real ingest.
    # See BREAKS.md 2026-09-07.
    raw_retention_days: int = field(
        default_factory=lambda: _int("RAW_RETENTION_DAYS", 7)
    )
    clean_retention_days: int = field(
        default_factory=lambda: _int("CLEAN_RETENTION_DAYS", 60)
    )
    # daily_agg is never pruned. It is the only permanent table and it is what
    # makes the app interesting in April with eight months of trend data.

    # --- Deduplication ----------------------------------------------------
    # Minutes. Two requests of the same complaint type at the same normalized
    # address within this window are treated as one submission. Tuned; see
    # BREAKS.md and docs/dedup-tuning.md for what 15 / 60 / 1440 looked like.
    dedup_window_minutes: int = field(
        default_factory=lambda: _int("DEDUP_WINDOW_MINUTES", 60)
    )

    # --- API --------------------------------------------------------------
    cors_origins: str = field(
        default_factory=lambda: os.environ.get("CORS_ORIGINS", "*")
    )

    @property
    def socrata_url(self) -> str:
        return f"https://{self.socrata_domain}/resource/{self.socrata_dataset}.json"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def require_database_url(self) -> str:
        if not self.database_url:
            raise RuntimeError(
                "DATABASE_URL is not set. Copy .env.example to .env and paste "
                "your Neon pooled connection string into it."
            )
        return self.database_url


settings = Settings()
