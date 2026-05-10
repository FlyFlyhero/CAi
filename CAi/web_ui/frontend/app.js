/**
 * CAi Copilot - Frontend Application
 * GPT-style chat interface with file management, preview, and PDF export.
 */

// ========== State ==========
const state = {
    messages: [],
    isStreaming: false,
    attachedFiles: [],       // File objects from local disk
    referencedFiles: [],     // filenames from workspace
    workspaceFiles: [],
    currentConvId: null,     // active conversation id
    conversations: [],       // conversation metadata list
};

// ========== DOM Elements ==========
const $ = (sel) => document.querySelector(sel);

const messagesContainer = $("#messagesContainer");
const messagesEl = $("#messages");
const welcomeScreen = $("#welcomeScreen");
const messageInput = $("#messageInput");
const sendBtn = $("#sendBtn");
const fileList = $("#fileList");
const attachedFilesEl = $("#attachedFiles");
const fileUploadInput = $("#fileUploadInput");
const chatFileInput = $("#chatFileInput");
const conversationListEl = $("#conversationList");

// ========== Initialization ==========
document.addEventListener("DOMContentLoaded", async () => {
    lucide.createIcons();
    setupEventListeners();
    loadFiles();
    await loadConversations();
});

function setupEventListeners() {
    sendBtn.addEventListener("click", sendMessage);
    messageInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    messageInput.addEventListener("input", () => {
        messageInput.style.height = "auto";
        messageInput.style.height = Math.min(messageInput.scrollHeight, 200) + "px";
        updateSendBtnState();
    });

    fileUploadInput.addEventListener("change", handleSidebarUpload);
    chatFileInput.addEventListener("change", handleChatFileAttach);

    $("#refreshFilesBtn").addEventListener("click", loadFiles);
    $("#exportPdfBtn").addEventListener("click", exportPdf);
    $("#clearFilesBtn").addEventListener("click", clearFiles);
    $("#newConvBtn").addEventListener("click", () => startNewConversation());
    $("#toggleSidebar").addEventListener("click", toggleSidebar);
    $("#collapseSidebarBtn").addEventListener("click", collapseSidebar);

    // Close modals on overlay click
    document.addEventListener("click", (e) => {
        if (e.target.classList.contains("modal-overlay")) {
            closeAllModals();
        }
    });
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") closeAllModals();
    });
}

function updateSendBtnState() {
    sendBtn.disabled = !messageInput.value.trim()
        && state.attachedFiles.length === 0
        && state.referencedFiles.length === 0;
}

// ========== Chat Logic ==========
async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text && state.attachedFiles.length === 0 && state.referencedFiles.length === 0) return;
    if (state.isStreaming) return;

    welcomeScreen.style.display = "none";

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
    messageInput.value = "";
    messageInput.style.height = "auto";
    sendBtn.disabled = true;
    state.attachedFiles = [];
    state.referencedFiles = [];
    attachedFilesEl.innerHTML = "";

    // Stream response
    state.isStreaming = true;
    const aiMsgEl = addMessage("assistant", "", true);

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
        let thinkingContent = "";
        let codeContent = "";
        let observationContent = "";
        let solutionContent = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";

            for (const line of lines) {
                if (!line.startsWith("data: ")) continue;
                const jsonStr = line.slice(6);
                if (!jsonStr) continue;

                try {
                    const event = JSON.parse(jsonStr);
                    switch (event.type) {
                        case "conversation_id":
                            state.currentConvId = event.content;
                            break;
                        case "thinking":
                            thinkingContent += (thinkingContent ? "\n" : "") + event.content;
                            break;
                        case "code":
                            codeContent += (codeContent ? "\n---\n" : "") + event.content;
                            break;
                        case "observation":
                            observationContent += (observationContent ? "\n---\n" : "") + event.content;
                            break;
                        case "solution":
                            solutionContent = event.content;
                            break;
                        case "error":
                            solutionContent = `❌ 错误: ${event.content}`;
                            break;
                        case "done":
                            break;
                    }
                    updateAIMessage(aiMsgEl, { thinking: thinkingContent, code: codeContent, observation: observationContent, solution: solutionContent });
                } catch (e) { /* skip */ }
            }
        }

        const indicator = aiMsgEl.querySelector(".streaming-indicator");
        if (indicator) indicator.remove();

    } catch (e) {
        updateAIMessage(aiMsgEl, { solution: `❌ 请求失败: ${e.message}` });
    }

    state.isStreaming = false;
    updateSendBtnState();
    loadFiles();
    loadConversations();  // Refresh sidebar to reflect updated title/timestamp
}

