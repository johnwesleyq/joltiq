import Link from "next/link";

export const metadata = { title: "Pricing" };

const TIERS = [
  { name: "Starter", price: "$299", note: "per month", rows: ["3 technicians", "50 devices", "1,000 browser and desktop minutes", "200 research lookups a day", "2 integrations"] },
  { name: "Growth", price: "$799", note: "per month", rows: ["10 technicians", "250 devices", "5,000 browser and desktop minutes", "1,000 research lookups a day", "5 integrations", "White-label portal"] },
  { name: "Scale", price: "$1,999", note: "per month", rows: ["30 technicians", "1,000 devices", "20,000 browser and desktop minutes", "5,000 research lookups a day", "20 integrations", "Custom domain"] },
  { name: "Enterprise", price: "Custom", note: "annual contract", rows: ["Volume pricing", "Dedicated environment option", "Security review support", "Custom connectors"] },
];

export default function Pricing() {
  return <main className="mx-auto max-w-[1120px] px-6 py-10">
    <h1 className="text-4xl font-bold">Pricing</h1>
    <p className="mt-3 max-w-[62ch] text-slate">Every plan starts with a 14-day trial. Annual billing is two months free. These prices are an early proposal and may change before general availability.</p>
    <div className="mt-10 grid gap-px overflow-hidden rounded-lg border border-line bg-line md:grid-cols-4">
      {TIERS.map((t) => <div key={t.name} className="bg-white p-6">
        <h2 className="text-xl font-bold">{t.name}</h2>
        <div className="mt-3"><span className="font-display text-3xl font-bold">{t.price}</span> <span className="text-sm text-slate">{t.note}</span></div>
        <ul className="mt-5 space-y-1.5 text-[0.93rem]">{t.rows.map((r) => <li key={r}>{r}</li>)}</ul>
        <Link href={t.name === "Enterprise" ? "/contact" : "/register"} className="btn btn-ghost mt-6">{t.name === "Enterprise" ? "Talk to us" : "Start a trial"}</Link>
      </div>)}
    </div>
    <p className="mt-6 text-sm text-slate">AI model usage is included up to your plan&apos;s monthly budget. You can also bring your own OpenAI, Anthropic or Gemini key.</p>
  </main>;
}
