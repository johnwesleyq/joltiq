import Core from "@/components/Core";

/** One orchestrated hero sequence: a customer asks, JoltIQ finds the fix, asks permission, edits the record on screen, proves it. */
const CHAT: { who: "you" | "ai" | "ok"; text: string; at: number }[] = [
  { who: "you", text: "Our website has been down since we moved hosting.", at: 0.2 },
  { who: "ai", text: "Your domain still points to the old server. Your team fixed the same thing in March by updating the A record.", at: 1.4 },
  { who: "ai", text: "Can I open your domain settings and change it to the new server?", at: 2.6 },
  { who: "you", text: "Yes, go ahead.", at: 3.6 },
  { who: "ok", text: "Done. I loaded your site from outside your network and it's back up.", at: 6.6 },
];

export default function LiveDemo() {
  return <div className="relative" aria-label="Example: JoltIQ fixing a DNS record">
    <div className="grid overflow-hidden rounded-[22px] border border-line bg-surface/80 shadow-[0_40px_120px_-40px_rgb(139_124_255/.6)] backdrop-blur md:grid-cols-[1fr_1.05fr]">
      <div className="space-y-2.5 p-5">
        <div className="mb-3 flex items-center gap-2"><Core size={22} /><span className="text-sm font-semibold">JoltIQ</span></div>
        {CHAT.map((m, i) => <div key={i} className="demo-step" style={{ animationDelay: `${m.at}s` }}>
          {m.who === "you"
            ? <div className="ml-auto w-fit max-w-[90%] rounded-2xl rounded-tr-sm bg-gradient-to-br from-plasma to-[#5b4fd6] px-3 py-2 text-sm text-white">{m.text}</div>
            : <div className={m.who === "ok" ? "w-fit max-w-[95%] rounded-2xl rounded-tl-sm border border-verify/40 bg-verify-soft px-3 py-2 text-sm text-white"
                                             : "w-fit max-w-[95%] rounded-2xl rounded-tl-sm bg-surface-2 px-3 py-2 text-sm"}>{m.who === "ok" && <b className="text-verify">Fixed ✓ </b>}{m.text}</div>}
        </div>)}
      </div>
      <div className="relative border-t border-line bg-[#0a0f28] p-4 md:border-l md:border-t-0">
        <div className="mb-3 flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-stop/70" /><span className="h-2.5 w-2.5 rounded-full bg-jolt/70" /><span className="h-2.5 w-2.5 rounded-full bg-verify/70" />
          <span className="ml-2 truncate text-xs text-slate">Customer&apos;s screen · domain settings</span></div>
        <div className="demo-step rounded-lg border border-jolt/60 px-2 py-1 text-xs text-jolt" style={{ animationDelay: "4s" }}>JoltIQ is controlling this screen · press Esc to stop</div>
        <table className="mt-3 w-full text-left text-xs">
          <thead className="text-slate"><tr><th className="py-1.5 font-medium">Type</th><th className="font-medium">Host</th><th className="font-medium">Value</th></tr></thead>
          <tbody>
            <tr className="border-t border-line"><td className="py-2">A</td><td>@</td><td className="relative font-mono">
              <span className="demo-value-old absolute">104.21.8.14</span><span className="demo-value-new text-verify">203.0.113.10</span></td></tr>
            <tr className="border-t border-line"><td className="py-2">CNAME</td><td>www</td><td className="font-mono">@</td></tr>
            <tr className="border-t border-line"><td className="py-2">MX</td><td>@</td><td className="font-mono">mail.host.net</td></tr>
          </tbody>
        </table>
        <div className="demo-step mt-5 space-y-1.5 rounded-lg border border-line bg-surface/60 p-3 font-mono text-[11px] text-slate" style={{ animationDelay: "5.6s" }}>
          <div>checking mysite.com from outside your network</div>
          <div className="demo-step text-verify" style={{ animationDelay: "6.3s" }}>resolves to 203.0.113.10 · page loads (200 OK)</div>
        </div>
        <svg className="demo-cursor pointer-events-none absolute left-0 top-0 h-5 w-5 drop-shadow" viewBox="0 0 24 24" aria-hidden><path d="M3 2l7 19 2.5-7.5L20 11z" fill="#ffd23f" stroke="#1a1405" strokeWidth="1.2" /></svg>
      </div>
    </div>
  </div>;
}
