# Native process ownership

Every `ManagedProcess` launches a supervisor with a backend-owned stdin pipe.
The engine inherits log output, environment and its configured working
directory, while its stdin is disconnected from the liveness pipe. Closing
the backend process therefore signals EOF even after a crash. No process is
killed merely because it occupies an expected network port.

On POSIX the supervisor and normal child processes share a new session. EOF
kills that process group. When the immediate launcher exits, the supervisor
records its exit code atomically and kills remaining group members before
exiting. The parent waits for group disappearance before freeing ownership.
A random launch token and bounded receipt bind the PID and exit information;
unverified receipt data is ignored. These guarantees assume engine children
remain in the owned session; intentional daemonization needs separate review.

On Windows the parent creates a named Job Object with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, retains its query handle, and launches a
small supervisor. The supervisor assigns itself before it creates an engine
child, so descendants inherit job membership without a Popen-assignment race.
Breakaway flags are disabled. EOF or launcher exit terminates the whole job.
The parent releases its handle only after the supervisor exits and job
accounting reports zero active processes. An assignment, termination or
accounting failure remains an ownership failure; a launcher exit alone is
insufficient evidence.

Reference and video tools use that same Job Object boundary through
`video_process.spawn_owned`. The backend creates the job before starting the
wrapper, and the wrapper joins before launching a tool. Tool stdin is
disconnected from the backend liveness pipe. The wrapper persists its named
job in the atomic worker receipt before spawning a child, then terminates the
job on pipe EOF or immediate-child exit. The backend retains the process and
query handle through pipe draining and zero-active-process accounting, even
when the wrapper has already exited. Concurrent cleanup callers share one
drain; cancellation during launch or drain cannot release ownership early.

Recovery verifies the PID/token receipt and exact named job. A missing kernel
job (`ERROR_FILE_NOT_FOUND`) is accepted as absent; access denial, failed
accounting and timeout retain ownership and block replacement work. Windows
receipts created before job supervision cannot establish this guarantee and
remain unverified. POSIX worker receipts remain compatible, and tool cleanup
continues to kill the owned session group. The POSIX liveness watcher uses an
unbuffered pipe so normal wrapper shutdown cannot abort Python while a daemon
holds a buffered stdin lock.

The implementation follows Microsoft's [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects),
[assignment](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject)
and [termination](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-terminatejobobject)
contracts. Nested jobs require Windows 8 or later; restrictions inherited from
an enclosing job can cause assignment to fail. That failure prevents the
native child from spawning.

Actual macOS subprocess tests cover parent EOF and a launcher exiting with a
live descendant. Platform-independent tests cover Windows startup order,
checked kernel results, EOF, assignment failure and nonempty-job teardown.
Windows kernel tests additionally cover tool-wrapper exit, parent pipe EOF
and a hard backend crash with tool descendants. They are included in the
existing `native-process-windows` CI job, skipped off Windows, and must run on
Windows before claiming platform validation. These checks use temporary logs,
short Python fixtures and no model weights.
