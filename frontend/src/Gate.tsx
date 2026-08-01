/**
 * Shared-secret gate for the hosted demo.
 *
 * Deployment concern. When `BIOLEAD_ACCESS_CODES` is unset the backend reports the gate as
 * disabled and this component renders nothing, so local development is unaffected and no
 * code path changes.
 *
 * The code is exchanged for an HttpOnly cookie set by the server. Nothing is stored in
 * localStorage and the token never reaches JavaScript, so an XSS in the page cannot read
 * it. `credentials: "include"` is required on the exchange and on every later fetch.
 */
import { useEffect, useRef, useState } from "react";
import { API } from "./apiBase";

type Status = "checking" | "open" | "locked" | "in";

export function Gate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>("checking");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch(`${API}/gate`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => setStatus(!d.enabled ? "open" : d.label ? "in" : "locked"))
      // A backend that cannot be reached is not an open door.
      .catch(() => setStatus("locked"));
  }, []);

  useEffect(() => {
    if (status === "locked") input.current?.focus();
  }, [status]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!code.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`${API}/gate`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      if (r.ok) {
        setStatus("in");
      } else if (r.status === 429) {
        const d = await r.json().catch(() => ({}));
        setError(`Too many attempts. Try again in ${d.retry_after ?? 60}s.`);
      } else {
        setError("That code was not recognised.");
        setCode("");
        input.current?.focus();
      }
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  if (status === "checking") return null;
  if (status === "open" || status === "in") return <>{children}</>;

  return (
    <div className="gate">
      <form className="gate-card" onSubmit={submit}>
        <h1 className="gate-title">BioLead</h1>
        <p className="gate-sub">Enter the access code to continue.</p>
        <input
          ref={input}
          className="gate-input"
          type="password"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          aria-label="Access code"
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? "gate-error" : undefined}
        />
        <button className="gate-go" type="submit" disabled={busy || !code.trim()}>
          {busy ? "Checking" : "Enter"}
        </button>
        {error && (
          <p className="gate-error" id="gate-error" role="alert">
            {error}
          </p>
        )}
      </form>
    </div>
  );
}
