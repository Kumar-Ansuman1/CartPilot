import { Bot, ShoppingBag, Sparkles } from "lucide-react";
import "./ModeSwitcher.css";

export default function ModeSwitcher({ mode }) {
  return (
    <nav className="mode-switch-shell" aria-label="Shopping mode">
      <div className="mode-switch-copy">
        <span className="mode-switch-kicker">CartPilot mode</span>
        <strong>
          {mode === "ai" ? "Delegated AI Buyer" : "Normal Shopping"}
        </strong>
      </div>

      <div className="mode-switch-control" role="group" aria-label="Choose shopping mode">
        <a
          className={`mode-option ${mode === "normal" ? "active" : ""}`}
          href="/"
          aria-current={mode === "normal" ? "page" : undefined}
        >
          <ShoppingBag size={16} />
          <span>
            <b>Normal</b>
            <small>You choose</small>
          </span>
        </a>

        <a
          className={`mode-option ${mode === "ai" ? "active" : ""}`}
          href="/ai-buyer"
          aria-current={mode === "ai" ? "page" : undefined}
        >
          {mode === "ai" ? <Sparkles size={16} /> : <Bot size={16} />}
          <span>
            <b>AI Buyer</b>
            <small>AI chooses</small>
          </span>
        </a>
      </div>
    </nav>
  );
}
