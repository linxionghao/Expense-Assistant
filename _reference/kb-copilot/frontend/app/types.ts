export type Role = "public" | "engineering";

export type SourceItem = {
  title: string;
  source: string;
  chunk_id: string;
  snippet: string;
};

export type AskResponse = {
  answer: string;
  sources: SourceItem[];
};

export type AskError = {
  error: string;
};

export type AskApiResponse = AskResponse | AskError;

export type ChatMessage =
  | { id: string; role: "user"; content: string }
  | { id: string; role: "assistant"; content: string; sources?: SourceItem[] };

export function isAskError(x: unknown): x is AskError {
  return (
    typeof x === "object" &&
    x !== null &&
    "error" in x &&
    typeof (x as { error: unknown }).error === "string"
  );
}

export function isRole(x: string): x is Role {
  return x === "public" || x === "engineering";
}
