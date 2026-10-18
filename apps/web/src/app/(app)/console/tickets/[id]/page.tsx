"use client";
import { use } from "react";
import TicketView from "@/components/TicketView";
export default function Page({ params }: { params: Promise<{ id: string }> }) { const { id } = use(params); return <TicketView id={id} staff />; }
