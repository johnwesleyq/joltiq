export const metadata = { title: "How it works" };

const FLOW = [
  ["Intake", "Tickets arrive from the portal chat, email, your PSA or the API. JoltIQ triages them and asks diagnostic questions when the description is thin."],
  ["Internal search", "Hybrid keyword and vector search over your runbooks, SOPs, manuals, call transcripts and past tickets, filtered by who's allowed to see what. Every answer carries citations down to the page and section."],
  ["Live research", "When your own knowledge runs out, JoltIQ searches vendor documentation and permitted forums with a privacy-redacted query. It separates vendor-confirmed evidence from forum hypotheses. Instructions or scripts on web pages are flagged and never run."],
  ["Typed plan", "The fix is a list of typed actions from a fixed catalog, each with a postcondition. Your policy engine, not the model, assigns the risk."],
  ["Consent and approval", "The customer agrees to a scoped session: these domains or this device, up to this risk, for this long. Sensitive steps need an explicit approval bound to the exact step."],
  ["Execution", "Browser work runs in an isolated Chromium session limited to the approved domains. Desktop work goes to the Windows companion, which re-checks every request against its own allowlist."],
  ["Independent verification", "A separate check observes the result: reloading the page from the server, reading the registry value, asking whether the service is running. The outcome is Verified, Failed, Partial or Unknown, and Unknown is never treated as success."],
  ["Escalation and learning", "Failures retry within limits, then go to a technician with the full trail. Verified fixes become draft knowledge articles that a person reviews before anything is reused."],
];

export default function Features() {
  return <main className="mx-auto max-w-[860px] px-6 py-10">
    <h1 className="text-4xl font-bold">How a ticket gets resolved</h1>
    <p className="mt-3 max-w-[60ch] text-lg text-slate">This is the same path every time. Each stage is recorded on the ticket so a technician can see what happened and why.</p>
    <ol className="mt-10 space-y-8">
      {FLOW.map(([t, d], i) => <li key={t} className="grid grid-cols-[2.5rem_1fr] gap-4">
        <span className="font-display text-2xl font-bold text-slate">{i + 1}</span>
        <div><h2 className="text-xl font-bold">{t}</h2><p className="mt-1 text-slate">{d}</p></div>
      </li>)}
    </ol>
  </main>;
}
