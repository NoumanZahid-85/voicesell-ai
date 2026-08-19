"use client";

import { MotionConfig } from "motion/react";

/** Root motion config: respect the OS reduced-motion setting globally. */
export function MotionProvider({ children }: { children: React.ReactNode }) {
  return <MotionConfig reducedMotion="user">{children}</MotionConfig>;
}