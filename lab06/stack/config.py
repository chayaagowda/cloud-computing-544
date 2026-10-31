"""Load and validate non-secret Lab06 settings."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
import shlex


class ConfigError(ValueError):
    """Raised when configuration is missing or invalid."""


def load_dotenv(path: str | Path = ".env") -> dict[str, str]:
    """Read KEY=VALUE settings without executing shell commands."""
    values = {}
    for number, raw in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ")
        key, separator, value = line.partition("=")
        if not separator:
            raise ConfigError(f"Invalid configuration on line {number}")
        try:
            parts = shlex.split(value, comments=True)
        except ValueError:
            raise ConfigError(f"Invalid quoting on line {number}") from None
        values[key.strip()] = " ".join(parts)
    return values


@dataclass(frozen=True)
class Tier:
    """Configuration for one application tier."""

    name: str
    role_name: str
    profile_name: str
    lt_name: str
    tg_name: str
    asg_name: str
    listener_port: int
    app_dir: Path

    @property
    def policy_name(self) -> str:
        """Return the inline IAM policy name."""
        return f"{self.role_name}-policy"

    @property
    def service_name(self) -> str:
        """Return the systemd service name."""
        return self.name


@dataclass(frozen=True)
class Settings:
    """Infrastructure and application settings; contains no secrets."""

    region: str
    ami_id: str
    key_name: str
    instance_type: str
    app_sg_id: str
    alb_name: str
    alb_sg_name: str
    az_a: str
    az_b: str
    app_port: int
    recovery_slo_seconds: int
    db_instance_id: str
    db_instance_class: str
    db_allocated_storage: int
    db_port: int
    db_name: str
    db_user: str
    db_sg_name: str
    db_subnet_group_name: str
    db_password_param: str
    cache_seconds: int
    gemini_param_name: str
    gemini_model: str
    key_cache_seconds: int
    contact_email: str
    weather: Tier
    advisor: Tier

    @property
    def tiers(self) -> tuple[Tier, Tier]:
        """Return both application tiers."""
        return self.weather, self.advisor

    def tier(self, name: str) -> Tier:
        """Find a tier by name."""
        return {"weather": self.weather, "advisor": self.advisor}[name]

    @classmethod
    def from_values(
        cls, values: dict[str, str], base_dir: str | Path = "."
    ) -> "Settings":
        """Build validated settings from non-secret values."""
        base = Path(base_dir)
        aliases = {
            "region": "AWS_REGION",
            "app_sg_id": "APP_SECURITY_GROUP_ID",
        }
        integer_fields = {
            "app_port", "recovery_slo_seconds",
            "db_allocated_storage", "db_port",
            "cache_seconds", "key_cache_seconds",
        }

        def required(key: str) -> str:
            value = values.get(key, "").strip()
            if not value:
                raise ConfigError(f"Missing setting: {key}")
            if any(character in value for character in "\n\r\0"):
                raise ConfigError(f"Invalid control character in {key}")
            return value

        def positive_integer(key: str) -> int:
            try:
                value = int(required(key))
            except ValueError:
                raise ConfigError(f"{key} must be a positive integer") from None
            if value <= 0:
                raise ConfigError(f"{key} must be a positive integer")
            return value

        def build_tier(name: str) -> Tier:
            prefix = name.upper()
            port = positive_integer(f"{prefix}_LISTENER_PORT")
            if port > 65535:
                raise ConfigError(f"{prefix}_LISTENER_PORT is out of range")
            return Tier(
                name=name,
                role_name=required(f"{prefix}_ROLE_NAME"),
                profile_name=required(f"{prefix}_PROFILE_NAME"),
                lt_name=required(f"{prefix}_LT_NAME"),
                tg_name=required(f"{prefix}_TG_NAME"),
                asg_name=required(f"{prefix}_ASG_NAME"),
                listener_port=port,
                app_dir=base / f"{name}-app",
            )

        kwargs = {}
        for field in fields(cls):
            if field.name in {"weather", "advisor"}:
                continue
            key = aliases.get(field.name, field.name.upper())
            kwargs[field.name] = (
                positive_integer(key)
                if field.name in integer_fields
                else required(key)
            )

        settings = cls(
            **kwargs,
            weather=build_tier("weather"),
            advisor=build_tier("advisor"),
        )
        if settings.az_a == settings.az_b:
            raise ConfigError("AZ_A and AZ_B must differ")
        if settings.weather.listener_port == settings.advisor.listener_port:
            raise ConfigError("Weather and Advisor listener ports must differ")
        if settings.app_port > 65535 or settings.db_port > 65535:
            raise ConfigError("Application and database ports must be valid")
        if settings.db_password_param == settings.gemini_param_name:
            raise ConfigError("The two secret parameter names must differ")
        return settings

    @classmethod
    def from_file(cls, path: str | Path = ".env") -> "Settings":
        """Load settings relative to the configuration file."""
        path = Path(path).resolve()
        return cls.from_values(load_dotenv(path), path.parent)
