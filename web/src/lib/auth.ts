"use client";

import apiClient from "@/lib/api/client";

export async function handleLogout(redirect: (path: string) => void) {
  await apiClient.POST("/api/v1/auth/cookie/logout");
  redirect("/login");
}

export async function handleOauth(toastError: (msg: string) => void) {
  const { error, data } = await apiClient.GET("/api/v1/auth/oauth/authorize", {
    params: {
      query: {
        scopes: ["openid", "email", "profile"],
      },
    },
  });
  if (!error && data?.authorization_url) {
    window.location.href = data.authorization_url;
  } else {
    toastError("Failed to initiate OAuth login.");
  }
}
