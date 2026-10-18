import Link from "next/link";
import Trace from "./Trace";

export default function Home() {
  return <main>
    <section className="mx-auto grid max-w-[1120px] items-center gap-12 px-6 pb-16 pt-10 lg:grid-cols-[1fr_1.05fr]">
      <div>
        <h1 className="max-w-[14ch] text-[clamp(2.4rem,5vw,3.9rem)] font-bold">A support technician that checks its own work.</h1>
        <p className="mt-5 max-w-[52ch] text-lg text-slate">
          JoltIQ picks up a ticket, asks the right questions, searches your runbooks and vendor docs, and proposes a fix.
          It asks before touching anything, then confirms the result through a separate check. If it can&apos;t prove the fix
          worked, it hands the ticket to a person instead of closing it.
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <Link href="/register" className="btn btn-primary">Start a 14-day trial</Link>
          <Link href="/features" className="btn btn-ghost">See how a ticket is resolved</Link>
        </div>
      </div>
      <Trace />
    </section>

    <section className="border-y border-line bg-paper">
      <div className="mx-auto grid max-w-[1120px] gap-10 px-6 py-14 md:grid-cols-3">
        {[
          ["Clicking isn't resolving", "Every change has a postcondition. After a settings change, JoltIQ reloads the page from the server and reads the value back. After a restart, it asks the machine whether the app is responding."],
          ["Nothing runs without a yes", "Risk comes from your policy, not the model. Sensitive steps wait for the customer or a technician to approve that exact step. Change the step and the approval is void."],
          ["People stay in control", "Customers see a live view and can take over or hit stop at any time. Sign-ins and MFA codes are typed by the person, never seen by the AI."],
        ].map(([t, d]) => <div key={t}><h2 className="text-xl font-bold">{t}</h2><p className="mt-2 text-slate">{d}</p></div>)}
      </div>
    </section>

    <section className="mx-auto max-w-[1120px] px-6 py-16">
      <h2 className="max-w-[24ch] text-3xl font-bold">Built for the people answering the phones</h2>
      <div className="mt-8 grid gap-x-12 gap-y-6 md:grid-cols-2">
        {[
          ["Managed service providers", "Separate tenants per client, white-label portals, and PSA sync with Freshdesk today. More connectors are documented as they're built."],
          ["Software support teams", "Turn known fixes into approved procedures and let the assistant handle the repeat tickets inside your customers' web apps."],
          ["Internal IT", "A Windows companion for diagnostics and allowlisted fixes: add-ins, app restarts, the print spooler. It shows a visible banner and a kill switch."],
          ["HVAC, plumbing and field offices", "Help for the dispatch software, QuickBooks and the office printer, without waiting for the IT person to drive over."],
        ].map(([t, d]) => <div key={t} className="border-l-2 border-ink pl-4"><h3 className="text-lg font-bold">{t}</h3><p className="mt-1 text-slate">{d}</p></div>)}
      </div>
    </section>
  </main>;
}
