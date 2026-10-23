# JoltIQ Windows companion

The companion is the enrolled user's local JoltIQ entry point and the guarded executor for approved Windows support work. It does not open a listening port; it makes signed outbound requests to the configured JoltIQ server.

## Ask JoltIQ from the tray

After enrollment, open the JoltIQ tray menu and choose **Ask JoltIQ…**. Describe the problem in ordinary language, for example:

> Outlook cannot find Adobe Sign.

The first Send creates one internal ticket and one AI run linked to the authenticated user and this exact device. The window shows the conversation, current state and ticket number, and can open the ticket in the portal. Later messages continue the same run. If the network is unavailable, the unsent text stays in the compose box; retrying the first message reuses its request ID so it cannot create a duplicate ticket.

The conversation itself does not share the screen and does not give JoltIQ keyboard or mouse control. If JoltIQ needs diagnostics, Guide mode or Fix mode, it requests the existing session and device-local consent separately. The user can still decline or press STOP.

What happens after a request depends on the company's published procedures and policy:

- JoltIQ can answer, ask follow-up questions, or escalate any request.
- It can guide a user or execute a fix only through an approved, typed workflow with the required consent and verification.
- A remembered answer or newly uploaded knowledge item cannot by itself become an executable computer action.
- Unsupported or uncertain diagnoses go to a person instead of becoming guessed actions.

## Developer verification

On Windows with the .NET 8 SDK and Windows Desktop runtime:

```powershell
dotnet test tests\JoltIQ.Agent.Tests\JoltIQ.Agent.Tests.csproj -v:minimal
dotnet build src\JoltIQ.Agent.Tray\JoltIQ.Agent.Tray.csproj -c Release
```

Native compilation and unit tests are necessary but do not approve a public release. Use the signed MSI and disposable Windows 10/11 acceptance matrix in `docs/operations/windows-general-computer-use-acceptance.md` before unrestricted deployment.
