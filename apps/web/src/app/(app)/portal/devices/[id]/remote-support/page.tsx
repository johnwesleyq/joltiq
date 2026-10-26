import RemoteSupportWorkspace from "@/components/RemoteSupportWorkspace";

export default async function RemoteSupportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <RemoteSupportWorkspace deviceId={id} />;
}
