import Link from "next/link";
import Core from "@/components/Core";
import LiveDemo from "./LiveDemo";

const STEPS = [
  ["Tell it what's wrong", "Customers chat or talk to JoltIQ in plain words. No forms, no ticket numbers to remember."],
  ["It finds the fix", "It searches every ticket and article your team has ever written, then researches anything new."],
  ["You say yes", "It explains exactly what it wants to change and waits for the customer's OK. Passwords stay with the customer."],
  ["It fixes it and proves it", "It takes over the browser or computer, makes the change while the customer watches, then checks it actually worked."],
];
const FOR = [
  ["IT providers (MSPs)", "Clear the password resets, printers, Outlook and VPN tickets before a technician ever sees them."],
  ["Software companies", "Fix customers' setup problems inside their accounts, instead of sending another how-to link."],
  ["Clinics and dental offices", "Front-desk IT help in minutes, with every step recorded for compliance."],
  ["Accounting firms", "QuickBooks, scanners and portals sorted during tax season without hiring."],
];

export default function Home() {
  return <main>
    <section className="mx-auto grid max-w-[1180px] items-center gap-12 px-6 pb-20 pt-8 lg:grid-cols-[0.95fr_1.05fr]">
      <div>
        <h1 className="text-[clamp(2.4rem,5.4vw,4.2rem)] leading-[1.05]">Tell it what&apos;s broken. Watch it fix it.</h1>
        <p className="mt-6 max-w-[52ch] text-lg text-slate">JoltIQ is an AI technician your customers can talk to. It learns from every ticket your team has solved,
          asks permission, then takes over the screen and fixes the problem while they watch. If it can&apos;t prove the fix worked, a person takes over.</p>
        <div className="mt-9 flex flex-wrap gap-3">
          <Link href="/register" className="btn btn-primary px-6 py-3 text-base">Try JoltIQ free</Link>
          <Link href="/features" className="btn btn-ghost px-6 py-3 text-base">See how it works</Link>
        </div>
        <ul className="mt-8 flex flex-wrap gap-x-6 gap-y-2 text-sm text-slate">
          <li>Asks before every change</li><li>Never sees passwords</li><li>Stop it any time</li>
        </ul>
      </div>
      <LiveDemo />
    </section>

    <section className="mx-auto max-w-[1180px] px-6 py-16">
      <h2 className="max-w-[22ch] text-[clamp(1.6rem,3vw,2.3rem)]">From &ldquo;it&apos;s broken&rdquo; to fixed in four steps</h2>
      <ol className="mt-10 grid gap-6 md:grid-cols-4">
        {STEPS.map(([t, d], i) => <li key={t} className="relative border-t-2 border-line pt-5">
          <span className="absolute -top-[9px] left-0 h-4 w-4 rounded-full border-2 border-paper" style={{ background: ["#5ce1ff", "#8b7cff", "#ff7ad9", "#ffd23f"][i] }} />
          <div className="text-sm text-slate">Step {i + 1}</div>
          <h3 className="mt-1 text-lg">{t}</h3><p className="mt-2 text-slate">{d}</p></li>)}
      </ol>
    </section>

    <section className="mx-auto max-w-[1180px] px-6 py-16">
      <div className="grid gap-12 lg:grid-cols-[0.8fr_1.2fr]">
        <div><h2 className="text-[clamp(1.6rem,3vw,2.3rem)]">Built for teams that answer the phones</h2>
          <p className="mt-4 max-w-[44ch] text-slate">Import your old tickets on day one. JoltIQ starts answering with your team&apos;s own fixes immediately.</p></div>
        <dl className="grid gap-x-10 gap-y-8 sm:grid-cols-2">{FOR.map(([t, d]) => <div key={t}>
          <dt className="text-lg font-semibold text-white">{t}</dt><dd className="mt-1 text-slate">{d}</dd></div>)}</dl>
      </div>
    </section>

    <section className="mx-auto max-w-[1180px] px-6 py-10">
      <div className="flex flex-col items-center rounded-[28px] border border-line bg-surface/60 px-6 py-16 text-center">
        <Core size={72} />
        <h2 className="mt-6 max-w-[24ch] text-[clamp(1.6rem,3vw,2.3rem)]">Give your customers a technician that never sleeps</h2>
        <Link href="/register" className="btn btn-primary mt-8 px-6 py-3 text-base">Try JoltIQ free</Link>
      </div>
    </section>
  </main>;
}
