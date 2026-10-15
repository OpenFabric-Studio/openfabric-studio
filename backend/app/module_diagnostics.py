"""Allowlisted setup failures; process output and exceptions stay in local logs."""
from __future__ import annotations

from pydantic import TypeAdapter, ValidationError

from .module_contracts import ModuleErrorCode, ModuleInstallJob, ModuleJobStep, ModuleSetupEvent, ModuleSetupFailure

_code: TypeAdapter[ModuleErrorCode] = TypeAdapter(ModuleErrorCode)
_RETRY = 'Retry setup to recheck saved work and finish the missing components. Existing files are preserved.'
_MESSAGES: dict[ModuleErrorCode, tuple[str, str, bool]] = {
    'already_installed': ('The selected components are already installed.', 'Choose only missing components, or explicitly select dependency reinstallation for a supported managed engine.', False),
    'reinstall_unavailable': ('Dependency reinstallation is unavailable for this installation.', 'External checkouts and system/native tools are preserved. Review their setup details outside the dependency reinstall flow.', False),
    'artifact_integrity': ('A model or installer file did not pass verification.', _RETRY, True),
    'installer_failed': ('The installer could not finish this step.', _RETRY, True),
    'download_failed': ('A required download could not be completed.', 'Check your connection, then retry setup. Verified downloads and completed components are reused.', True),
    'download_stalled': ('A required download stopped responding.', 'Check your connection, then retry setup. Saved download data can be reused.', True),
    'installer_timeout': ('This installation step exceeded its time limit.', _RETRY, True),
    'insufficient_disk': ('An installation location has insufficient free space.', 'Free space in the cache or model location, then review the setup plan again. Existing models are preserved.', False),
    'installation_conflict': ('Existing files conflict with the requested installation.', 'The existing files were preserved. Review the installation location before preparing a new setup plan.', False),
    'environment_unverified': ('The isolated environment did not pass its compatibility checks.', _RETRY, True),
    'unsupported_platform': ('This setup is unavailable on this computer.', 'Choose a supported feature or computer. No unsupported installation was attempted.', False),
    'unsafe_install_path': ('The installation location could not be used safely.', 'Review the configured location and any symbolic links. Existing files were preserved.', False),
    'archive_unsafe': ('The installer archive could not be extracted safely.', 'Do not install this archive manually. Review the error code with support before trying again.', False),
    'worker_unverified': ('A previous installer could not be verified as stopped.', 'Wait for the existing process to finish or restart the app before reviewing setup again.', False),
    'not_managed': ('This installation is managed outside the app.', 'Review its configured location before preparing a managed setup.', False),
    'plan_changed': ('Setup requirements changed after review.', 'Review a new setup plan before installing additional files.', False),
    'installer_output_limit': ('The installer exceeded its diagnostic output limit.', 'Review this error code with support before retrying setup.', False),
}


def failure_for(code: str | None, *, phase: str | None = None) -> ModuleSetupFailure:
    try:
        parsed = _code.validate_python(code)
    except ValidationError:
        parsed = 'installation_failed'
    summary, recovery, retryable = _MESSAGES.get(parsed, ('Setup could not finish this step.', _RETRY, True))
    return ModuleSetupFailure(code=parsed, summary=summary, recovery=recovery, retryable=retryable,
        phase=phase[:1000] if phase else None)


def append_event(step: ModuleJobStep, detail: str, at: str) -> None:
    bounded = detail[:500]
    if bounded and (not step.events or step.events[-1].detail != bounded):
        step.events = [*step.events[-39:], ModuleSetupEvent(at=at, detail=bounded)]


def public_job(job: ModuleInstallJob) -> ModuleInstallJob:
    """Older jobs get useful messages without inventing or rewriting saved history."""
    result = job.model_copy(deep=True)
    if result.state == 'failed':
        result.error_code = failure_for(result.error_code).code
        index = result.current_step
        if result.error_code == 'worker_unverified' and index is not None and index < len(result.steps):
            step = result.steps[index]
            step.state = 'failed'
            step.error_code = 'worker_unverified'
            if step.failure is None or step.failure.code != 'worker_unverified':
                step.failure = failure_for('worker_unverified')
        for step in result.steps:
            if step.state == 'failed':
                step.failure = step.failure or failure_for(step.error_code or result.error_code)
                step.error_code = step.failure.code
    return result
