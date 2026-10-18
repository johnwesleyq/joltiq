export const metadata = { title: "Security" };

const ITEMS = [
  ["Tenant isolation", "Every customer's data is separated at two levels. The application scopes each query to the organization, and PostgreSQL row-level security refuses rows from any other tenant, even if application code makes a mistake."],
  ["Permission before action", "Browser and desktop work needs a scoped, time-limited consent from the person whose system it is. Your policy decides which steps need approval. Approvals are cryptographically bound to the exact step and expire."],
  ["The AI never sees your passwords", "When an application asks someone to sign in, the assistant pauses. The person types directly into the isolated session. Password and code fields are masked in screenshots and never reach the model or the logs."],
  ["Web content is untrusted", "Research results are screened for instruction-like text and shell commands. Nothing from the web is executed. Fixes found online are escalated to a technician unless a person turns them into an approved procedure."],
  ["Windows companion", "Runs as the signed-in user with a visible banner and a stop button. Its device-bound key is protected by Windows DPAPI. Every request is signed and replay-protected, and the companion re-checks every action against its own allowlist. There is no remote shell."],
  ["Audit trail", "Actions, approvals and verifications are written to an append-only log with a hash chain, so tampering is detectable. You can export it at any time."],
];

export default function Security() {
  return <main className="mx-auto max-w-[860px] px-6 py-10">
    <h1 className="text-4xl font-bold">Security</h1>
    <p className="mt-3 max-w-[62ch] text-lg text-slate">JoltIQ operates inside your customers&apos; systems, so it is built to be safe by default.</p>
    <div className="mt-10 space-y-8">{ITEMS.map(([t, d]) => <section key={t}><h2 className="text-xl font-bold">{t}</h2><p className="mt-1 text-slate">{d}</p></section>)}</div>
    <p className="mt-12 rounded-md bg-paper p-4 text-sm text-slate">JoltIQ does not currently hold SOC 2, ISO 27001 or HIPAA certifications. Contact us for our security questionnaire and architecture documentation.</p>
  </main>;
}
