"""Allow ``python -m log_analyzer`` to invoke the CLI."""

from log_analyzer.cli import main

raise SystemExit(main())
