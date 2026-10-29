# Runbook: headless companion (DEV/TEST)

A Linux stand-in for the Windows companion. It uses the real C# core and a SIMULATED OS. It does not replace Windows testing.

```bash
docker compose --profile dev-mock up -d --build companion-headless
curl -s 127.0.0.1:8410/health                        # {"status":"ok","enrolled":false}
K='X-Control-Key: dev-only-control-key-not-a-secret' # override with COMPANION_CONTROL_KEY
```

1. **Enroll.** In the console go to Devices → Add a Windows PC, or `POST /api/v1/devices/enrollment-codes`. Then run
   `curl -s -H "$K" -H 'content-type: application/json' -d '{"code":"ABCD-EFGH-JKLM"}' 127.0.0.1:8410/enroll`.
   Alternatively, set `COMPANION_ENROLL_CODE` before `up`. The device then shows as `HEADLESS-SIM-01`, Online.
2. **Watch.** `curl -s -H "$K" 127.0.0.1:8410/status` shows the counter, polling state, pending consents, active sessions, simulated actions, and the redacted runtime log.
3. **Consent (the person at the PC).** Run `POST /consent/{session_id}` with `{"accept": true|false}`. Unanswered requests are declined at the runtime's consent timeout.
4. **STOP.** Run `POST /stop/{session_id}`. This is the same path as the tray banner's Stop button (`AgentRuntime.StopFromDeviceAsync`).
5. **Offline.** Run `POST /network` with `{"online": false}`. Use `true` to resume.
6. **Reset.** Run `POST /forget` to drop the local enrollment. Revoke the device in the console as well. Run `docker compose rm -sfv companion-headless` and remove the `companion-data` volume for a clean slate.

## Tests
```bash
cd apps/windows-agent
dotnet test tests/JoltIQ.Agent.Core.Tests            # xUnit, Linux OK (needs ASP.NET Core 8 runtime)
dotnet run --project tests/JoltIQ.Agent.Core.SelfTest # dependency-free runner (also wrapped by xUnit)
(cd ../api && python ../../scripts/gen_companion_vectors.py)  # regenerate vectors after a protocol change
```

BLOCKED without Windows: tray/UIA, DPAPI, registry and service actions, MSI, and signing. See ADR 0018.

