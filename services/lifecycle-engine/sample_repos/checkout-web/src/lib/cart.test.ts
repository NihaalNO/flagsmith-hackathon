import { test } from "node:test";
import assert from "node:assert/strict";
import { cartTotal } from "./cart.ts";

const enabled = { hasFeature: (_name: string) => true } as any;

test("cartTotal keeps cent precision with the new checkout", () => {
  assert.equal(cartTotal(enabled, [{ price: 1.25, quantity: 2 }, { price: 0.1, quantity: 3 }]), 2.8);
});
