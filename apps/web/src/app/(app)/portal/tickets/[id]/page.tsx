"use client";
import { use } from "react";
import TicketView from "@/components/TicketView";
import { STAFF } from "@/lib/api";
import { useMe } from "@/lib/hooks";
export default function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const me = useMe();
  return <TicketView id={id} staff={STAFF.includes(me.data?.active_organization?.role ?? "")} />;
}
