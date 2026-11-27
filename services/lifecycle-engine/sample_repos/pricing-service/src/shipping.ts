import type { Flags } from "flagsmith-nodejs";

export function shippingCost(flags: Flags, subtotal: number): number {
  if (flags.isFeatureEnabled("free_shipping") && subtotal >= 50) {
    return 0;
  }
  const rate = flags.getFeatureValue("shipping_rate");
  return typeof rate === "number" ? rate : 5.99;
}
