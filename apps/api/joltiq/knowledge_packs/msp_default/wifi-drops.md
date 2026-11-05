# [DEFAULT] Wi-Fi keeps dropping
> DEMO/DEFAULT content shipped with JoltIQ (msp-default pack v1). Generic Windows/MSP guidance — replace or extend it with your own company procedures.

## Symptoms
Wi-Fi disconnects every few minutes, "No internet, secured", slow or stalling pages, Teams/Zoom calls freezing.

## Triage (read-only first)
1. JoltIQ: **network.wifi_report** — signal %, radio type, channel, receive/transmit rate. Signal under 50% or link rate under 50 Mbps points to distance/interference.
2. JoltIQ: **network.diagnostics** — is the gateway reachable and does DNS resolve?
3. JoltIQ: **network.ping** 8.8.8.8 (and the gateway) — packet loss above 2% on Wi-Fi is a radio problem; loss only past the gateway is the ISP.
4. Ask: does it happen to other devices on the same Wi-Fi? Only one PC → PC side. Everyone → router/ISP.

## Fixes (in order, each needs the customer's Approve + Allow)
1. **network.flush_dns** when pages fail but ping by IP works.
2. **network.renew_ip** when the PC has a 169.254.x.x address or "No internet".
3. **network.adapter_reset** ("Wi-Fi") when the adapter is stuck; the connection drops ~5 seconds.
4. Move closer to the access point / use 5 GHz; power-cycle the router (customer does this).
5. Update the Wi-Fi driver from the PC maker (technician task).

## Escalate when
Several users affected, the router is managed by the MSP, or loss persists after the steps above.
