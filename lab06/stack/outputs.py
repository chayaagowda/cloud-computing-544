"""Save non-secret identifiers for the Weather Advisor stack."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class StackOutputs:
    """Resource identifiers used by tests and cleanup tools."""

    vpc_id: str
    subnet_a: str
    subnet_b: str
    alb_security_group_id: str
    alb_arn: str
    alb_dns_name: str
    weather_tg_arn: str
    advisor_tg_arn: str

    def tg_arn(self, tier_name: str) -> str:
        """Return the target group ARN for a tier."""
        return {
            "weather": self.weather_tg_arn,
            "advisor": self.advisor_tg_arn,
        }[tier_name]

    def save(self, directory: str | Path = ".") -> None:
        """Write resource metadata without secret values."""
        directory = Path(directory)
        data = asdict(self)
        (directory / "stack_outputs.json").write_text(
            json.dumps(data, indent=2) + "\n"
        )
        (directory / "stack_outputs.env").write_text(
            "\n".join(
                f"{key.upper()}={value}" for key, value in data.items()
            ) + "\n"
        )

    @classmethod
    def load(cls, directory: str | Path = ".") -> "StackOutputs":
        """Read saved resource metadata."""
        path = Path(directory) / "stack_outputs.json"
        return cls(**json.loads(path.read_text()))

    @staticmethod
    def remove(directory: str | Path = ".") -> None:
        """Remove local outputs after successful teardown."""
        for name in ("stack_outputs.json", "stack_outputs.env"):
            (Path(directory) / name).unlink(missing_ok=True)
