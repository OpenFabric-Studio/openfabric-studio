"""Review training-only assets using the existing managed setup lifecycle."""
from __future__ import annotations

import hashlib
import json
from typing import Literal, TYPE_CHECKING

from .module_contracts import ModulePlan, ModulePlanRequest, ModulePlanStep
from .module_licenses import declarations
from .module_music_models import _path, _preflight, _valid_file, setup_stable_training_models, stable_training_artifacts
from .stable_lora_contracts import StableLoraVariant
from .stable_lora_runtime import status

if TYPE_CHECKING:
    from .module_catalog import ModuleEnvironment
    from .module_jobs import InstallContext, InstallOutcome


def plan(request: ModulePlanRequest, environment: ModuleEnvironment) -> ModulePlan:
    from .module_catalog import approval_scope, available_bytes
    from .module_jobs import ModuleSetupError
    variant = request.stable_training_variant
    if variant is None:
        raise ModuleSetupError('plan_changed')
    capability = status(environment)
    artifacts = stable_training_artifacts(variant)
    root = environment.paths['stable_audio3']
    _preflight(root, [item.path for item in artifacts])
    missing = [item for item in artifacts if not _valid_file(_path(root, item.path))]
    size = sum(item.bytes for item in missing)
    required = 2 * size
    destinations = {environment.root, *(_path(root, item.path).parent for item in missing)}
    spaces = [available_bytes(path) for path in destinations]
    free = min(space for space in spaces if space is not None) if all(space is not None for space in spaces) else None
    enough = free is not None and free >= required
    label = 'Medium' if variant == 'medium' else 'Small Music'
    operation: Literal['unsupported', 'manual', 'verify', 'reuse'] = 'unsupported' if not capability.supported else 'manual' if not capability.installed else 'verify' if missing else 'reuse'
    detail = ('Prepare the Stable Audio 3 module first; a verified native training environment is required.' if not capability.installed
        else 'Existing training base, matching audio encoder and text encoder passed structural checks.' if not missing
        else 'Reuse the installed trainer and valid weights. Download and verify only the missing training base, matching audio encoder and text encoder.')
    step = ModulePlanStep(module_id='stable_audio3', name='Stable Audio 3 ' + label + ' training assets',
        operation=operation, estimated_download_bytes=size, detail=detail, actions=[], licenses=declarations('stable_audio3'))
    warnings = ['Training preparation preserves existing source, environments, inference models, datasets and checkpoints.',
        'Stable Audio weights use the Stability AI Community License; T5Gemma uses Gemma Terms of Use. Review the linked terms before downloading.',
        'Training asset checks do not establish training compatibility, adapter quality or generated audio quality.']
    if not enough:
        warnings.append('Insufficient or unverified free space in the model cache or installation location. Required space includes cached downloads and installed copies.')
    if not capability.installed:
        warnings.append(detail)
    canonical = {'scope': approval_scope(request, environment), 'steps': [step.model_dump(mode='json')]}
    token = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return ModulePlan(features=request.features, download_models=True, stable_training_variant=variant, plan_token=token,
        steps=[step], estimated_download_bytes=size, download_size_unknown=False, required_free_bytes=required,
        free_bytes=free, can_install=enough and operation == 'verify', warnings=warnings)


async def prepare(context: InstallContext, variant: StableLoraVariant) -> InstallOutcome:
    from .module_jobs import ModuleSetupError
    capability = status(context.environment)
    if not capability.supported:
        raise ModuleSetupError('unsupported_platform')
    if not capability.installed:
        raise ModuleSetupError('environment_unverified')
    return await setup_stable_training_models(context, variant)
