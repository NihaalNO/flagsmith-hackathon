from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "FlagPilot API"
    database_url: str
    # Fernet key used to encrypt integration secrets (API tokens) at rest.
    # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: str
    # Supabase project URL; the API verifies Supabase Auth access tokens against
    # its public signing keys (<url>/auth/v1/.well-known/jwks.json).
    supabase_url: str
    # Public (publishable) key: safe in browsers; used by the /dev/login test page.
    supabase_publishable_key: str = ""
    # GitHub App (code scanning + cleanup PRs). Private key may use literal "\n".
    github_app_id: int | None = None
    github_app_slug: str | None = None
    github_app_private_key: str | None = None
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    # Lifecycle engine (services/lifecycle-engine): Flagsmith environment names that count as
    # production when judging rollout state. Matching is case-insensitive.
    production_environment_names: str = "production,prod,live"
    # Optional LLM narrative for cleanup plans: "groq", "openai-compatible", "anthropic",
    # "claude-code" (local only: needs a signed-in Claude Code CLI) or "mock". Empty = deterministic.
    flagpilot_llm_provider: str = ""
    flagpilot_llm_model: str = ""
    # openai-compatible only: server root including the API version path, e.g.
    # https://api.openai.com/v1, https://openrouter.ai/api/v1, http://localhost:11434/v1 (Ollama).
    flagpilot_llm_base_url: str = ""
    flagpilot_llm_api_key: str = ""  # optional for local servers
    # auto | json_schema | json_object | none. auto picks per provider/model (Groq: json_schema
    # only on models that support it, JSON mode otherwise).
    flagpilot_llm_json_mode: str = "auto"
    # groq only (Groq's conventional variable). FLAGPILOT_LLM_API_KEY takes precedence if set.
    groq_api_key: str = ""

    @property
    def production_environment_list(self) -> set[str]:
        return {n.strip().lower() for n in self.production_environment_names.split(",") if n.strip()}

    # env_ignore_empty: blank entries copied from .env.example (e.g. `GITHUB_APP_ID=`) count as
    # unset, so optional settings fall back to their defaults instead of failing to parse.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    @property
    def github_private_key_pem(self) -> str | None:
        return self.github_app_private_key.replace("\\n", "\n") if self.github_app_private_key else None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
