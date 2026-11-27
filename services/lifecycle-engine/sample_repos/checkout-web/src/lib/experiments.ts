import type { IFlagsmith } from "flagsmith";

// Experiment flags are named at runtime, so their names cannot be resolved statically.
export function isExperimentOn(client: IFlagsmith, experiment: string): boolean {
  return client.hasFeature(`exp_${experiment}`);
}

export function readFlag(client: IFlagsmith, name: string) {
  return client.getValue(name);
}
