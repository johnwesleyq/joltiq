# [DEFAULT] VPN won't connect
> DEMO/DEFAULT content shipped with JoltIQ (msp-default pack v1). Generic Windows/MSP guidance — replace or extend it with your own company procedures.

## Triage
1. Is the internet working without VPN? **network.diagnostics**, **network.ping** 8.8.8.8.
2. **network.proxy_check** — a leftover proxy/PAC breaks many VPN clients.
3. **network.traceroute** the VPN gateway host the MSP gives you.
4. Hotel/guest Wi-Fi often blocks VPN ports: try a phone hotspot.
5. Clock wrong → certificate errors: **service.restart** W32Time.

## Fixes
1. **network.flush_dns**, then reconnect.
2. **network.renew_ip** when on a new network.
3. Reinstall the VPN client only from the vendor/MSP-approved source (technician).

## Never
Never ask for or type the user's VPN password or MFA code; the user enters it.
