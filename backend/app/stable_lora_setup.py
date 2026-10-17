"""Review selected Stable Audio assets using the existing managed setup lifecycle."""
from __future__ import annotations

import hashlib
import json
from typing import Literal, TYPE_CHECKING

from .module_contracts import ModulePlan, ModulePlanRequest, ModulePlanStep
from .module_licenses import declarations
from .module_music_models import (_path, _preflight, _valid_file, setup_stable_generation_models,
    setup_stable_training_models, stable_generation_artifacts, stable_training_artifacts,
    setup_stable_conditioning_models, stable_conditioning_artifacts)
from .stable_lora_contracts import StableLoraVariant
from .stable_lora_runtime import status

if TYPE_CHECKING:
    from .module_catalog import ModuleEnvironment
    from .module_jobs import InstallContext, InstallOutcome


def plan(request: ModulePlanRequest, environment: ModuleEnvironment) -> ModulePlan:
    from .module_catalog import approval_scope, available_bytes
    from .module_jobs import ModuleSetupError
    conditioning = request.stable_conditioning_variant is not None
    generation = request.stable_generation_variant is not None or conditioning
    variant = request.stable_conditioning_variant or request.stable_generation_variant or request.stable_training_variant
    if variant is None:
        raise ModuleSetupError('plan_changed')
    if generation:
        from .local_music_runtime import engine_status
        generation_status = next(engine for engine in engine_status(environment).engines if engine.id == 'stable_audio3')
        supported, installed = generation_status.supported, generation_status.installed
    else:
        training_status = status(environment)
        supported, installed = training_status.supported, training_status.installed
    artifacts = stable_conditioning_artifacts(variant) if conditioning else stable_generation_artifacts(variant) if generation else stable_training_artifacts(variant)
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
    purpose = 'audio reference' if conditioning else 'generation' if generation else 'training'
    components = ('generation model, matching audio decoder, reference encoder and text encoder' if conditioning
        else 'generation model, matching audio decoder and text encoder' if generation else 'training base, matching audio encoder and text encoder')
    operation: Literal['unsupported', 'manual', 'verify', 'reuse'] = 'unsupported' if not supported else 'manual' if not installed else 'verify' if missing else 'reuse'
    detail = ('Prepare the Stable Audio 3 module first; a verified native ' + purpose + ' environment is required.' if not installed
        else 'Existing ' + components + ' passed structural checks.' if not missing
        else 'Reuse the installed runtime and valid weights. Download and verify only the missing ' + components + '.')
    licenses = declarations('stable_audio3')
    if conditioning:
        selected_hashes = {item.sha256 for item in artifacts}
        licenses = [item.model_copy(update={'identities': [identity for identity in item.identities
            if identity.sha256 is None or identity.sha256 in selected_hashes]}) for item in licenses]
    step = ModulePlanStep(module_id='stable_audio3', name='Stable Audio 3 ' + label + ' ' + purpose + ' assets',
        operation=operation, estimated_download_bytes=size, detail=detail, actions=[], licenses=licenses)
    warnings = ['Asset preparation preserves existing source, environments, inference models, datasets and checkpoints.',
        'Stable Audio weights use the Stability AI Community License; T5Gemma uses Gemma Terms of Use. Review the linked terms before downloading.',
        'Asset checks do not establish runtime compatibility, adapter quality or generated audio quality.']
    if not enough:
        warnings.append('Insufficient or unverified free space in the model cache or installation location. Required space includes cached downloads and installed copies.')
    if not installed:
        warnings.append(detail)
    canonical = {'scope': approval_scope(request, environment), 'steps': [step.model_dump(mode='json')]}
    token = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return ModulePlan(features=request.features, download_models=True, stable_training_variant=request.stable_training_variant,
        stable_generation_variant=request.stable_generation_variant, stable_conditioning_variant=request.stable_conditioning_variant, plan_token=token,
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


async def prepare_generation(context: InstallContext, variant: StableLoraVariant) -> InstallOutcome:
    from .local_music_runtime import engine_status
    from .module_jobs import ModuleSetupError
    capability = next(engine for engine in engine_status(context.environment).engines if engine.id == 'stable_audio3')
    if not capability.supported:
        raise ModuleSetupError('unsupported_platform')
    if not capability.installed:
        raise ModuleSetupError('environment_unverified')
    return await setup_stable_generation_models(context, variant)


async def prepare_conditioning(context: InstallContext, variant: StableLoraVariant) -> InstallOutcome:
    from .local_music_runtime import engine_status
    from .module_jobs import ModuleSetupError
    capability = next(engine for engine in engine_status(context.environment).engines if engine.id == 'stable_audio3')
    if not capability.supported:
        raise ModuleSetupError('unsupported_platform')
    if not capability.installed:
        raise ModuleSetupError('environment_unverified')
    return await setup_stable_conditioning_models(context, variant)
