"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAct, useApi } from "@/lib/hooks";
import { ErrorNote } from "@/components/ui";

type RemoteSession = {
  id: string;
  device_id: string;
  state: "active" | "ended";
  reason: string;
  created_at: string;
  ended_at: string | null;
  workspace_path: string;
  transport_url: string | null;
};

type RemoteStatus = {
  enabled: boolean;
  configured: boolean;
  active_session: RemoteSession | null;
};

export default function RemoteSupportWorkspace({ deviceId }: { deviceId: string }) {
  const status = useApi<RemoteStatus>(["remote-access", deviceId], `/devices/${deviceId}/remote-access`, { refetchInterval: 5000 });
  const [ended, setEnded] = useState(false);
  const end = useAct(
    (sessionId: string) => api<RemoteSession>(`/devices/${deviceId}/remote-access/sessions/${sessionId}/end`, { method: "POST" }),
    [["remote-access", deviceId]],
  );
  const session = ended ? null : status.data?.active_session;

  async function endSupport() {
    if (!session) return;
    await end.mutateAsync(session.id);
    setEnded(true);
  }

  if (status.isLoading) {
    return <section className="remote-workspace-state" aria-live="polite"><span className="remote-status-dot" />Preparing remote support…</section>;
  }

  if (status.error) {
    return <section className="remote-workspace-state">
      <h1 className="font-display text-2xl">Remote support</h1>
      <ErrorNote error={status.error} />
      <p className="text-sm text-slate">JoltIQ could not load this session. Return to the device list and try again.</p>
      <Link className="btn btn-ghost mt-4" href="/portal/devices">Back to devices</Link>
    </section>;
  }

  if (!status.data?.enabled || !status.data.configured) {
    return <section className="remote-workspace-state">
      <p className="remote-eyebrow">JoltIQ Remote Support</p>
      <h1 className="font-display text-2xl">Remote support</h1>
      <p className="mt-3 text-slate">This computer is not ready for remote support. Ask an administrator to link it, then start a new session.</p>
      <Link className="btn btn-ghost mt-5" href="/portal/devices">Back to devices</Link>
    </section>;
  }

  if (!session) {
    return <section className="remote-workspace-state">
      <p className="remote-eyebrow">Session ended</p>
      <h1 className="font-display text-2xl">Remote support</h1>
      <p className="mt-3 text-slate">This support session is no longer active. No further screen access is requested.</p>
      <Link className="btn btn-ghost mt-5" href="/portal/devices">Back to devices</Link>
    </section>;
  }

  return <div className="remote-workspace">
    <aside className="remote-workspace-rail" aria-label="Support session status">
      <div>
        <p className="remote-eyebrow">Live assistance</p>
        <h1 className="font-display text-2xl">Remote support</h1>
      </div>

      <div className="remote-session-status" aria-live="polite">
        <span className="remote-status-dot" aria-hidden="true" />
        <div>
          <strong>Waiting for the person at the computer</strong>
          <p>Local permission is still required before the remote view can begin.</p>
        </div>
      </div>

      <div className="remote-session-reason">
        <span>Reason for access</span>
        <p>{session.reason}</p>
      </div>

      <div className="mt-auto space-y-3">
        <p className="text-xs text-slate">A privacy banner remains visible on the computer. The person there can stop access at any time.</p>
        <ErrorNote error={end.error} />
        <button className="btn btn-stop w-full" disabled={end.isPending} onClick={endSupport}>
          {end.isPending ? "Ending…" : "End support"}
        </button>
        <Link className="block text-center text-xs text-slate hover:text-ink" href="/portal/devices">Back to devices</Link>
      </div>
    </aside>

    <section className="remote-canvas" aria-label="Remote desktop area">
      <div className="remote-canvas-bar">
        <div><span className="remote-canvas-kicker">JoltIQ secure view</span><strong>Customer computer</strong></div>
        <span className="text-xs text-slate">Permission controlled on the computer</span>
      </div>
      {session.transport_url ? <iframe
        className="remote-desktop-frame"
        src={session.transport_url}
        title="JoltIQ remote desktop"
        sandbox="allow-scripts allow-same-origin allow-forms allow-modals allow-popups allow-downloads"
        allow="clipboard-read; clipboard-write; fullscreen"
      /> : <div className="remote-transport-missing" role="status">
        <strong>The remote view is unavailable.</strong>
        <p>Staff authentication may be required. Return to the device list and start a new support session after signing in.</p>
      </div>}
    </section>
  </div>;
}
