import React from "react";
import { flagsmithClient } from "../lib/flagsmithClient";

export function Banner() {
  const text = flagsmithClient.getValue("banner_text", { fallback: "Welcome back!" });
  return <div className="banner">{text}</div>;
}
