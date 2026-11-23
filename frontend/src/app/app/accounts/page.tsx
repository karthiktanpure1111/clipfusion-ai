import type { Metadata } from "next";
import { Suspense } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { AccountsView } from "@/components/accounts/accounts-view";

export const metadata: Metadata = { title: "Connected Accounts" };

export default function AccountsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-2xl" />}>
      <AccountsView />
    </Suspense>
  );
}
