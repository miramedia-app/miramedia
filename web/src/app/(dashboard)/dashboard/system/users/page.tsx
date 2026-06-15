"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  EllipsisVertical,
  KeyRound,
  Mail,
  Pencil,
  Plus,
  Power,
  PowerOff,
  ShieldCheck,
  Trash2,
  User as UserIcon,
  UsersIcon,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { DashboardHeader } from "@/components/dashboard-header";
import { StatusPill } from "@/components/ui/status-pill";
import { TypePill } from "@/components/ui/type-pill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { DataList } from "@/components/data-list";
import type {
  BulkAction,
  ColumnDef,
  FacetDef,
  GroupByDef,
  SortOption,
} from "@/components/data-list";
import { useUser } from "@/components/providers/user-provider";
import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";

type UserRead = components["schemas"]["UserRead"];

function formatDate(value: string | null | undefined): string {
  if (!value) return "Never";
  return new Date(value).toLocaleString();
}

export default function UsersPage() {
  const { user: currentUser } = useUser();
  const qc = useQueryClient();

  const usersQuery = useQuery({
    queryKey: ["users", "all"],
    queryFn: async () => {
      const { data } = await apiClient.GET("/api/v1/users");
      return (data ?? []) as UserRead[];
    },
  });
  const users = usersQuery.data ?? [];

  // Dialogs
  const [addOpen, setAddOpen] = React.useState(false);
  const [saving, startSaving] = React.useTransition();
  const [newEmail, setNewEmail] = React.useState("");
  const [newPassword, setNewPassword] = React.useState("");
  const [newIsActive, setNewIsActive] = React.useState(true);
  const [newIsSuperuser, setNewIsSuperuser] = React.useState(false);
  const [newIsVerified, setNewIsVerified] = React.useState(true);

  function resetForm() {
    setNewEmail("");
    setNewPassword("");
    setNewIsActive(true);
    setNewIsSuperuser(false);
    setNewIsVerified(true);
  }

  function createUser() {
    if (!newEmail || !newPassword) {
      toast.error("Email and password are required");
      return;
    }
    startSaving(async () => {
      try {
        const { error } = await apiClient.POST("/api/v1/users/create", {
          body: {
            email: newEmail,
            password: newPassword,
            is_active: newIsActive,
            is_superuser: newIsSuperuser,
            is_verified: newIsVerified,
          },
        });
        if (error) {
          toast.error(
            `Failed to create user: ${(error as { detail?: string }).detail ?? "Unknown error"}`,
          );
          return;
        }
        toast.success(`User ${newEmail} created successfully`);
        setAddOpen(false);
        resetForm();
        await qc.invalidateQueries({ queryKey: ["users", "all"] });
      } catch {
        toast.error("Failed to create user");
      }
    });
  }

  const [editDialogOpen, setEditDialogOpen] = React.useState(false);
  const [selectedUser, setSelectedUser] = React.useState<UserRead | null>(null);
  const [editEmail, setEditEmail] = React.useState("");
  const [editPassword, setEditPassword] = React.useState("");

  async function saveUser() {
    if (!selectedUser) return;
    const { error } = await apiClient.PATCH("/api/v1/users/{id}", {
      params: { path: { id: selectedUser.id } },
      body: {
        is_verified: selectedUser.is_verified,
        is_active: selectedUser.is_active,
        is_superuser: selectedUser.is_superuser,
        ...(editPassword !== "" && { password: editPassword }),
        ...(editEmail !== "" && { email: editEmail }),
      },
    });
    if (error) {
      toast.error(`Failed to update user ${selectedUser.email}`);
    } else {
      toast.success(`User ${selectedUser.email} updated successfully.`);
      setEditDialogOpen(false);
      setSelectedUser(null);
      setEditPassword("");
      setEditEmail("");
    }
    await qc.invalidateQueries({ queryKey: ["users", "all"] });
  }

  const [userToDelete, setUserToDelete] = React.useState<UserRead | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = React.useState(false);

  async function deleteUser() {
    if (!userToDelete) return;
    const { error } = await apiClient.DELETE("/api/v1/users/{id}", {
      params: { path: { id: userToDelete.id } },
    });
    if (error) {
      toast.error(`Failed to delete user ${userToDelete.email}`);
    } else {
      toast.success(`User ${userToDelete.email} deleted successfully.`);
      setDeleteDialogOpen(false);
      setUserToDelete(null);
    }
    await qc.invalidateQueries({ queryKey: ["users", "all"] });
  }

  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [inviting, setInviting] = React.useState(false);
  const [inviteEmail, setInviteEmail] = React.useState("");
  const [inviteIsSuperuser, setInviteIsSuperuser] = React.useState(false);

  async function inviteUser() {
    if (!inviteEmail.trim()) {
      toast.error("Email is required");
      return;
    }
    setInviting(true);
    try {
      const { data, error } = await apiClient.POST("/api/v1/users/invite", {
        body: { email: inviteEmail.trim(), is_superuser: inviteIsSuperuser },
      });
      if (error) {
        toast.error(`Invite failed: ${(error as { detail?: string }).detail ?? ""}`);
        return;
      }
      toast.success(
        (data as { invite_email_sent?: boolean } | undefined)?.invite_email_sent
          ? `Invite email sent to ${inviteEmail}`
          : `User created. Email send failed — share the reset link manually.`,
      );
      setInviteOpen(false);
      setInviteEmail("");
      setInviteIsSuperuser(false);
      await qc.invalidateQueries({ queryKey: ["users", "all"] });
    } finally {
      setInviting(false);
    }
  }

  async function sendPasswordReset(target: UserRead) {
    if (!confirm(`Send password reset email to ${target.email}?`)) return;
    const { error } = await apiClient.POST("/api/v1/users/{user_id}/password-reset", {
      params: { path: { user_id: target.id } },
    });
    if (error) {
      toast.error("Failed to send reset email");
    } else {
      toast.success(`Reset email sent to ${target.email}`);
    }
  }

  async function bulkSetActive(items: UserRead[], activate: boolean) {
    if (items.length === 0) return;
    const { data, error } = await apiClient.POST("/api/v1/users/bulk", {
      body: {
        user_ids: items.map((u) => u.id) as never,
        is_active: activate,
      },
    });
    if (error) {
      toast.error("Bulk update failed");
      return;
    }
    toast.success(
      `${(data as { updated?: number } | undefined)?.updated ?? 0} user(s) ${
        activate ? "activated" : "deactivated"
      }`,
    );
    await qc.invalidateQueries({ queryKey: ["users", "all"] });
  }

  // DataList configuration
  const columns = React.useMemo<ColumnDef<UserRead>[]>(
    () => [
      {
        id: "email",
        header: "Email",
        width: "minmax(0,1fr)",
        render: (u) => (
          <>
            <span className="truncate text-sm font-medium">{u.email}</span>
            {u.id === currentUser?.id && <TypePill>You</TypePill>}
          </>
        ),
      },
      {
        id: "role",
        header: "Role",
        width: "80px",
        render: (u) => <TypePill>{u.is_superuser ? "Admin" : "User"}</TypePill>,
      },
      {
        id: "last-login",
        header: "Last login",
        width: "180px",
        hideBelow: "md",
        mono: true,
        render: (u) => (
          <span className="truncate text-xs text-muted-foreground">
            {u.last_login_at ? formatDate(u.last_login_at) : "Never"}
          </span>
        ),
      },
      {
        id: "verified",
        header: "Verified",
        width: "96px",
        hideBelow: "sm",
        render: (u) => <StatusPill status={u.is_verified ? "verified" : "unverified"} />,
      },
      {
        id: "status",
        header: "Status",
        width: "112px",
        hideBelow: "sm",
        render: (u) => <StatusPill status={u.is_active ? "active" : "inactive"} />,
      },
    ],
    [currentUser?.id],
  );

  const facets = React.useMemo<FacetDef<UserRead>[]>(
    () => [
      {
        id: "role",
        label: "Role",
        icon: <ShieldCheck className="h-3.5 w-3.5" />,
        options: [
          { value: "admin", label: "Admin", icon: <ShieldCheck className="h-3.5 w-3.5" /> },
          { value: "regular", label: "Regular", icon: <UserIcon className="h-3.5 w-3.5" /> },
        ],
        predicate: (u, values, op) => {
          const v = u.is_superuser ? "admin" : "regular";
          const hit = values.includes(v);
          return op === "excludes" ? !hit : hit;
        },
      },
      {
        id: "status",
        label: "Status",
        options: [
          { value: "active", label: "Active" },
          { value: "inactive", label: "Inactive" },
        ],
        predicate: (u, values, op) => {
          const v = u.is_active ? "active" : "inactive";
          const hit = values.includes(v);
          return op === "excludes" ? !hit : hit;
        },
      },
      {
        id: "verified",
        label: "Verified",
        options: [
          { value: "yes", label: "Verified" },
          { value: "no", label: "Unverified" },
        ],
        predicate: (u, values, op) => {
          const v = u.is_verified ? "yes" : "no";
          const hit = values.includes(v);
          return op === "excludes" ? !hit : hit;
        },
      },
    ],
    [],
  );

  const sortOptions: SortOption<UserRead>[] = [
    { id: "email-asc", label: "Email A–Z", compare: (a, b) => a.email.localeCompare(b.email) },
    { id: "email-desc", label: "Email Z–A", compare: (a, b) => b.email.localeCompare(a.email) },
    {
      id: "last-login-desc",
      label: "Last login (newest)",
      compare: (a, b) =>
        new Date(b.last_login_at ?? 0).getTime() - new Date(a.last_login_at ?? 0).getTime(),
    },
  ];

  const groupings = React.useMemo<GroupByDef<UserRead>[]>(
    () => [
      {
        id: "role",
        label: "Role",
        getGroup: (u) =>
          u.is_superuser
            ? { key: "admin", label: "Admin", sortOrder: 0 }
            : { key: "user", label: "User", sortOrder: 1 },
      },
      {
        id: "status",
        label: "Status",
        getGroup: (u) =>
          u.is_active
            ? { key: "active", label: "Active", sortOrder: 0 }
            : { key: "inactive", label: "Inactive", sortOrder: 1 },
      },
      {
        id: "verified",
        label: "Verified",
        getGroup: (u) =>
          u.is_verified
            ? { key: "verified", label: "Verified", sortOrder: 0 }
            : { key: "unverified", label: "Unverified", sortOrder: 1 },
      },
    ],
    [],
  );

  const bulkActions: BulkAction<UserRead>[] = [
    {
      id: "activate",
      label: "Activate",
      icon: <Power className="h-3.5 w-3.5" />,
      onRun: (items) => void bulkSetActive(items, true),
    },
    {
      id: "deactivate",
      label: "Deactivate",
      icon: <PowerOff className="h-3.5 w-3.5" />,
      onRun: (items) => void bulkSetActive(items, false),
    },
  ];

  const unselectableIds = React.useMemo(
    () => new Set(currentUser ? [currentUser.id] : []),
    [currentUser],
  );

  const renderRowActions = React.useCallback(
    (u: UserRead) =>
      u.id === currentUser?.id ? null : (
        <>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-muted-foreground"
            title="Send password reset"
            onClick={() => void sendPasswordReset(u)}
          >
            <KeyRound className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-muted-foreground"
            title="Edit"
            onClick={() => {
              setSelectedUser({ ...u });
              setEditEmail("");
              setEditPassword("");
              setEditDialogOpen(true);
            }}
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground">
                  <EllipsisVertical className="h-4 w-4" />
                </Button>
              }
            />
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                className="text-destructive"
                onClick={() => {
                  setUserToDelete(u);
                  setDeleteDialogOpen(true);
                }}
              >
                <Trash2 className="mr-2 h-4 w-4" />
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </>
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [currentUser?.id],
  );

  if (!currentUser?.is_superuser) {
    return (
      <>
        <DashboardHeader
          crumbs={[
            { label: "Dashboard", href: "/dashboard" },
            { label: "System", href: "/dashboard/system/users" },
            { label: "Users" },
          ]}
        />
        <main className="flex w-full flex-col gap-4 p-4 pt-0">
          <p className="text-sm text-muted-foreground">Admin access required.</p>
        </main>
      </>
    );
  }

  return (
    <>
      <DashboardHeader
        crumbs={[
          { label: "Dashboard", href: "/dashboard" },
          { label: "System", href: "/dashboard/system/users" },
          { label: "Users" },
        ]}
      />
      <main className="flex w-full flex-col gap-4 p-4 pt-0">
        <DataList<UserRead>
          data={users}
          getId={(u) => u.id}
          columns={columns}
          searchPlaceholder="Search or filter users…"
          searchMatch={(u, q) => u.email.toLowerCase().includes(q)}
          facets={facets}
          sortOptions={sortOptions}
          defaultSort="email-asc"
          groupings={groupings}
          defaultGroupId="role"
          bulkActions={bulkActions}
          unselectableIds={unselectableIds}
          loading={usersQuery.isLoading}
          emptyIcon={<UsersIcon />}
          emptyTitle="No users yet"
          emptyDescription="Invite or add a user to get started."
          toolbarTrailing={
            <>
              <Button
                size="default"
                variant="outline"
                className="text-xs"
                onClick={() => setInviteOpen(true)}
              >
                <Mail className="mr-1 h-4 w-4" />
                Invite
              </Button>
              <Button size="default" className="text-xs" onClick={() => setAddOpen(true)}>
                <Plus className="mr-1 h-4 w-4" />
                Add user
              </Button>
            </>
          }
          rowActions={renderRowActions}
        />
      </main>

      {/* Add user */}
      <Dialog
        open={addOpen}
        onOpenChange={(o) => {
          if (!o) resetForm();
          setAddOpen(o);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add User</DialogTitle>
            <DialogDescription>Create a new user account</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="new-email">Email</Label>
              <Input
                id="new-email"
                type="email"
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
                placeholder="user@example.com"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="new-password">Password</Label>
              <Input
                id="new-password"
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder="Password"
              />
            </div>
            <Separator />
            <div className="flex flex-col gap-3">
              <div className="flex items-center justify-between">
                <Label htmlFor="new-active">Active</Label>
                <Switch id="new-active" checked={newIsActive} onCheckedChange={setNewIsActive} />
              </div>
              <div className="flex items-center justify-between">
                <Label htmlFor="new-verified">Verified</Label>
                <Switch
                  id="new-verified"
                  checked={newIsVerified}
                  onCheckedChange={setNewIsVerified}
                />
              </div>
              <div className="flex items-center justify-between">
                <Label htmlFor="new-superuser">Admin</Label>
                <Switch
                  id="new-superuser"
                  checked={newIsSuperuser}
                  onCheckedChange={setNewIsSuperuser}
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setAddOpen(false);
                resetForm();
              }}
            >
              Cancel
            </Button>
            <Button onClick={() => void createUser()} disabled={saving}>
              {saving ? "Creating..." : "Create User"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit user */}
      <Dialog open={editDialogOpen} onOpenChange={setEditDialogOpen}>
        <DialogContent className="w-full max-w-[600px] rounded-lg p-6 shadow-lg">
          <DialogHeader>
            <DialogTitle className="mb-1 text-xl font-semibold">Edit user</DialogTitle>
            <DialogDescription className="mb-4 text-sm">
              Edit {selectedUser?.email}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-6">
            <div>
              <Label className="mb-1 block text-sm font-medium">Verified</Label>
              <RadioGroup
                className="flex gap-4"
                value={selectedUser?.is_verified ? "true" : "false"}
                onValueChange={(v) =>
                  setSelectedUser((s) => (s ? { ...s, is_verified: v === "true" } : s))
                }
              >
                <div className="flex items-center gap-1">
                  <RadioGroupItem id="verified-true" value="true" />
                  <Label htmlFor="verified-true" className="text-sm">
                    True
                  </Label>
                </div>
                <div className="flex items-center gap-1">
                  <RadioGroupItem id="verified-false" value="false" />
                  <Label htmlFor="verified-false" className="text-sm">
                    False
                  </Label>
                </div>
              </RadioGroup>
            </div>
            <hr />
            <div>
              <Label className="mb-1 block text-sm font-medium">Active</Label>
              <RadioGroup
                className="flex gap-4"
                value={selectedUser?.is_active ? "true" : "false"}
                onValueChange={(v) =>
                  setSelectedUser((s) => (s ? { ...s, is_active: v === "true" } : s))
                }
              >
                <div className="flex items-center gap-1">
                  <RadioGroupItem id="active-true" value="true" />
                  <Label htmlFor="active-true" className="text-sm">
                    True
                  </Label>
                </div>
                <div className="flex items-center gap-1">
                  <RadioGroupItem id="active-false" value="false" />
                  <Label htmlFor="active-false" className="text-sm">
                    False
                  </Label>
                </div>
              </RadioGroup>
            </div>
            <hr />
            <div>
              <Label className="mb-1 block text-sm font-medium">Admin</Label>
              <RadioGroup
                className="flex gap-4"
                value={selectedUser?.is_superuser ? "true" : "false"}
                onValueChange={(v) =>
                  setSelectedUser((s) => (s ? { ...s, is_superuser: v === "true" } : s))
                }
              >
                <div className="flex items-center gap-1">
                  <RadioGroupItem id="superuser-true" value="true" />
                  <Label htmlFor="superuser-true" className="text-sm">
                    True
                  </Label>
                </div>
                <div className="flex items-center gap-1">
                  <RadioGroupItem id="superuser-false" value="false" />
                  <Label htmlFor="superuser-false" className="text-sm">
                    False
                  </Label>
                </div>
              </RadioGroup>
            </div>
            <div>
              <Label htmlFor="edit-email" className="mb-1 block text-sm font-medium">
                Email
              </Label>
              <Input
                id="edit-email"
                value={editEmail}
                onChange={(e) => setEditEmail(e.target.value)}
                placeholder={selectedUser?.email}
                type="text"
              />
            </div>
            <div>
              <Label htmlFor="edit-password" className="mb-1 block text-sm font-medium">
                Password
              </Label>
              <Input
                id="edit-password"
                value={editPassword}
                onChange={(e) => setEditPassword(e.target.value)}
                placeholder="Keep empty to not change the password"
                type="password"
              />
            </div>
          </div>
          <div className="mt-8 flex justify-end gap-2">
            <Button onClick={() => setEditDialogOpen(false)} variant="outline">
              Cancel
            </Button>
            <Button onClick={() => void saveUser()} variant="destructive">
              Save
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Invite */}
      <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Invite User</DialogTitle>
            <DialogDescription>
              Creates a verified user with a random password and emails them a password reset link
              to choose their own.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="invite-email">Email</Label>
              <Input
                id="invite-email"
                type="email"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                placeholder="user@example.com"
              />
            </div>
            <div className="flex items-center justify-between">
              <Label htmlFor="invite-superuser">Grant admin privileges</Label>
              <Switch
                id="invite-superuser"
                checked={inviteIsSuperuser}
                onCheckedChange={setInviteIsSuperuser}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setInviteOpen(false)} disabled={inviting}>
              Cancel
            </Button>
            <Button onClick={() => void inviteUser()} disabled={inviting}>
              {inviting ? "Sending..." : "Send Invite"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete User</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete the user <strong>{userToDelete?.email}</strong>? This
              action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel
              onClick={() => {
                setDeleteDialogOpen(false);
                setUserToDelete(null);
              }}
            >
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void deleteUser()}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
