import { flagsmith } from "./client";

export async function quoteHandler(userId: string) {
  const flags = await flagsmith.getIdentityFlags(userId);
  if (flags.isFeatureEnabled("new_checkout")) {
    return { version: 2 };
  }
  return { version: 1 };
}
