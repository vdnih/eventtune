"use client";

import { useEffect, useState } from "react";
import { onAuthStateChanged, type User } from "firebase/auth";
import { auth } from "@/lib/firebase";

export type AuthState = "checking" | "authed" | "unauthed";

/** Firebase の認証状態を購読し、ユーザーと状態を返す。 */
export function useAuth(): { user: User | null; state: AuthState } {
  const [user, setUser] = useState<User | null>(null);
  const [state, setState] = useState<AuthState>("checking");

  useEffect(() => {
    const unsub = onAuthStateChanged(auth, (u) => {
      setUser(u);
      setState(u ? "authed" : "unauthed");
    });
    return () => unsub();
  }, []);

  return { user, state };
}
