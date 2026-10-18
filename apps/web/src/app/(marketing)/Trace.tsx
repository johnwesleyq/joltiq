// One orchestrated sequence: a real resolution path, step by step. Reduced-motion users see the final state.
const STEPS = [
  { who: "Customer", text: "Outlook closes every time I open a PDF attachment.", tone: "plain" },
  { who: "JoltIQ", text: "Found Event 1000: faulting module PdfPreview.dll. Matches approved runbook KB-214.", tone: "ai" },
  { who: "Plan", text: "1. Disable add-in PdfPreview.Connect (sensitive)\n2. Restart Outlook (sensitive)", tone: "plan" },
  { who: "Customer", text: "Approved both steps.", tone: "plain" },
  { who: "Check", text: "LoadBehavior read back = 2 ✓   Outlook responding ✓", tone: "verify" },
  { who: "Ticket #1042", text: "Resolved — verified independently. Draft article sent for review.", tone: "verify" },
];

export default function Trace() {
  return <div aria-label="Example resolution" className="relative rounded-xl bg-ink p-6 text-[0.95rem] text-white shadow-[0_30px_60px_-30px_rgba(21,35,58,.6)]">
    <ol className="space-y-3">
      {STEPS.map((s, i) => <li key={i} className="trace-step grid grid-cols-[96px_1fr] gap-3" style={{ animationDelay: `${i * 0.9}s` }}>
        <span className="pt-1.5 text-sm text-white/55">{s.who}</span>
        <span className={
          s.tone === "ai" ? "rounded-md bg-jolt px-3 py-2 text-ink" :
          s.tone === "verify" ? "rounded-md bg-verify px-3 py-2" :
          s.tone === "plan" ? "whitespace-pre-line rounded-md border border-white/25 px-3 py-2" :
          "rounded-md bg-ink-2 px-3 py-2"}>{s.text}</span>
      </li>)}
    </ol>
  </div>;
}
