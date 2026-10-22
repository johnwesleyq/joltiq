# Windows public-release gates

The machine-readable source of truth is `docs/operations/evidence/windows-public-release-readiness.json`. Validate it with:

```bash
python3 scripts/validate_windows_release_readiness.py docs/operations/evidence/windows-public-release-readiness.json
```

`public_launch_ready` may be `true` only when all four gates below are `passed` and reference their evidence. Engineering tests, a physical-PC build, a self-signed certificate, an internal review, or a partially completed case catalog cannot satisfy a public-release gate.

## Current prerequisite inventory

The authorized physical PC is a Dell Pro Max 16 MC16250 running Windows 11 Pro 64-bit, version 10.0.26200 build 26200. Read-only inspection found no current-user code-signing certificate with private key, no Windows SDK `signtool.exe`, no Hyper-V management command, no VMware `vmrun`, and no Windows Sandbox executable. Windows reports a hypervisor is present, but the optional-feature query required elevation and no registered disposable VM was found. This PC remains an engineering host, not snapshot-reset acceptance infrastructure.

## Gate completion requirements

### Authenticode signing and timestamping

1. Obtain an organization-validated public code-signing identity for the legal publisher. Keep the private key in the CA-required hardware token, HSM, or managed signing service; do not commit or copy it into the repository.
2. Install the Windows SDK signing tools on the protected signing runner.
3. For an exportable CI credential supported by the certificate provider, set the protected `windows-signing` environment secrets `WINDOWS_SIGNING_CERT_BASE64` and `WINDOWS_SIGNING_CERT_PASSWORD`. For a non-exportable token/HSM, adapt the protected runner to expose the certificate in `CurrentUser\My` and pass its thumbprint to `apps/windows-agent/scripts/sign.ps1`.
4. Sign the tray executable and broker executable before packaging, then sign the MSI. Require SHA-256 and a trusted RFC 3161 timestamp, and run `Assert-JoltIqAuthenticodeFile -RequireTimestamp` against all three.
5. Record hashes, signer subject, certificate thumbprint and validity, signature status, and timestamp status. Never record private-key material or passwords.

### Installer lifecycle

Use `apps/windows-agent/scripts/test-on-windows.ps1 -InstallerLifecycle` on a named clean snapshot, with reviewed previous and current MSIs. Evidence must show fresh install, service identity and ACL, tray launch, repair, upgrade, downgrade refusal, uninstall cleanup, and the hashes of both installers.

### Disposable Windows acceptance

Provide separate 64-bit Windows 10 and Windows 11 VMs, each with a documented clean named snapshot that is reverted before every case. Execute the complete general-computer-use and Outlook catalogs under the existing lab procedures, using only test tenants, profiles, credentials, and content. The licensed classic-Outlook case needs a disposable profile and harmless real COM add-in. Only validator-approved aggregates with `passed: true` count.

### Independent security review

An unaffiliated qualified reviewer must assess the exact commit and release artifacts, perform the agreed penetration tests, document findings and retests, and sign a release decision with no unresolved Critical or Important release blocker. An author review or AI review is useful preflight evidence but is not independent approval.
