import React from "react";
import { useFlags } from "flagsmith/react";
import { NewCheckout } from "./NewCheckout";
import { LegacyCheckout } from "./LegacyCheckout";

export function CheckoutPage() {
  const flags = useFlags(["new_checkout"]);
  return (
    <main>
      <h1>Checkout</h1>
      {flags.new_checkout.enabled ? <NewCheckout /> : <LegacyCheckout />}
    </main>
  );
}
