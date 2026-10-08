# WSL memory coordination

WSL workspaces share the Linux VM's memory allowance. Check the Windows user's
`.wslconfig` and Linux `/proc/meminfo` before choosing warning and critical
thresholds. Docker Desktop may consume the same WSL VM memory budget, even when
its containers do not appear in the process totals of the current distribution.
Use Docker statistics as a separate view; do not add them blindly to process
RSS totals, which can count shared pages more than once.

On WSL only, memgate flags a sufficiently old, heavy process whose parent is
`/init`, except for managed service cgroups. This is a review candidate: verify
that the process has lost its owning workload before stopping it. On ordinary
Linux, an `init` parent does not trigger this WSL-specific rule.

An independent OOM watchdog such as earlyoom may be useful. Its thresholds are
separate from memgate's launch gates. Some WSL deployments choose earlyoom's
`-s 100,100` option to avoid waiting for swap depletion; inspect the installed
version's help and the VM's swap policy before selecting it. memgate neither
installs nor configures a watchdog and never reports a fixed watchdog trigger
in MB. Choose memgate's critical floor with enough margin for the actual
watchdog configuration and each workload's peak usage.

Set protected ports, repository names, and service cgroup names explicitly for
services that must survive cleanup. Exclude retained Docker container or compose
project names with `docker_exclude`, and use `ignore_patterns` for other known
candidates. An explicit `oom_score_adj` is optional and requires the appropriate
permissions; omission leaves OOM scores unchanged.
