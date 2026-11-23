export const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : "Please try again.";

export const formatDateTime = (value: string) =>
  new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
