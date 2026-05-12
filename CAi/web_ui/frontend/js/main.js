/**
 * Application entry point.
 * Initializes DOM refs, binds events, and kicks off data loading.
 */
import { $, state, dom, initDomRefs, safeCreateIcons, initTheme, toggleTheme, updateSendBtnState } from "./state.js";
import { sendMessage, cancelGeneration } from "./chat.js";
import { loadFiles, handleSidebarUpload, handleChatFileAttach, exportPdf, clearFiles, closeAllModals } from "./files.js";
import { loadConversations, startNewConversation } from "./conversations.js";

// Initialize theme immediately (before DOMContentLoaded) to avoid flash
initTheme();

// ========== Boot ==========
document.addEventListener("DOMContentLoaded", async () => {
    initDomRefs();
    safeCreateIcons();
    setupEventListeners();
    restoreSidebarState();
    loadFiles();
    await loadConversations();
});

// ========== Event Binding ==========
function setupEventListeners() {
    // Send button handles both send and cancel depending on streaming state
    dom.sendBtn.addEventListener("click", () => {
        if (state.isStreaming) {
            cancelGeneration();
        } else {
            sendMessage();
        }
    });

    dom.messageInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    dom.messageInput.addEventListener("input", () => {
        dom.messageInput.style.height = "auto";
        dom.messageInput.style.height = Math.min(dom.messageInput.scrollHeight, 200) + "px";
        updateSendBtnState();
    });

    dom.fileUploadInput.addEventListener("change", handleSidebarUpload);
    dom.chatFileInput.addEventListener("change", handleChatFileAttach);

    $("#refreshFilesBtn").addEventListener("click", loadFiles);
    $("#exportPdfBtn").addEventListener("click", exportPdf);
    $("#clearFilesBtn").addEventListener("click", clearFiles);
    $("#newConvBtn").addEventListener("click", () => startNewConversation());
    $("#toggleSidebar").addEventListener("click", toggleSidebar);
    $("#collapseSidebarBtn").addEventListener("click", collapseSidebar);

    const themeBtn = document.getElementById("themeToggleBtn");
    if (themeBtn) themeBtn.addEventListener("click", toggleTheme);

    // Close modals on overlay click
    document.addEventListener("click", (e) => {
        if (e.target.classList.contains("modal-overlay")) {
            closeAllModals();
        }
    });
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
            if (dom.sendBtn.classList.contains("btn-stop")) {
                cancelGeneration();
            } else {
                closeAllModals();
            }
        }
    });
}

// ========== Sidebar ==========
function toggleSidebar() {
    $("#sidebar").classList.toggle("open");
}

function collapseSidebar() {
    const sidebar = $("#sidebar");
    sidebar.classList.toggle("collapsed");
    localStorage.setItem("cai-sidebar-collapsed", sidebar.classList.contains("collapsed"));
}

function restoreSidebarState() {
    if (localStorage.getItem("cai-sidebar-collapsed") === "true") {
        $("#sidebar").classList.add("collapsed");
    }
}

// ========== Welcome Hints ==========
// Expose setPrompt globally for the onclick handlers in index.html
window.setPrompt = function (text) {
    dom.messageInput.value = text;
    dom.messageInput.focus();
    dom.sendBtn.disabled = false;
    dom.messageInput.style.height = "auto";
    dom.messageInput.style.height = dom.messageInput.scrollHeight + "px";
};
