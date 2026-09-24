"use client";

import { useEffect, useState } from "react";
import { fetchAuthStatus } from "@/lib/auth";
import { effectiveLearningPolicy, isAdministrator, type LearningPolicy } from "@/lib/learning-access";

export interface LearningAccess {
  resolved: boolean;
  statusAvailable: boolean;
  isAdmin: boolean;
  policy: LearningPolicy | null;
}

export function useLearningAccess(): LearningAccess {
  const [access, setAccess] = useState<LearningAccess>({
    resolved: false, statusAvailable: false, isAdmin: false, policy: null,
  });
  useEffect(() => {
    let alive = true;
    const refresh = () => void fetchAuthStatus().then(status => {
      if (alive) setAccess({
        resolved: true,
        statusAvailable: status !== null,
        isAdmin: isAdministrator(status),
        policy: effectiveLearningPolicy(status),
      });
    });
    refresh();
    window.addEventListener("focus", refresh);
    return () => {
      alive = false;
      window.removeEventListener("focus", refresh);
    };
  }, []);
  return access;
}
