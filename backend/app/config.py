"""Central configuration, loaded from environment variables / backend/.env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # .../backend


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- LLM (Google Gemini, free tier) ---
    gemini_api_key: str = ""
    # "fast" tier: high-volume agent steps (research loops, routing).
    model_fast: str = "gemini-3.5-flash-lite"
    # "smart" tier: planning, writing and critique, where quality matters most.
    model_smart: str = "gemini-3-flash-preview"
    # Tried in order when a model is rate-limited (429) or unavailable (5xx).
    model_fallbacks: str = "gemini-3.1-flash-lite,gemini-3.8-flash,gemini-flash-lite-latest"
    embed_model: str = "gemini-embedding-001"
    embed_dim: int = 768

    # --- Runtime ---
    data_dir: Path = BASE_DIR / "data"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Optional regex for extra allowed origins, e.g. Vercel preview URLs: https://myapp-.*\.vercel\.app
    cors_origin_regex: str = ""
    max_concurrent_runs: int = 2
    max_concurrent_llm_calls: int = 4
    llm_timeout_s: float = 120.0
    llm_cache: bool = True  # response cache: reproducible evals + saves free-tier quota
    tool_cache_ttl_s: int = 24 * 3600
    mcp_mode: str = "stdio"  # "stdio" (subprocess MCP server) or "inprocess"

    # --- Abuse protection for the public demo ---
    access_code: str = ""  # if set, write endpoints require header X-Access-Code
    runs_per_hour_per_ip: int = 20

    # Optional free OpenAlex key (openalex.org/settings/api): raises the daily budget 10x.
    openalex_api_key: str = ""
    # Optional contact e-mail sent to OpenAlex / Crossref (their "polite pool").
    contact_email: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "veritas.db"

    @property
    def fallback_models(self) -> list[str]:
        return [m.strip() for m in self.model_fallbacks.split(",") if m.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def model_chain(self, tier: str) -> list[str]:
        """Ordered list of models to try for a tier, primary first, without duplicates."""
        primary = self.model_smart if tier == "smart" else self.model_fast
        secondary = self.model_fast if tier == "smart" else self.model_smart
        chain: list[str] = []
        for m in [primary, *self.fallback_models, secondary]:
            if m and m not in chain:
                chain.append(m)
        return chain


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
