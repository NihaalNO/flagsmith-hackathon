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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def github_private_key_pem(self) -> str | None:
        return self.github_app_private_key.replace("\\n", "\n") if self.github_app_private_key else None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
