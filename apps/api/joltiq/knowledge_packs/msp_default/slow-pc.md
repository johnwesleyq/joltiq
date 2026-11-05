# [DEFAULT] Slow PC
> DEMO/DEFAULT content shipped with JoltIQ (msp-default pack v1). Generic Windows/MSP guidance — replace or extend it with your own company procedures.

## Triage (all read-only)
1. **system.info** — uptime (more than 7 days without restart is common cause), RAM.
2. **disk.usage** — C: under 10% free slows everything.
3. **process.list** — programs not responding or many browser windows.
4. **disk.health** — anything other than Healthy → back up and escalate (hardware).
5. **eventlog.recent_errors** System — disk or driver errors.
6. **update.status** — reboot pending?

## Fixes
1. **disk.cleanup_temp** (files older than 7 days in the user's temp folder; not reversible).
2. **app.uninstall** unwanted startup programs the customer names.
3. **system.restart** LAST, with a 2-minute warning, if reboot is pending or uptime is long.

## Escalate when
Disk health is not Healthy, RAM is under 8 GB for the workload, or malware is suspected (run the MSP's security tool; never "clean" with random downloads).
