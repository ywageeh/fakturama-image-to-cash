"""Usage:
    python -m i2c extract <image>   # image -> validated JSON, no UI
    python -m i2c run <image>       # full Order -> Invoice flow in Fakturama
    python -m i2c run <file.json>   # same, from a saved extracted.json (no LLM call)
Exit codes: 0 ok, 2 manual review needed.
"""

import argparse
import sys
import traceback
from datetime import datetime
from pathlib import Path

from .config import Config
from .errors import ManualReview
from .extract import extract
from .llm import LLM
from .models import OrderData
from .rules import check_order


def main() -> None:
    p = argparse.ArgumentParser(prog="i2c")
    p.add_argument("command", choices=["extract", "run"])
    p.add_argument("image", type=Path)
    args = p.parse_args()

    run_dir = Path("runs") / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    app = None
    code = 0
    try:
        if args.image.suffix == ".json":  # replay a saved extraction, skipping the LLM
            order = OrderData.model_validate_json(args.image.read_text(encoding="utf-8"))
            check_order(order)
        else:
            order = extract(args.image, LLM(Config.from_env()))
        (run_dir / "extracted.json").write_text(order.model_dump_json(indent=2), encoding="utf-8")
        print(order.model_dump_json(indent=2))
        if args.command == "run":
            from .flow import run  # UI deps only needed here
            from .ui.app import Fakturama

            app = Fakturama(run_dir)
            run(app, order)
            print(f"Done. Evidence in {run_dir}")
    except Exception as e:
        if not isinstance(e, ManualReview):  # unexpected error: still stop cleanly with evidence
            (run_dir / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
            e = ManualReview("unexpected", f"{type(e).__name__}: {e}")
        if app is not None:
            app.step(f"MANUAL REVIEW NEEDED: {e}")
            app.screenshot(f"manual_review_{e.step}")
        (run_dir / "manual_review.txt").write_text(str(e), encoding="utf-8")
        print(f"MANUAL REVIEW NEEDED {e}\nEvidence in {run_dir}", file=sys.stderr)
        code = 2
    sys.exit(code)
