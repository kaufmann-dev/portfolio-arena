import type { MetaBatchStatus } from "./api/types";

export function metaBatchStatusCopy(status: MetaBatchStatus): string {
  if (status === "ready") {
    return "Each meta run uses frozen sources matching its agent, managed/rebuilt mode, and long/short direction.";
  }
  if (status === "insufficient") {
    return "The batch did not contain enough usable normal-portfolio decisions to run synthesis.";
  }
  if (status === "failed") {
    return "The source packet could not be constructed, so meta evaluations were not queued.";
  }
  return "Meta evaluations begin after every due normal-portfolio run for the same agent reaches a terminal state.";
}