function addMessage(role, content, isStreaming = false) {
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

    messagesEl.appendChild(msgEl);
    scrollToBottom();
    return msgEl;
}

function updateAIMessage(msgEl, { thinking, code, observation, solution }) {
    const body = msgEl.querySelector(".message-body");
    let html = "";

    if (thinking) {
        html += buildCollapsible("🤔 思考过程", thinking, "thinking", false);
    }
    if (code) {
        html += buildCollapsible("💻 执行代码", `<pre><code>${escapeHtml(code)}</code></pre>`, "code", false, true);
    }
    if (observation) {
        html += buildCollapsible("📊 执行结果", `<pre><code>${escapeHtml(observation)}</code></pre>`, "observation", false, true);
    }
    if (solution) {
        html += `<div class="solution-content">${renderMarkdown(solution)}</div>`;
    }
    if (state.isStreaming && !solution) {
        html += '<div class="streaming-indicator"><span class="streaming-dot"></span><span class="streaming-dot"></span><span class="streaming-dot"></span></div>';
    }

    body.innerHTML = html;
    body.querySelectorAll("pre code").forEach((block) => {
        hljs.highlightElement(block);
    });
    scrollToBottom();
}

function buildCollapsible(title, content, type, defaultOpen = false, isRaw = false) {
    const openClass = defaultOpen ? "open" : "";
    const renderedContent = isRaw ? content : `<div>${escapeHtml(content).replace(/\n/g, "<br>")}</div>`;
    return `
        <div class="collapsible ${openClass}" onclick="this.classList.toggle('open')">
            <div class="collapsible-header">
                <span class="arrow">▶</span>
                <span>${title}</span>
            </div>
            <div class="collapsible-body">${renderedContent}</div>
        </div>
    `;
}

// ========== File Management ==========
async function loadFiles() {
    try {
        const res = await fetch("/api/files");
        const data = await res.json();
        state.workspaceFiles = data.files || [];
        renderFileList();
    } catch (e) {
        console.error("Failed to load files:", e);
    }
}

function renderFileList() {
    if (state.workspaceFiles.length === 0) {
        fileList.innerHTML = '<div class="file-empty">📂 暂无文件</div>';
        return;
    }

    fileList.innerHTML = state.workspaceFiles
        .map((f) => {
            const icon = getFileIcon(f.name);
            const size = formatFileSize(f.size);
            return `
                <div class="file-item">
                    <span class="file-icon">${icon}</span>
                    <span class="file-name" title="${escapeHtml(f.name)}" onclick="previewFile('${escapeHtml(f.name)}')">${escapeHtml(f.name)}</span>
                    <span class="file-size">${size}</span>
                    <span class="file-action file-ref-btn" onclick="event.stopPropagation();referenceFile('${escapeHtml(f.name)}')" title="引用到对话">📌</span>
                    <span class="file-action file-dl-btn" onclick="event.stopPropagation();downloadFile('${escapeHtml(f.name)}')" title="下载">⬇️</span>
                    <span class="file-action file-del-btn" onclick="event.stopPropagation();deleteFile('${escapeHtml(f.name)}')" title="删除">🗑️</span>
                </div>
            `;
        })
        .join("");
}

function referenceFile(filename) {
    if (state.referencedFiles.includes(filename)) {
        showToast("已引用该文件", "info");
        return;
    }
    state.referencedFiles.push(filename);
    renderAttachedFiles();
    updateSendBtnState();
    showToast(`已引用: ${filename}`, "success");
}

