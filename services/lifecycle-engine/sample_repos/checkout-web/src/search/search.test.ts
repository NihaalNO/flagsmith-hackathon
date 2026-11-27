import { test } from "node:test";
import assert from "node:assert/strict";
import { search } from "./search.ts";

const disabled = { hasFeature: (_name: string) => false } as any;

test("search uses the modern engine", () => {
  assert.deepEqual(search(disabled, "shoes"), ["modern:shoes"]);
});
