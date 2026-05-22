/**
 * Utilities panel — agent's self-learned skill library.
 *
 * Surfaces utilities that the agent has accumulated through use, in
 * chemist-friendly language. Code is hidden by default behind a
 * "技术细节" disclosure.
 *
 * Public exports:
 *   loadUtilities()  — fetch + render counter on the trigger button
 *   openPanel()      — open the grid modal
 */

import {
    state, dom,
    escapeHtml, safeHighlight, showToast,
} from "./state.js?v=7";

// ========== Status presentation ==========
// Mirrors backend `_compute_status`. The backend is the source of truth
// for which bucket a utility falls into; this map only handles display.

const STATUS_PRESENTATION = {
    healthy:  { dot: "🟢", label: "成熟",   tone: "ok"  },
    trial:    { dot: "🟡", label: "试用中", tone: "mid" },
    unstable: { dot: "🔴", label: "不稳定", tone: "bad" },
    new:      { dot: "⚪", label: "新学的", tone: "new" },
};

// Friendly labels for "how often used" — chemists prefer words to numbers.
function maturityLabel(callCount) {
    if (callCount === 0) return "🌱 刚学到的能力";
    if (callCount < 3) return "🌱 新学的能力";
    if (callCount < 10) return "📈 试用中";
    if (callCount < 50) return "✓ 成熟";
    return "⭐ 常用能力";
}

// ========== State (panel-local) ==========

let _cachedList = [];

// ========== Public API ==========

export async function loadUtilities() {
    try {
        const res = await fetch("/api/utilities/list");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        _cachedList = data.utilities || [];
        _updateTriggerCount(data.total ?? _cachedList.length);
    } catch (err) {
        // Silent fail — utilities are optional. Just hide the trigger.
        _updateTriggerCount(null);
    }
}

export function openPanel() {
    _renderPanel();
}

// ========== Trigger button counter ==========

function _updateTriggerCount(n) {
    const btn = document.getElementById("utilitiesBtn");
    if (!btn) return;

    const countEl = btn.querySelector(".util-count");
    if (n === null || n === undefined) {
        btn.style.display = "none";
        return;
    }
    btn.style.display = "";
    if (countEl) countEl.textContent = String(n);
}

// ========== Grid panel modal ==========

