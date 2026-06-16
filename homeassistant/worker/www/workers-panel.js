// Home Assistant Workers Panel
// Registered as panel_custom at /config/workers

class WorkersPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    this._hass = hass;
    this._loadWorkers();
  }

  async _loadWorkers() {
    if (!this._hass) return;
    try {
      const result = await this._hass.callWS({ type: "workers/list" });
      this._render(result.workers);
    } catch (err) {
      this._renderError(err);
    }
  }

  _render(workers) {
    const statusColor = (s) =>
      ({
        running: "#4caf50",
        unavailable: "#f44336",
        not_implemented: "#9e9e9e",
      }[s] || "#9e9e9e");

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          padding: 16px;
          font-family: var(--primary-font-family, sans-serif);
        }
        h1 {
          font-size: 1.5em;
          margin-bottom: 16px;
          color: var(--primary-text-color);
        }
        table {
          width: 100%;
          border-collapse: collapse;
          background: var(--card-background-color);
          border-radius: 8px;
          overflow: hidden;
        }
        th {
          text-align: left;
          padding: 12px 16px;
          background: var(--secondary-background-color);
          color: var(--secondary-text-color);
          font-size: 0.85em;
          text-transform: uppercase;
        }
        td {
          padding: 12px 16px;
          border-top: 1px solid var(--divider-color);
          color: var(--primary-text-color);
        }
        .status {
          display: inline-flex;
          align-items: center;
          gap: 6px;
        }
        .dot {
          width: 10px;
          height: 10px;
          border-radius: 50%;
        }
        .capacity {
          font-size: 0.9em;
          color: var(--secondary-text-color);
        }
        .empty {
          padding: 32px 16px;
          text-align: center;
          color: var(--secondary-text-color);
        }
      </style>
      <h1>Workers</h1>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Type</th>
            <th>Address</th>
            <th>Status</th>
            <th>Integrations</th>
          </tr>
        </thead>
        <tbody>
          ${
            workers.length === 0
              ? `<tr><td colspan="5" class="empty">No workers declared.</td></tr>`
              : workers
                  .map(
                    (w) => `
            <tr>
              <td><strong>${w.name}</strong></td>
              <td>${w.type}</td>
              <td><code>${w.address || "\u2014"}</code></td>
              <td>
                <span class="status">
                  <span class="dot" style="background:${statusColor(w.status)}"></span>
                  ${w.status}
                </span>
              </td>
              <td class="capacity">
                ${w.active_integrations} / ${w.max_integrations != null ? w.max_integrations : "\u221e"}
              </td>
            </tr>
          `
                  )
                  .join("")
          }
        </tbody>
      </table>
    `;
  }

  _renderError(err) {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; padding: 16px; }
        .error { color: var(--error-color, red); padding: 16px; }
      </style>
      <div class="error">Error loading workers: ${err}</div>
    `;
  }
}

customElements.define("workers-panel", WorkersPanel);
