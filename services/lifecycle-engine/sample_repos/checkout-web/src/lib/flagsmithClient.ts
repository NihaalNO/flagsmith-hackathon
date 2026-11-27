import flagsmith from "flagsmith";

// Singleton browser client used outside React components.
export const flagsmithClient = flagsmith;

export function initFlags(environmentID: string) {
  return flagsmithClient.init({ environmentID });
}
