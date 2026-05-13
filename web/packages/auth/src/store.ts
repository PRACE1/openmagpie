"use client";

import { create } from "zustand";
import type { AuthUser } from "@magpie/api-utils";

export interface AuthState {
  user: AuthUser | null;
  setUser: (user: AuthUser | null) => void;
  clear: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  setUser: (user) => set({ user }),
  clear: () => set({ user: null }),
}));
