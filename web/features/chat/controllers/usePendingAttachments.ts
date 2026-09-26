"use client";

import { useCallback, useEffect, useRef, useState, type SetStateAction } from "react";
import type { AttachmentLimits } from "@/lib/attachment-limits";
import {
  preparePendingAttachments,
  type AttachmentPreparationFailure,
  type PendingAttachment,
} from "./pending-attachments";

// All composers share the HEIC worker. Serializing only each selected batch
// still allows two paste/drop events (or two composers) to overlap its codec.
let preparationQueue: Promise<void> = Promise.resolve();

export function usePendingAttachments(limits: AttachmentLimits) {
  const [attachments, updateAttachments] = useState<PendingAttachment[]>([]);
  const [attachmentsPreparing, setPreparing] = useState(false);
  const current = useRef<PendingAttachment[]>([]);
  const pending = useRef(0);
  const latestBatch = useRef<Promise<unknown>>(Promise.resolve());
  const mounted = useRef(true);
  const generation = useRef(0);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      generation.current += 1;
    };
  }, []);

  const setAttachments = useCallback((value: SetStateAction<PendingAttachment[]>) => {
    // Clearing the composer also cancels any result still being prepared;
    // removing one completed item leaves the other selected files queued.
    if (Array.isArray(value) && value.length === 0) generation.current += 1;
    const next = typeof value === "function" ? value(current.current) : value;
    current.current = next;
    updateAttachments(next);
  }, []);

  const prepareAndAppendAttachments = useCallback(
    (files: File[]) => {
      const admittedGeneration = generation.current;
      pending.current += 1;
      setPreparing(true);
      const run = async (): Promise<{ failures: AttachmentPreparationFailure[] }> => {
        if (!mounted.current || generation.current !== admittedGeneration) return { failures: [] };
        const prepared = await preparePendingAttachments(files, 0, limits);
        if (!mounted.current || generation.current !== admittedGeneration) return { failures: [] };

        // The user can remove an attachment while decoding. Check the live
        // collection at commit time, not the state captured by an async handler.
        const next = [...current.current];
        const failures = [...prepared.failures];
        let total = next.reduce((sum, item) => sum + (item.size ?? 0), 0);
        for (const item of prepared.attachments) {
          const size = item.size ?? 0;
          if (total + size > limits.maxTotalBytes) {
            failures.push({ name: item.filename, reason: "quota" });
          } else {
            total += size;
            next.push(item);
          }
        }
        setAttachments(() => next);
        return { failures };
      };
      const task = preparationQueue.then(run, run).finally(() => {
        pending.current -= 1;
        if (mounted.current) setPreparing(pending.current > 0);
      });
      preparationQueue = task.then(
        () => undefined,
        () => undefined
      );
      latestBatch.current = task;
      return task;
    },
    [limits, setAttachments]
  );

  const waitForAttachments = useCallback(async () => {
    const admittedGeneration = generation.current;
    while (pending.current > 0) await latestBatch.current;
    return mounted.current && generation.current === admittedGeneration ? current.current : null;
  }, []);

  return {
    attachments,
    setAttachments,
    attachmentsPreparing,
    prepareAndAppendAttachments,
    waitForAttachments,
  };
}
