/**
 * Chat logic: sending messages, streaming SSE, rendering message bubbles.
 */
import {
    state, dom,
    escapeHtml, renderMarkdown, safeHighlight, scrollToBottom, showToast,
    updateSendBtnState,
} from "./state.js";
import { loadFiles } from "./files.js";
import { loadConversations } from "./conversations.js";

// ========== Cancel ==========

export async function cancelGeneration() {
    if (!state.isStreaming) return;
    try {
        const url = state.currentConvId
            ? `/api/chat/cancel?conversation_id=${encodeURIComponent(state.currentConvId)}`
            : "/api/chat/cancel";
        await fetch(url, { method: "POST" });
    } catch (_) { /* best effort */ }
    state.isStreaming = false;
    updateSendBtnState();
    showToast("已停止生成", "info");
}

// ========== Send Message ==========

export async function sendMessage() {
    const text = dom.messageInput.value.trim();
    if (!text && state.attachedFiles.length === 0 && state.referencedFiles.length === 0) return;
    if (state.isStreaming) return;

    dom.welcomeScreen.style.display = "none";

    // Build display
    let displayText = text;
    const allFileNames = [
        ...state.attachedFiles.map(f => f.name),
        ...state.referencedFiles,
    ];
    if (allFileNames.length > 0) {
        displayText += `\n\n📎 ${allFileNames.join(", ")}`;
    }

    addMessage("user", displayText);

    // Upload new files
    const fileRefs = [...state.referencedFiles];
    if (state.attachedFiles.length > 0) {
        try {
            const formData = new FormData();
            for (const file of state.attachedFiles) {
                formData.append("files", file);
            }
            const res = await fetch("/api/upload", { method: "POST", body: formData });
            const data = await res.json();
            fileRefs.push(...(data.uploaded || []));
        } catch (e) {
            showToast("文件上传失败: " + e.message, "error");
        }
    }

    // Clear input
    dom.messageInput.value = "";
    dom.messageInput.style.height = "auto";
    dom.sendBtn.disabled = true;
    state.attachedFiles = [];
    state.referencedFiles = [];
    dom.attachedFilesEl.innerHTML = "";

    // Stream response
    state.isStreaming = true;
    updateSendBtnState();
    const aiMsgEl = addMessage("assistant", "", true);

    let fullContent = "";
    let currentTurnText = "";

    try {
        const response = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                message: text,
                file_refs: fileRefs,
                conversation_id: state.currentConvId,
            }),
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let streamDone = false;

        const handleEvent = (event) => {
            switch (event.type) {
                case "conversation_id":
                    state.currentConvId = event.content;
                    break;
                case "token":
                    currentTurnText += event.content;
                    fullContent += event.content;
                    renderStreamingMessage(aiMsgEl, fullContent, true);
                    break;
                case "message_end": {
                    const beforeTurn = fullContent.slice(0, fullContent.length - currentTurnText.length);
                    fullContent = beforeTurn + event.content;
                    currentTurnText = "";
                    renderStreamingMessage(aiMsgEl, fullContent, true);
                    break;
                }
                case "observation":
                    fullContent += "\n" + event.content;
                    currentTurnText = "";
                    renderStreamingMessage(aiMsgEl, fullContent, true);
                    break;
                case "solution":
                    if (!fullContent.trim()) {
                        fullContent = event.content;
                        renderStreamingMessage(aiMsgEl, fullContent, true);
                    }
                    break;
                case "error":
                    fullContent += `\n\n❌ 错误: ${event.content}`;
                    renderStreamingMessage(aiMsgEl, fullContent, true);
                    break;
                case "done":
                    streamDone = true;
                    break;
            }
        };

        while (!streamDone) {
            const { done, value } = await reader.read();
            if (done) {
                if (buffer.trim()) {
                    for (const line of buffer.split("\n")) {
                        if (!line.startsWith("data: ")) continue;
                        const j = line.slice(6).trim();
                        if (!j) continue;
                        try { handleEvent(JSON.parse(j)); } catch (_) { /* skip */ }
                    }
                }
                break;
            }

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";

            for (const line of lines) {
                if (!line.startsWith("data: ")) continue;
                const j = line.slice(6).trim();
                if (!j) continue;
                try { handleEvent(JSON.parse(j)); } catch (_) { /* skip */ }
                if (streamDone) break;
            }
        }

        renderStreamingMessage(aiMsgEl, fullContent || "(无响应)", false);

    } catch (e) {
        renderStreamingMessage(aiMsgEl, `❌ 请求失败: ${e.message}`, false);
    } finally {
        state.isStreaming = false;
        // Final render with streaming=false to ensure dots are removed
        renderStreamingMessage(aiMsgEl, fullContent || "(无响应)", false);
        updateSendBtnState();
        loadFiles();
        loadConversations();
    }
}

// ========== Message Rendering ==========