async function handleSidebarUpload(e) {
    const files = e.target.files;
    if (!files.length) return;

    const formData = new FormData();
    for (const file of files) {
        formData.append("files", file);
    }

    try {
        await fetch("/api/upload", { method: "POST", body: formData });
        showToast("文件上传成功", "success");
        loadFiles();
    } catch (e) {
        showToast("上传失败: " + e.message, "error");
    }
    e.target.value = "";
}

function handleChatFileAttach(e) {
    const files = Array.from(e.target.files);
    if (!files.length) return;

    // Show picker: upload new OR reference existing
    if (state.workspaceFiles.length > 0) {
        showFilePickerModal(files);
    } else {
        // No workspace files, just attach directly
        state.attachedFiles.push(...files);
        renderAttachedFiles();
        updateSendBtnState();
    }
    e.target.value = "";
}

function renderAttachedFiles() {
    const items = [
        ...state.attachedFiles.map((f, i) => `
            <div class="attached-file">
                <span>${getFileIcon(f.name)} ${f.name}</span>
                <span class="remove-file" onclick="removeAttachedFile(${i})">✕</span>
            </div>
        `),
        ...state.referencedFiles.map((name, i) => `
            <div class="attached-file attached-file-ref">
                <span>📌 ${name}</span>
                <span class="remove-file" onclick="removeReferencedFile(${i})">✕</span>
            </div>
        `),
    ];
    attachedFilesEl.innerHTML = items.join("");
}

function removeAttachedFile(index) {
    state.attachedFiles.splice(index, 1);
    renderAttachedFiles();
    updateSendBtnState();
}

function removeReferencedFile(index) {
    state.referencedFiles.splice(index, 1);
    renderAttachedFiles();
    updateSendBtnState();
}

async function downloadFile(filename) {
    window.open(`/api/files/${encodeURIComponent(filename)}`, "_blank");
}

async function deleteFile(filename) {
    if (!confirm(`确定删除 ${filename}？`)) return;
    try {
        await fetch(`/api/files/${encodeURIComponent(filename)}`, { method: "DELETE" });
        showToast("已删除", "info");
        loadFiles();
    } catch (e) {
        showToast("删除失败", "error");
    }
}

// ========== File Preview Modal ==========
function previewFile(filename) {
    const ext = filename.split(".").pop().toLowerCase();
    const fileUrl = `/api/files/${encodeURIComponent(filename)}?inline=1`;

    let contentHtml = "";

    const imageExts = ["png", "jpg", "jpeg", "gif", "bmp", "webp", "svg"];
    const textExts = ["txt", "csv", "py", "json", "sh", "yaml", "yml", "r", "log", "smi"];

    if (imageExts.includes(ext)) {
        contentHtml = `<img src="${fileUrl}" class="preview-image" alt="${escapeHtml(filename)}">`;
    } else if (ext === "pdf") {
        contentHtml = `<iframe src="${fileUrl}" class="preview-pdf"></iframe>`;
    } else if (ext === "md") {
        // Markdown: render as formatted HTML
        contentHtml = `<div class="preview-text-loading">加载中...</div>`;
        showPreviewModal(filename, contentHtml);
        fetch(fileUrl)
            .then(res => res.text())
            .then(text => {
                const container = $(".preview-body");
                if (container) {
                    container.innerHTML = `<div class="preview-markdown message-body">${renderMarkdown(text)}</div>`;
                    container.querySelectorAll("pre code").forEach(b => hljs.highlightElement(b));
                }
            })
            .catch(() => {
                const container = $(".preview-body");
                if (container) container.innerHTML = `<div class="preview-error">无法加载文件内容</div>`;
            });
        return;
    } else if (textExts.includes(ext)) {
        contentHtml = `<div class="preview-text-loading">加载中...</div>`;
        showPreviewModal(filename, contentHtml);
        fetch(fileUrl)
            .then(res => res.text())
            .then(text => {
                const previewText = text.length > 50000
                    ? text.slice(0, 50000) + "\n\n... (文件过长，仅展示前 50000 字符)"
                    : text;
                const container = $(".preview-body");
                if (container) {
                    container.innerHTML = `<pre class="preview-text-content"><code>${escapeHtml(previewText)}</code></pre>`;
                    container.querySelectorAll("pre code").forEach(b => hljs.highlightElement(b));
                }
            })
            .catch(() => {
                const container = $(".preview-body");
                if (container) container.innerHTML = `<div class="preview-error">无法加载文件内容</div>`;
            });
        return;
    } else {
        contentHtml = `
            <div class="preview-unsupported">
                <p>该文件类型不支持预览</p>
                <button class="btn btn-outline" onclick="downloadFile('${escapeHtml(filename)}')">⬇️ 下载文件</button>
            </div>
        `;
    }

    showPreviewModal(filename, contentHtml);
}

