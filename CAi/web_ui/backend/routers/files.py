"""File upload, download, preview, delete and PDF export endpoints."""

from __future__ import annotations

import os
import re
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from CAi.logger import get_logger

from ..conversation_store import ConversationStore
from ..deps import get_store, get_workspace_dir

logger = get_logger("CAi.web_ui.files")

router = APIRouter(prefix="/api", tags=["files"])

_IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".tiff",
})

# Fallback MIME types for extensions that mimetypes.guess_type may not know.
_MIME_FALLBACKS = {
    ".pdf":  "application/pdf",
    ".json": "application/json",
    ".csv":  "text/csv",
    ".md":   "text/markdown",
    ".yaml": "text/yaml",
    ".yml":  "text/yaml",
    ".doc":  "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls":  "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".smi":  "chemical/x-daylight-smiles",
    ".sdf":  "chemical/x-mdl-sdfile",
    ".mol":  "chemical/x-mdl-molfile",
    ".mol2": "chemical/x-mol2",
    ".pdb":  "chemical/x-pdb",
    ".pdbqt":"chemical/x-pdbqt",
}


def _safe_path(filename: str, workspace_dir: str) -> str:
    """Resolve path and ensure it stays within the workspace directory."""
    resolved = os.path.realpath(os.path.join(workspace_dir, os.path.basename(filename)))
    if not resolved.startswith(os.path.realpath(workspace_dir)):
        raise HTTPException(status_code=400, detail="Invalid filename")
    return resolved


def _guess_media_type(filename: str) -> str:
    import mimetypes
    mt = mimetypes.guess_type(filename)[0]
    if mt:
        return mt
    ext = os.path.splitext(filename)[1].lower()
    return _MIME_FALLBACKS.get(ext, "application/octet-stream")


# ========== Upload / List / Download / Delete ==========


@router.post("/upload")
async def upload_files(
    files: list[UploadFile] = File(...),
    workspace_dir: str = Depends(get_workspace_dir),
):
    uploaded = []
    for file in files:
        safe_name = os.path.basename(file.filename or "upload")
        target = _safe_path(safe_name, workspace_dir)
        with open(target, "wb") as f:
            content = await file.read()
            f.write(content)
        uploaded.append(safe_name)
    return {"uploaded": uploaded}


@router.get("/files")
async def list_files(workspace_dir: str = Depends(get_workspace_dir)):
    if not os.path.exists(workspace_dir):
        return {"files": []}
    files = []
    for name in os.listdir(workspace_dir):
        fp = os.path.join(workspace_dir, name)
        if os.path.isfile(fp):
            stat = os.stat(fp)
            files.append({
                "name": name,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
    return {"files": files}


@router.get("/files/{filename}")
async def download_file(
    filename: str,
    inline: int = 0,
    workspace_dir: str = Depends(get_workspace_dir),
):
    fp = _safe_path(filename, workspace_dir)
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="File not found")

    media_type = _guess_media_type(filename)

    if inline:
        from starlette.responses import Response
        with open(fp, "rb") as f:
            content = f.read()
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )
    return FileResponse(fp, filename=filename, media_type=media_type)


@router.delete("/files/{filename}")
async def delete_file(
    filename: str,
    workspace_dir: str = Depends(get_workspace_dir),
):
    fp = _safe_path(filename, workspace_dir)
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="File not found")
    os.remove(fp)
    return {"deleted": filename}


# ========== PDF export ==========


@router.post("/export-pdf")
async def export_pdf(
    conversation_id: str | None = None,
    store: ConversationStore = Depends(get_store),
    workspace_dir: str = Depends(get_workspace_dir),
):
    from ..pdf_export import (
        EmptyConversation,
        PdfEngineUnavailable,
        export_conversation_to_pdf,
    )

    if conversation_id:
        conv = store.get_conversation(conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        recent = store.list_conversations()
        if not recent:
            raise HTTPException(status_code=404, detail="No conversations to export")
        conv = store.get_conversation(recent[0]["id"])
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = re.sub(r"[^\w\-]+", "_", conv.get("title") or "conversation").strip("_")
    filename = f"{safe_title}_{ts}.pdf" if safe_title else f"conversation_{ts}.pdf"
    out_path = os.path.join(workspace_dir, filename)

    try:
        export_conversation_to_pdf(conv, out_path, workspace_dir=workspace_dir)
    except EmptyConversation as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PdfEngineUnavailable as e:
        logger.error("PDF engine unavailable: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.exception("PDF export failed")
        raise HTTPException(status_code=500, detail=f"Export failed: {e}") from e

    return FileResponse(out_path, filename=filename, media_type="application/pdf")
