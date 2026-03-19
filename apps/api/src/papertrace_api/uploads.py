from __future__ import annotations

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
    target_path = upload_dir / f"{sanitize_filename(Path(filename).stem)}-{uuid4().hex[:10]}.pdf"

    total_size = 0
    with target_path.open("wb") as output:
        while chunk := await upload.read(1024 * 1024):
            total_size += len(chunk)
            if total_size > settings.paper_upload_max_bytes:
                output.close()
                target_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Uploaded PDF exceeds PAPER_UPLOAD_MAX_BYTES",
                )
            output.write(chunk)

    await upload.close()
    return str(target_path)
