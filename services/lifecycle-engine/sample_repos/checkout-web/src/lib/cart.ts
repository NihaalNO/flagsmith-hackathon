import type { IFlagsmith } from "flagsmith";
import { FLAGS } from "./flags.ts";

export interface CartItem {
  price: number;
  quantity: number;
}

function legacyTotal(items: CartItem[]): number {
  let total = 0;
  for (const item of items) {
    total += item.price * item.quantity;
  }
  return Math.round(total);
}

function itemizedTotal(items: CartItem[]): number {
  const cents = items.reduce((sum, i) => sum + Math.round(i.price * 100) * i.quantity, 0);
  return cents / 100;
}

export function cartTotal(flagsmith: IFlagsmith, items: CartItem[]): number {
  if (flagsmith.hasFeature(FLAGS.NEW_CHECKOUT)) {
    return itemizedTotal(items);
  } else {
    return legacyTotal(items);
  }
}
