"""
Configuration settings for PFactory Web Server.

Settings are loaded from environment variables with sensible defaults.
"""

import secrets
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings

from .paths import get_data_dir, get_data_file


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Server configuration
    HOST: str = "0.0.0.0"
    PORT: int = 3114
    DEBUG: bool = False

    # SSL configuration
    SSL_ENABLED: bool = False
    SSL_CERTFILE: str = ""  # Path to SSL certificate
    SSL_KEYFILE: str = ""   # Path to SSL private key

    # Authentication
    API_TOKEN: str = ""  # Will generate default if not set
    DISABLE_AUTH: bool = False  # Set to True to disable auth (dev only)

    # JWT Configuration
    JWT_SECRET: str = ""  # Auto-generated if not set
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    JWT_ALGORITHM: str = "HS256"

    # Database
    DATABASE_URL: str = ""  # Auto-generated if not set (sqlite+aiosqlite:///...)
    # Alembic migration behaviour at app boot. P1.4 of Epic #26.
    #   true  → app boot runs `alembic upgrade head` (default; suits local
    #           dev + simple deployments)
    #   false → app boot only verifies the schema is at head and fails fast
    #           if not. Use this in K8s deployments where a Helm Job runs
    #           migrations out-of-band before the app pods start (allows
    #           the app role to lack DDL privileges).
    MIGRATIONS_AUTO_APPLY: bool = True

    # Paths
    PROJECTS_DATA_DIR: str = ""  # Directory to store project metadata
    BACKEND_PATH: str = ""  # Path to apps/backend

    # CORS — localhost defaults. Override or extend via APP_CORS_ORIGINS env var.
    # Accepts a comma-separated string ("https://a.com,https://b.com") or a JSON list.
    CORS_ORIGINS: list[str] = [
        "http://localhost:3115",
        "http://localhost:3000",
        "https://localhost:3115",
        "https://localhost:3000",
        "https://localhost:3114",
    ]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v):
        if isinstance(v, str):
            stripped = v.strip()
            if stripped.startswith("["):
                # Let pydantic handle JSON-list form natively
                return stripped
            return [s.strip() for s in stripped.split(",") if s.strip()]
        return v

    # Terminal
    DEFAULT_SHELL: str = "/bin/bash"
    MAX_TERMINALS: int = 20

    # Task execution
    MAX_CONCURRENT_TASKS: int = 5

    # Liveness watchdog (#95) — periodic sweep that flags a silent in-flight
    # stage as `stalled`. OFF by default; opt in with APP_LIVENESS_SWEEP_ENABLED.
    LIVENESS_SWEEP_ENABLED: bool = False
    LIVENESS_SWEEP_INTERVAL_SECONDS: int = 300  # how often to sweep
    LIVENESS_SWEEP_DEADLINE_SECONDS: float = 600  # idle budget before stalled

    class Config:
        env_file = ".env"
        env_prefix = "APP_"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Generate default token if not set
        if not self.API_TOKEN:
            self.API_TOKEN = self._get_or_generate_token()

        # Generate JWT secret if not set
        if not self.JWT_SECRET:
            self.JWT_SECRET = self._get_or_generate_jwt_secret()

        # Set default paths
        if not self.BACKEND_PATH:
            # Assume we're in apps/web-server, backend is at ../backend
            self.BACKEND_PATH = str(
                Path(__file__).parent.parent.parent / "backend"
            )

        if not self.PROJECTS_DATA_DIR:
            self.PROJECTS_DATA_DIR = str(get_data_dir())

        # Set default database URL
        if not self.DATABASE_URL:
            self.DATABASE_URL = (
                f"sqlite+aiosqlite:///{self.PROJECTS_DATA_DIR}/data.db"
            )

        # Set up SSL paths if enabled
        if self.SSL_ENABLED:
            self._setup_ssl()

    def _get_or_generate_token(self) -> str:
        """Get existing token or generate a new one."""
        token_file = get_data_file(".token")

        if token_file.exists():
            return token_file.read_text().strip()

        # Generate new token
        token = secrets.token_urlsafe(32)

        # Save token
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(token)
        token_file.chmod(0o600)  # Owner read/write only

        print(f"\n{'='*60}")
        print("PFactory - First Run Setup")
        print(f"{'='*60}")
        print(f"Generated API token: {token}")
        print(f"Token saved to: {token_file}")
        print("\nUse this token to authenticate API requests:")
        print(f"  Authorization: Bearer {token}")
        print(f"{'='*60}\n")

        return token

    def _get_or_generate_jwt_secret(self) -> str:
        """Get existing JWT secret or generate a new one.

        The secret is persisted to ~/.pfactory/.jwt_secret so it
        survives server restarts, keeping existing tokens valid.
        """
        secret_file = get_data_file(".jwt_secret")

        if secret_file.exists():
            return secret_file.read_text().strip()

        # Generate new secret
        secret = secrets.token_urlsafe(32)

        # Save secret
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(secret)
        secret_file.chmod(0o600)  # Owner read/write only

        return secret

    def _setup_ssl(self) -> None:
        """Set up SSL certificates, generating self-signed if needed."""
        import subprocess

        ssl_dir = get_data_dir() / "ssl"
        ssl_dir.mkdir(parents=True, exist_ok=True)

        cert_file = ssl_dir / "cert.pem"
        key_file = ssl_dir / "key.pem"

        # Use provided paths or defaults
        if self.SSL_CERTFILE and self.SSL_KEYFILE:
            # User provided custom paths
            if not Path(self.SSL_CERTFILE).exists():
                raise ValueError(f"SSL certificate not found: {self.SSL_CERTFILE}")
            if not Path(self.SSL_KEYFILE).exists():
                raise ValueError(f"SSL key not found: {self.SSL_KEYFILE}")
            return

        # Generate self-signed certificate if not exists
        if not cert_file.exists() or not key_file.exists():
            print(f"\n{'='*60}")
            print("PFactory - SSL Setup")
            print(f"{'='*60}")
            print("Generating self-signed SSL certificate...")

            try:
                subprocess.run(
                    [
                        "openssl", "req", "-x509", "-newkey", "rsa:4096",
                        "-keyout", str(key_file),
                        "-out", str(cert_file),
                        "-days", "365",
                        "-nodes",
                        "-subj", "/CN=localhost/O=PFactory/C=US"
                    ],
                    check=True,
                    capture_output=True
                )
                key_file.chmod(0o600)
                print(f"Certificate generated: {cert_file}")
                print(f"Private key generated: {key_file}")
                print("\nNOTE: This is a self-signed certificate.")
                print("Your browser will show a security warning.")
                print(f"{'='*60}\n")
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Failed to generate SSL certificate: {e.stderr.decode()}")
            except FileNotFoundError:
                raise RuntimeError("OpenSSL not found. Install OpenSSL to enable HTTPS.")

        # Set paths to generated certificates
        self.SSL_CERTFILE = str(cert_file)
        self.SSL_KEYFILE = str(key_file)


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """Get the settings instance."""
    return settings
