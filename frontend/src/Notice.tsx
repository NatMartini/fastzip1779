import { X } from "lucide-react";

export function Notice({ text, clear }: { text: string; clear: () => void }) {
  return (
    <div className="notice" role="alert">
      <span>{text}</span>
      <button aria-label="Dismiss message" onClick={clear}>
        <X size={17} />
      </button>
    </div>
  );
}
