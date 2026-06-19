from __future__ import annotations

import sys

from pydantic import ValidationError

from signal_copier.config import Config
from signal_copier.infra.log import setup_logging


def main() -> None:
    try:
        config = Config()
    except ValidationError as exc:
        # Demo-only guardrail (FR-6.6) and other validation errors land here.
        sys.stderr.write(f"❌ Config validation failed:\n{exc}\n")
        sys.exit(2)

    setup_logging(config.log_path)  # stub; replaced by M7

    print(
        f"🟢 signal_copier M2 started (config loaded)\n"
        f"   Mode: {'dry_run' if config.dry_run else 'live demo'}\n"
        f"   Timezone: {config.timezone}\n"
        f"   Amounts: initial=${config.amount_initial} "
        f"gale1=${config.amount_gale1} gale2=${config.amount_gale2}\n"
        f"   (state machine + gale math ready; broker/listener/scheduler pending M5+)"
    )


if __name__ == "__main__":
    main()
