import { test } from "node:test";
import assert from "node:assert/strict";
import { shippingCost } from "./shipping.ts";

const flags = (enabled: boolean, rate: unknown) =>
  ({ isFeatureEnabled: () => enabled, getFeatureValue: () => rate }) as any;

test("free shipping above threshold", () => {
  assert.equal(shippingCost(flags(true, 4), 60), 0);
});

test("falls back to default rate", () => {
  assert.equal(shippingCost(flags(false, null), 10), 5.99);
});
