"use client";
import Link from "next/link";
import TicketList from "@/components/TicketList";
import { PageHead } from "@/components/ui";
export default function MyTickets() {
  return <div><PageHead title="My tickets"><Link className="btn btn-primary" href="/portal">Get help</Link></PageHead><TicketList base="/portal/tickets" staff={false} /></div>;
}
