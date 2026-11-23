import type { Metadata } from "next";
import { ClientOnly } from "@/components/common/client-only";
import { Skeleton } from "@/components/ui/skeleton";
import { CalendarView } from "@/components/calendar/calendar-view";

export const metadata: Metadata = { title: "Publishing Calendar" };

export default function CalendarPage() {
  return (
    <ClientOnly fallback={<Skeleton className="h-[480px] rounded-2xl" />}>
      <CalendarView />
    </ClientOnly>
  );
}
