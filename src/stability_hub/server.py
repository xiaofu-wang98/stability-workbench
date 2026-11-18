"""Local pilot launcher; enterprise identity remains an IT adapter responsibility."""

import argparse
import os
from pathlib import Path

import uvicorn

from .api import create_app
from .models import ProjectConfig
from .permissions import Role, actor_for_role
from .storage import Store


def main():
    parser = argparse.ArgumentParser(description="Run the V4 backend on localhost")
    parser.add_argument("--home", type=Path, default=Path("data/v4-pilot"))
    parser.add_argument("--config", type=Path, action="append", required=True)
    parser.add_argument("--actor-id", required=True)
    parser.add_argument("--actor-name", required=True)
    parser.add_argument("--role", choices=[r.value for r in Role], default=Role.TECHNICIAN.value)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--frontend", type=Path)
    parser.add_argument("--allow-origin", action="append", default=[])
    args = parser.parse_args()
    configs = [ProjectConfig.model_validate_json(path.read_text(encoding="utf-8-sig")) for path in args.config]
    app = create_app(Store(args.home), configs, actor_for_role(args.actor_id, args.actor_name, Role(args.role)),
                     api_token=os.environ.get("STABILITY_HUB_API_TOKEN"), frontend=args.frontend, origins=args.allow_origin,
                     enforce_formal_sample_binding=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
