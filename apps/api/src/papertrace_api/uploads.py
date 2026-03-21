from __future__ import annotations

import hashlib
import re
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, status
from papertrace_core.settings import Settings
from starlette.datastructures import UploadFile

FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(filename: str) -> str:
    normalized = FILENAME_SAFE_RE.sub("-", filename.strip()).strip("-._")
    return normalized or "paper"


async def persist_uploaded_pdf(upload: UploadFile, settings: Settings) -> str:
    filename = upload.filename or "paper.pdf"
    suffix = Path(filename).suffix.lower()
    if suffix != ".pdf":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded paper file must have a .pdf extension",
        )

    upload_dir = settings.local_data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    temp_path = upload_dir / f".upload-{uuid4().hex}.pdf"

    total_size = 0
    digest = hashlib.sha256()
    with temp_path.open("wb") as output:
        while chunk := await upload.read(1024 * 1024):
            total_size += len(chunk)
            if total_size > settings.paper_upload_max_bytes:
                output.close()
                temp_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Uploaded PDF exceeds PAPER_UPLOAD_MAX_BYTES",
                )
            digest.update(chunk)
            output.write(chunk)

    await upload.close()
    target_path = upload_dir / f"{sanitize_filename(Path(filename).stem)}-{digest.hexdigest()}.pdf"
    if target_path.exists():
        temp_path.unlink(missing_ok=True)
    else:
        temp_path.replace(target_path)
    return str(target_path)
