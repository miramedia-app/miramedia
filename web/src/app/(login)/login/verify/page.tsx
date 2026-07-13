"use client";

import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { UserCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { handleLogout } from "@/lib/auth";

export default function VerifyPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const onLogout = () => handleLogout(qc, (p) => router.push(p));
  return (
    <>
      <div className="absolute top-4 right-4">
        <Button onClick={onLogout} variant="outline">
          Logout
        </Button>
      </div>
      <div className="mx-auto w-full max-w-md text-center">
        <div className="mb-6">
          <UserCheck className="mx-auto h-16 w-16 text-primary" />
        </div>
        <h1 className="mt-4 text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
          Account Pending Activation
        </h1>
        <p className="mt-4 text-lg text-muted-foreground">
          Your account has been successfully created, but activation by an administrator is
          required.
        </p>
        <div className="mt-8">
          <Button onClick={onLogout}>Logout</Button>
        </div>
        <p className="end mt-10 text-sm text-muted-foreground">
          If you have any questions, please contact an administrator.
        </p>
      </div>
    </>
  );
}