function showPreviewModal(filename, bodyHtml) {
    closeAllModals();
    const modal = document.createElement("div");
    modal.className = "modal-overlay";
    modal.id = "previewModal";
    modal.innerHTML = `
        <div class="modal-container">
            <div class="modal-header">
                <span class="modal-title">${getFileIcon(filename)} ${escapeHtml(filename)}</span>
                <div class="modal-actions">
                    <button class="btn btn-sm btn-outline" onclick="referenceFile('${escapeHtml(filename)}');closeAllModals();">📌 引用</button>
                    <button class="btn btn-sm btn-outline" onclick="downloadFile('${escapeHtml(filename)}')">⬇️ 下载</button>
                    <button class="btn-icon" onclick="closeAllModals()">✕</button>
                </div>
            </div>
            <div class="preview-body">${bodyHtml}</div>
        </div>
    `;
    document.body.appendChild(modal);
    requestAnimationFrame(() => modal.classList.add("visible"));
}

// ========== File Picker Modal (attach button) ==========
function showFilePickerModal(newFiles) {
    closeAllModals();

    // If user selected files from disk, add them directly
    state.attachedFiles.push(...newFiles);
    renderAttachedFiles();
    updateSendBtnState();

    // Also show workspace file picker for referencing
    if (state.workspaceFiles.length === 0) return;

    const modal = document.createElement("div");
    modal.className = "modal-overlay";
    modal.id = "filePickerModal";

    const fileItems = state.workspaceFiles.map(f => {
        const checked = state.referencedFiles.includes(f.name) ? "checked" : "";
        return `
            <label class="picker-item">
                <input type="checkbox" value="${escapeHtml(f.name)}" ${checked}>
                <span>${getFileIcon(f.name)} ${escapeHtml(f.name)}</span>
                <span class="picker-size">${formatFileSize(f.size)}</span>
            </label>
        `;
    }).join("");

    modal.innerHTML = `
        <div class="modal-container modal-sm">
            <div class="modal-header">
                <span class="modal-title">📌 引用工作区文件</span>
                <button class="btn-icon" onclick="closeAllModals()">✕</button>
            </div>
            <div class="picker-body">
                <p class="picker-hint">已上传 ${newFiles.length} 个新文件。还可以选择工作区已有文件作为引用：</p>
                <div class="picker-list">${fileItems}</div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-sm btn-outline" onclick="closeAllModals()">完成</button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
    requestAnimationFrame(() => modal.classList.add("visible"));

    // Handle checkbox changes
    modal.querySelectorAll("input[type=checkbox]").forEach(cb => {
        cb.addEventListener("change", () => {
            const name = cb.value;
            if (cb.checked && !state.referencedFiles.includes(name)) {
                state.referencedFiles.push(name);
            } else if (!cb.checked) {
                state.referencedFiles = state.referencedFiles.filter(n => n !== name);
            }
            renderAttachedFiles();
            updateSendBtnState();
        });
    });
}

function closeAllModals() {
    document.querySelectorAll(".modal-overlay").forEach(m => m.remove());
}

// ========== Actions ==========
async function exportPdf() {
    try {
        showToast("正在生成 PDF...", "info");
        const url = state.currentConvId
            ? `/api/export-pdf?conversation_id=${encodeURIComponent(state.currentConvId)}`
            : "/api/export-pdf";
        const res = await fetch(url, { method: "POST" });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || "Export failed");
        }
        const blob = await res.blob();
        const downloadUrl = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = downloadUrl;
        a.download = `conversation_${Date.now()}.pdf`;
        a.click();
        URL.revokeObjectURL(downloadUrl);
        showToast("PDF 导出成功", "success");
        loadFiles();
    } catch (e) {
        showToast("导出失败: " + e.message, "error");
    }
}

async function clearFiles() {
    if (!confirm("确定清空工作区所有文件？（会话记录会保留）")) return;
    try {
        await fetch("/api/workspace", { method: "DELETE" });
        loadFiles();
        showToast("已清空工作区文件", "info");
    } catch (e) {
        showToast("清空失败", "error");
    }
}

function toggleSidebar() {
    const sidebar = $("#sidebar");
    // On mobile: slide in/out
    sidebar.classList.toggle("open");
}

function collapseSidebar() {
    const sidebar = $("#sidebar");
    sidebar.classList.toggle("collapsed");
    // Save preference
    localStorage.setItem("cai-sidebar-collapsed", sidebar.classList.contains("collapsed"));
}

// Restore sidebar state on load
(function restoreSidebarState() {
    const collapsed = localStorage.getItem("cai-sidebar-collapsed") === "true";
    if (collapsed) {
        document.addEventListener("DOMContentLoaded", () => {
            $("#sidebar").classList.add("collapsed");
        });
    }
})();

// ========== Helpers ==========
function setPrompt(text) {
    messageInput.value = text;
    messageInput.focus();
    sendBtn.disabled = false;
    messageInput.style.height = "auto";
    messageInput.style.height = messageInput.scrollHeight + "px";
}

function scrollToBottom() {
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

function renderMarkdown(text) {
    try {
        return marked.parse(text);
    } catch {
        return escapeHtml(text).replace(/\n/g, "<br>");
    }
}

function getFileIcon(filename) {
    const ext = filename.split(".").pop().toLowerCase();
    const icons = {
        pdf: "📄", csv: "📊", xlsx: "📊", xls: "📊",
        png: "🖼️", jpg: "🖼️", jpeg: "🖼️", gif: "🖼️",
        py: "🐍", r: "📈", json: "📋", txt: "📝", md: "📝",
        sdf: "🧪", mol: "🧪", pdb: "🧬",
    };
    return icons[ext] || "📁";
}

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

// ========== Toast ==========
function showToast(message, type = "info") {
    let container = $(".toast-container");
    if (!container) {
        container = document.createElement("div");
        container.className = "toast-container";
        document.body.appendChild(container);
    }

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = "0";
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ========== Theme Toggle ==========
function initTheme() {
    const saved = localStorage.getItem("cai-theme");
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const theme = saved || (prefersDark ? "dark" : "light");
    applyTheme(theme);
}

function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("cai-theme", theme);

    // Switch highlight.js theme
    const hljsLink = document.getElementById("hljs-theme");
    if (hljsLink) {
        hljsLink.href = theme === "dark"
            ? "https://cdn.jsdelivr.net/npm/highlight.js@11/styles/github-dark.min.css"
            : "https://cdn.jsdelivr.net/npm/highlight.js@11/styles/github.min.css";
    }
}

function toggleTheme() {
    const current = document.documentElement.getAttribute("data-theme") || "dark";
    const next = current === "dark" ? "light" : "dark";
    applyTheme(next);
}

// Initialize theme on load
initTheme();

// Bind toggle button
document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("themeToggleBtn");
    if (btn) btn.addEventListener("click", toggleTheme);
});

// ========== Conversation Management ==========
async function loadConversations() {
    try {
        const res = await fetch("/api/conversations");
        const data = await res.json();
        state.conversations = data.conversations || [];
        renderConversationList();
    } catch (e) {
        console.error("Failed to load conversations:", e);
    }
}

function renderConversationList() {
    if (!conversationListEl) return;

    if (state.conversations.length === 0) {
        conversationListEl.innerHTML = '<div class="conv-empty">💬 暂无会话，点击 + 新建</div>';
        return;
    }

    conversationListEl.innerHTML = state.conversations
        .map((c) => {
            const active = c.id === state.currentConvId ? "active" : "";
            const title = escapeHtml(c.title || "新对话");
            const date = formatDate(c.updated_at);
            return `
                <div class="conversation-item ${active}" onclick="selectConversation('${c.id}')">
                    <span class="conv-title" title="${title}">${title}</span>
                    <div class="conv-actions">
                        <button class="conv-action-btn" onclick="event.stopPropagation();renameConversation('${c.id}')" title="重命名">✏️</button>
                        <button class="conv-action-btn danger" onclick="event.stopPropagation();removeConversation('${c.id}')" title="删除">🗑️</button>
                    </div>
                    <span class="conv-meta">${date}</span>
                </div>
            `;
        })
        .join("");
}

async function selectConversation(convId) {
    if (state.isStreaming) {
        showToast("请等待当前对话完成", "info");
        return;
    }
    try {
        const res = await fetch(`/api/conversations/${convId}`);
        if (!res.ok) throw new Error("加载失败");
        const conv = await res.json();

        state.currentConvId = convId;
        renderConversationList();

        // Clear and rebuild messages
        messagesEl.innerHTML = "";
        welcomeScreen.style.display = "none";

        const messages = conv.messages || [];
        if (messages.length === 0) {
            welcomeScreen.style.display = "flex";
        } else {
            for (const msg of messages) {
                if (msg.role === "user") {
                    addMessage("user", msg.content);
                } else if (msg.role === "assistant") {
                    const aiEl = addMessage("assistant", "", false);
                    updateAIMessage(aiEl, { solution: msg.content });
                }
            }
        }
    } catch (e) {
        showToast("无法加载会话: " + e.message, "error");
    }
}

async function startNewConversation() {
    if (state.isStreaming) {
        showToast("请等待当前对话完成", "info");
        return;
    }
    try {
        const res = await fetch("/api/conversations", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
        });
        const meta = await res.json();
        state.currentConvId = meta.id;

        messagesEl.innerHTML = "";
        welcomeScreen.style.display = "flex";

        await loadConversations();
        showToast("已创建新会话", "success");
    } catch (e) {
        showToast("创建失败: " + e.message, "error");
    }
}

async function renameConversation(convId) {
    const conv = state.conversations.find((c) => c.id === convId);
    if (!conv) return;
    const newTitle = prompt("重命名会话:", conv.title);
    if (!newTitle || newTitle === conv.title) return;
    try {
        await fetch(`/api/conversations/${convId}/title`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: newTitle }),
        });
        await loadConversations();
    } catch (e) {
        showToast("重命名失败", "error");
    }
}

async function removeConversation(convId) {
    if (!confirm("确定删除这个会话？此操作不可恢复。")) return;
    try {
        await fetch(`/api/conversations/${convId}`, { method: "DELETE" });
        if (convId === state.currentConvId) {
            state.currentConvId = null;
            messagesEl.innerHTML = "";
            welcomeScreen.style.display = "flex";
        }
        await loadConversations();
        showToast("已删除", "info");
    } catch (e) {
        showToast("删除失败", "error");
    }
}

function formatDate(iso) {
    if (!iso) return "";
    try {
        const d = new Date(iso);
        const now = new Date();
        const diffMs = now - d;
        const diffMin = Math.floor(diffMs / 60000);
        const diffHour = Math.floor(diffMs / 3600000);
        const diffDay = Math.floor(diffMs / 86400000);
        if (diffMin < 1) return "刚刚";
        if (diffMin < 60) return `${diffMin}分钟前`;
        if (diffHour < 24) return `${diffHour}小时前`;
        if (diffDay < 7) return `${diffDay}天前`;
        return `${d.getMonth() + 1}/${d.getDate()}`;
    } catch {
        return "";
    }
}