export function addMessage(role, content, isStreaming = false) {
    const msgEl = document.createElement("div");
    msgEl.className = `message message-${role}`;

    if (role === "user") {
        msgEl.innerHTML = `
            <div class="message-content">
                <div class="message-text">${escapeHtml(content).replace(/\n/g, "<br>")}</div>
            </div>
        `;
    } else {
        msgEl.innerHTML = `
            <div class="message-header">
                <div class="message-avatar">🧬</div>
                <span class="message-role">CAi</span>
            </div>
            <div class="message-body">
                ${isStreaming ? '<div class="streaming-indicator"><span class="streaming-dot"></span><span class="streaming-dot"></span><span class="streaming-dot"></span></div>' : ""}
            </div>
        `;
    }

    dom.messagesEl.appendChild(msgEl);
    scrollToBottom();
    return msgEl;
}

export function renderStreamingMessage(msgEl, fullContent, isStreaming = false) {
    const body = msgEl.querySelector(".message-body");
    const segments = parseSegments(fullContent);
    let html = "";

    segments.forEach((seg, i) => {
        const isLast = i === segments.length - 1;
        const inProgress = isStreaming && isLast;
        const statusOK = " ✓";
        const statusRun = " ⏳";

        if (seg.type === "text") {
            if (seg.content.trim()) {
                html += `<div class="solution-content">${renderMarkdown(seg.content)}</div>`;
            }
        } else if (seg.type === "code") {
            const status = inProgress ? `${statusRun} 执行中...` : statusOK;
            html += buildCollapsible(
                `💻 执行代码${status}`,
                `<pre><code>${escapeHtml(seg.content)}</code></pre>`,
                "code",
                inProgress,
                true,
            );
        } else if (seg.type === "observation") {
            // Extract image paths and render them as <img> tags
            const obsContent = seg.content;
            const imageLines = [];
            const textLines = [];
            for (const line of obsContent.split("\n")) {
                const imgMatch = line.match(/\[Image saved\]:\s*(.+)/);
                if (imgMatch) {
                    const filename = imgMatch[1].trim().split(/[/\\]/).pop();
                    imageLines.push(
                        `<div class="plot-preview"><img src="/api/files/${encodeURIComponent(filename)}?inline=1" alt="${escapeHtml(filename)}"></div>`
                    );
                } else {
                    textLines.push(line);
                }
            }
            let obsHtml = `<pre><code>${escapeHtml(textLines.join("\n"))}</code></pre>`;
            if (imageLines.length) {
                obsHtml += imageLines.join("");
            }
            html += buildCollapsible(
                `📊 执行结果${statusOK}`,
                obsHtml,
                "observation",
                inProgress,
                true,
            );
        }
    });

    if (isStreaming) {
        html += '<div class="streaming-indicator"><span class="streaming-dot"></span><span class="streaming-dot"></span><span class="streaming-dot"></span></div>';
    }

    if (!html.trim()) {
        html = isStreaming
            ? '<div class="streaming-indicator"><span class="streaming-dot"></span><span class="streaming-dot"></span><span class="streaming-dot"></span></div>'
            : '<div class="solution-content"><em>(无响应)</em></div>';
    }

    body.innerHTML = html;
    body.querySelectorAll("pre code").forEach((block) => safeHighlight(block));
    scrollToBottom();
}

// ========== Parsing ==========

export function parseSegments(content) {
    const segs = [];
    const src = content.replace(/<done\s*\/?>/g, "");
    const tagRe = /<(execute|observation)>/g;
    let lastIndex = 0;
    let m;

    while ((m = tagRe.exec(src)) !== null) {
        if (m.index > lastIndex) {
            segs.push({ type: "text", content: src.slice(lastIndex, m.index) });
        }
        const tagName = m[1];
        const closeTag = `</${tagName}>`;
        const closeIdx = src.indexOf(closeTag, tagRe.lastIndex);
        if (closeIdx === -1) {
            segs.push({
                type: tagName === "execute" ? "code" : "observation",
                content: src.slice(tagRe.lastIndex),
            });
            lastIndex = src.length;
            break;
        } else {
            segs.push({
                type: tagName === "execute" ? "code" : "observation",
                content: src.slice(tagRe.lastIndex, closeIdx),
            });
            lastIndex = closeIdx + closeTag.length;
            tagRe.lastIndex = lastIndex;
        }
    }

    if (lastIndex < src.length) {
        segs.push({ type: "text", content: src.slice(lastIndex) });
    }

    return segs;
}

// Legacy — used by conversation hydration
export function updateAIMessage(msgEl, { thinking, code, observation, solution }) {
    const parts = [];
    if (thinking) parts.push(thinking);
    if (code) parts.push(`<execute>${code}</execute>`);
    if (observation) parts.push(`<observation>${observation}</observation>`);
    if (solution) parts.push(solution);
    renderStreamingMessage(msgEl, parts.join("\n"), false);
}

function buildCollapsible(title, content, _type, defaultOpen = false, isRaw = false) {
    const openClass = defaultOpen ? "open" : "";
    const renderedContent = isRaw ? content : `<div>${escapeHtml(content).replace(/\n/g, "<br>")}</div>`;
    return `
        <div class="collapsible ${openClass}">
            <div class="collapsible-header" onclick="event.stopPropagation(); this.parentElement.classList.toggle('open')">
                <span class="arrow">▶</span>
                <span>${title}</span>
            </div>
            <div class="collapsible-body">${renderedContent}</div>
        </div>
    `;
}