function _renderPanel() {
    _closePanel();

    const modal = document.createElement("div");
    modal.className = "modal-overlay util-panel-overlay";
    modal.id = "utilitiesPanel";

    const total = _cachedList.length;
    const cardsHtml = total
        ? _cachedList.map(_renderCard).join("")
        : `<div class="util-empty">
              <div class="util-empty-icon">🌱</div>
              <p>暂无已学能力</p>
              <p class="util-empty-hint">执行几次代码后，agent 会自动从中提炼可复用的能力到这里。</p>
           </div>`;

    modal.innerHTML = `
        <div class="modal-container util-panel-container">
            <div class="modal-header">
                <span class="modal-title">🧠 已学能力 <span class="util-total">(${total})</span></span>
                <div class="modal-actions">
                    <button class="btn-icon util-panel-close" title="关闭">✕</button>
                </div>
            </div>
            <div class="util-panel-body">
                <p class="util-panel-hint">这些是 agent 在与你协作过程中自动学会、并可重复使用的能力。</p>
                <div class="util-grid">${cardsHtml}</div>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
    requestAnimationFrame(() => modal.classList.add("visible"));

    modal.querySelector(".util-panel-close").addEventListener("click", _closePanel);
    modal.addEventListener("click", (e) => {
        if (e.target === modal) _closePanel();
    });

    modal.querySelectorAll(".util-card").forEach((card) => {
        card.addEventListener("click", () => {
            const name = card.dataset.name;
            if (name) _openDetail(name);
        });
    });
}

function _renderCard(util) {
    const presentation = STATUS_PRESENTATION[util.status] || STATUS_PRESENTATION.new;
    const desc = util.description || "（未提供描述）";
    const lastUsed = _formatRelativeTime(util.last_used);

    return `
        <div class="util-card util-card-${presentation.tone}" data-name="${escapeHtml(util.name)}" tabindex="0">
            <div class="util-card-header">
                <span class="util-status-dot" title="${presentation.label}">${presentation.dot}</span>
                <h3 class="util-card-title">${escapeHtml(util.description || util.name)}</h3>
            </div>
            <div class="util-card-meta">
                <span class="util-card-uses">${maturityLabel(util.call_count)}</span>
                ${lastUsed ? `<span class="util-card-time">· ${escapeHtml(lastUsed)}</span>` : ""}
            </div>
            <div class="util-card-name">${escapeHtml(util.name)}</div>
        </div>
    `;
}

function _closePanel() {
    document.querySelectorAll(".util-panel-overlay, .util-detail-overlay").forEach((m) => m.remove());
}

// ========== Detail modal ==========

async function _openDetail(name) {
    let detail;
    try {
        const res = await fetch(`/api/utilities/detail/${encodeURIComponent(name)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        detail = await res.json();
    } catch (err) {
        showToast("加载失败: " + err.message, "error");
        return;
    }

    const modal = document.createElement("div");
    modal.className = "modal-overlay util-detail-overlay";
    modal.id = "utilityDetail";

    const presentation = STATUS_PRESENTATION[detail.status] || STATUS_PRESENTATION.new;
    const usagePct = detail.call_count
        ? Math.round((detail.success_count / detail.call_count) * 100)
        : 0;
    const usageBar = detail.call_count > 0
        ? `<div class="util-usage-bar">
              <div class="util-usage-fill" style="width:${usagePct}%"></div>
           </div>
           <div class="util-usage-text">
              已成功用于 <strong>${detail.success_count}</strong> / ${detail.call_count} 次任务
           </div>`
        : `<p class="util-usage-empty">这个能力还没被使用过。</p>`;

    const warning = detail.status === "unstable"
        ? `<div class="util-warning">⚠ 此能力近期失败较多，agent 下次会优先尝试其他方法。</div>`
        : "";

    modal.innerHTML = `
        <div class="modal-container util-detail-container">
            <div class="modal-header">
                <span class="modal-title">
                    <span class="util-status-dot">${presentation.dot}</span>
                    ${escapeHtml(detail.description || detail.name)}
                </span>
                <button class="btn-icon util-detail-close" title="关闭">✕</button>
            </div>
            <div class="util-detail-body">
                ${warning}

                <section class="util-section">
                    <h4 class="util-section-title">这个能力做什么</h4>
                    <p class="util-section-text">${escapeHtml(detail.description || "（未提供描述）")}</p>
                </section>

                <section class="util-section">
                    <h4 class="util-section-title">使用情况</h4>
                    <div class="util-section-text">
                        <p class="util-maturity">${maturityLabel(detail.call_count)}</p>
                        ${usageBar}
                        ${detail.last_used
                            ? `<p class="util-last-used">最近使用: ${escapeHtml(_formatRelativeTime(detail.last_used) || detail.last_used)}</p>`
                            : ""}
                    </div>
                </section>

                <section class="util-section">
                    <details class="util-tech">
                        <summary>▶ 查看技术细节（开发者）</summary>
                        <div class="util-tech-body">
                            <p class="util-tech-name">函数名: <code>${escapeHtml(detail.name)}</code></p>
                            <pre><code class="language-python">${escapeHtml(detail.code || "")}</code></pre>
                        </div>
                    </details>
                </section>
            </div>
            <div class="modal-footer util-detail-footer">
                <button class="btn btn-sm btn-outline btn-ghost-danger util-delete-btn">
                    🗑️ 不再使用此能力
                </button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
    requestAnimationFrame(() => modal.classList.add("visible"));

    modal.querySelectorAll("pre code").forEach((b) => safeHighlight(b));

    modal.querySelector(".util-detail-close").addEventListener("click", () => modal.remove());
    modal.addEventListener("click", (e) => {
        if (e.target === modal) modal.remove();
    });
    modal.querySelector(".util-delete-btn").addEventListener("click", () => _confirmDelete(detail.name));
}

async function _confirmDelete(name) {
    if (!confirm(`确定不再使用「${name}」？\n该能力会被移除，agent 之后不会再用它。\n下次遇到类似任务时，agent 可能会重新学一个。`)) {
        return;
    }
    try {
        const res = await fetch(`/api/utilities/detail/${encodeURIComponent(name)}`, { method: "DELETE" });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        showToast("已移除", "success");
        _closePanel();
        await loadUtilities();
        // Re-open the panel so the user sees the updated list
        openPanel();
    } catch (err) {
        showToast("移除失败: " + err.message, "error");
    }
}

// ========== Time formatting ==========

function _formatRelativeTime(iso) {
    if (!iso) return "";
    try {
        const then = new Date(iso);
        const now = new Date();
        const diffMs = now - then;
        const diffMin = Math.floor(diffMs / 60000);
        const diffHour = Math.floor(diffMs / 3600000);
        const diffDay = Math.floor(diffMs / 86400000);
        if (diffMin < 1) return "刚刚使用";
        if (diffMin < 60) return `${diffMin} 分钟前`;
        if (diffHour < 24) return `${diffHour} 小时前`;
        if (diffDay < 7) return `${diffDay} 天前`;
        return `${then.getMonth() + 1}/${then.getDate()}`;
    } catch {
        return "";
    }
}
