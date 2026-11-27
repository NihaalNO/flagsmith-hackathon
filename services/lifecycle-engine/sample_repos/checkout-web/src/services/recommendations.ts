import type { IFlagsmith } from "flagsmith";

export class RecommendationService {
  constructor(private readonly flagsmith: IFlagsmith) {}

  limit(): number {
    return this.flagsmith.hasFeature("recommendations_v2") ? 12 : 4;
  }
}
