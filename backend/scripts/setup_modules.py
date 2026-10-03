"""Review/install fixed local features using the same service as Settings.

--root is an operator CLI setting; the browser never accepts a destination.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.module_catalog import configured_environment, MODULE_IDS, plan
from app.module_contracts import ModuleInstallRequest, ModulePlanRequest
from app.module_jobs import ModuleJobService, ModuleSetupError


async def run(request: ModulePlanRequest, *, install: bool) -> int:
    environment = configured_environment()
    reviewed = await plan(request, environment)
    print(reviewed.model_dump_json(indent=2), flush=True)
    if not install:
        return 0
    service = ModuleJobService(environment)
    await service.recover()
    try:
        created = await service.create(ModuleInstallRequest(features=request.features,
            download_models=request.download_models, plan_token=reviewed.plan_token))
        previous = ''
        while True:
            current = service.get(created.id)
            output = current.model_dump_json()
            if output != previous:
                print(output, flush=True)
                previous = output
            if current.state not in ('queued', 'running'):
                return 0 if current.state == 'completed' else 2
            await asyncio.sleep(0.25)
    finally:
        await service.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--feature', action='append', choices=MODULE_IDS, required=True)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--download-models', action='store_true')
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    root: object = args.root
    features: object = args.feature
    download: object = args.download_models
    install: object = args.install
    if isinstance(root, Path):
        if not root.is_absolute():
            parser.error('--root must be an absolute path')
        os.environ['OPENFABRIC_MODULE_ROOT'] = str(root)
    if not isinstance(features, list) or not isinstance(download, bool) or not isinstance(install, bool):
        return 2
    try:
        request = ModulePlanRequest.model_validate({'features': features, 'download_models': download})
        return asyncio.run(run(request, install=install))
    except ModuleSetupError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('Setup interrupted; owned workers drained. Review the saved job before resuming.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
