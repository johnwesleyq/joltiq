# [DEFAULT] Blue screen (BSOD) basics
> DEMO/DEFAULT content shipped with JoltIQ (msp-default pack v1). Generic Windows/MSP guidance — replace or extend it with your own company procedures.

## Triage
1. Ask for the stop code on the blue screen (e.g. IRQL_NOT_LESS_OR_EQUAL, CRITICAL_PROCESS_DIED) and what was happening (docking, new device, update).
2. **eventlog.recent_errors** System, 72 hours — look for BugCheck (event 1001) and disk/driver errors just before.
3. **update.status** — a recent update or driver?
4. **disk.health** and **disk.usage** — failing disk or full disk.
5. **system.info** — uptime/OS build.

## Fixes
1. Undo the recent change: **app.uninstall** the new program the customer names; roll back a driver (technician).
2. **update.scan** — fixes for known crash bugs often ship as updates.
3. Unplug new USB devices/docks and test.

## Escalate when
Repeated BSODs, disk health warning, MEMORY_MANAGEMENT stop codes (RAM test), or the PC can't boot (needs hands-on/recovery media).
