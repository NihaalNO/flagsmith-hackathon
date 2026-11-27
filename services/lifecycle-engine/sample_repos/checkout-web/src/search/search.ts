import type { IFlagsmith } from "flagsmith";
import { LEGACY_SEARCH_FLAG } from "../lib/flags.ts";

export function modernSearch(query: string): string[] {
  return [`modern:${query}`];
}

export function legacySearch(query: string): string[] {
  return [`legacy:${query}`];
}

export function search(flagsmith: IFlagsmith, query: string): string[] {
  if (!flagsmith.hasFeature(LEGACY_SEARCH_FLAG)) {
    return modernSearch(query);
  }
  return legacySearch(query);
}
