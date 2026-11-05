# [DEFAULT] Printer shows "Offline" or jobs are stuck
> DEMO/DEFAULT content shipped with JoltIQ (msp-default pack v1). Generic Windows/MSP guidance — replace or extend it with your own company procedures.

## Triage
1. JoltIQ: **printer.list** — is the printer installed, which driver and port, is it the default?
2. Check the printer itself: powered on, no paper jam, same network (Wi-Fi printers often fall off after router restarts).
3. JoltIQ: **network.ping** the printer's IP (from its port name, e.g. IP_192.168.1.50).

## Fixes
1. **printer.clear_queue** — stops the spooler, deletes waiting jobs, starts it again (HIGH RISK: all queued jobs are lost; re-print after).
2. **service.restart** Spooler — when the queue is empty but nothing prints.
3. **printer.set_default** — when jobs go to the wrong printer (e.g. "Microsoft Print to PDF").
4. **driver.install** — only from HP/Canon/Epson/Brother official sites, signature-checked.

## Escalate when
The printer's IP changed (needs a new port), it is a shared/print-server queue, or hardware errors show on the printer panel.
