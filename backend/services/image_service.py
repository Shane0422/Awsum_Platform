"""Shared canonical entity images with compensating filesystem rollback around DB commit."""
import logging
import os
import errno
from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile
import time
from collections.abc import Iterator

from fastapi import HTTPException

from backend.services import images
from backend.services.paths import AwsumPathResolver
from backend.services.resource_storage import ensure_resource_directory


logger = logging.getLogger(__name__)


@contextmanager
def image_operation_lock(owner: AwsumPathResolver, category: str) -> Iterator[None]:
    # SQL transaction locks end at rollback/failed commit. Hold the file lock
    # through compensation as well, so another worker cannot overwrite recovery.
    logical = owner.get_image_path(category, "operation.png")
    directory = ensure_resource_directory(logical, root=images.image_storage_root())
    path = directory / ".master-image.lock"
    try:
        stream = path.open("a+b")
    except OSError as exc:
        logger.exception("Could not open entity image operation lock.")
        raise HTTPException(500, "Image storage is unavailable. No changes were saved.") from exc
    with stream:
        if path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + 10
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    logger.exception("Could not lock entity image storage.")
                    raise HTTPException(500, "Image storage cannot be locked. No changes were saved.") from exc
                if time.monotonic() >= deadline:
                    raise HTTPException(409, "Another image save is in progress. Please try again.") from exc
                time.sleep(0.05)
        # Closing the descriptor releases the OS lock, even if recovery raises.
        yield


def master_image_lock():
    return image_operation_lock(AwsumPathResolver("PLATFORM"), "master")


class CanonicalImageChange:
    """Caller holds its owner/record lock until commit or rollback and finish()."""

    def __init__(self, owner: AwsumPathResolver, code: str | int, previous: str | None, data: bytes | None,
                 *, category: str = "master"):
        path_for = lambda extension: owner.get_entity_image_path(category, code, extension)
        canonical = path_for(images.IMAGE_CONFIG["format"])
        self.category = category
        self.reference = canonical if data is not None else None
        self.target = images.physical_image_path(images.ImagePath.parse(self.reference)) if self.reference else None
        self.previous = None
        if previous:
            try:
                parsed = images.ImagePath.parse(previous) if not previous.startswith("/static/") else images.legacy_image_path(previous)
            except ValueError as exc:
                raise HTTPException(409, "Existing image reference is invalid. Contact an administrator before replacement.") from exc
            if parsed is None:
                if category != "logo" or images.legacy_physical_path(previous) is None:
                    raise HTTPException(409, "Existing image reference requires reviewed migration before replacement.")
                logger.info("Preserving legacy shared logo during owner-specific replacement: %s", previous)
            else:
                expected = path_for(Path(parsed.file_name).suffix.lstrip("."))
                same_owner = parsed.owner == owner and parsed.image_category == category
                if not same_owner or (category != "logo" and parsed.logical_path != expected):
                    raise HTTPException(409, "Image reference does not match its owner and permanent identifier.")
                previous_file = images.physical_image_path(parsed)
                if previous_file.is_file():
                    self.previous = previous_file
                else:
                    logger.warning("Repairing missing entity image reference during an authorized save: %s", previous)
        self.data = data
        self.backup: Path | None = None
        self.staged: Path | None = None
        self.applied = False

    def _temporary(self, parent: Path) -> Path:
        descriptor, name = tempfile.mkstemp(prefix=".master-image-", suffix=".tmp", dir=parent)
        os.close(descriptor)
        return Path(name)

    def apply(self) -> None:
        try:
            if self.target and self.target.exists() and self.target != self.previous:
                raise HTTPException(409, "The canonical image filename is already in use; no file was overwritten.")
            if self.target and self.data is not None and self.reference is not None:
                ensure_resource_directory(self.reference, root=images.image_storage_root())
                self.staged = self._temporary(self.target.parent)
                with self.staged.open("wb") as stream:
                    stream.write(self.data)
                    stream.flush()
                    os.fsync(stream.fileno())
                if self.staged.read_bytes() != self.data:
                    raise OSError("Staged image verification failed.")
            if self.previous:
                self.backup = self._temporary(self.previous.parent)
                shutil.copyfile(self.previous, self.backup)
            if self.target:
                if self.target == self.previous:
                    os.replace(self.staged, self.target)
                else:
                    # Publish complete bytes, refusing a concurrent create rather than overwriting it.
                    os.link(self.staged, self.target)
                self.applied = True
        except OSError as exc:
            logger.exception("Could not stage canonical entity image.")
            raise HTTPException(500, "Image could not be saved. No record changes were committed.") from exc

    def rollback(self) -> None:
        if not self.applied:
            return
        try:
            if self.previous and self.backup:
                os.replace(self.backup, self.previous)
                self.backup = None
            if self.target and self.target != self.previous:
                self.target.unlink(missing_ok=True)
            self.applied = False
        except OSError:
            logger.critical("Image rollback failed; restore %s from %s before retrying.", self.previous, self.backup,
                            exc_info=True)
            # Keep the backup for manual recovery; never hide a failed compensation.
            raise

    def finish(self, *, committed: bool = False) -> str | None:
        warning = None
        obsolete = self.previous if committed and self.previous != self.target else None
        for path in (obsolete, self.staged, self.backup):
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    warning = ("Changes committed, but an obsolete or temporary image file needs server cleanup. Contact an administrator."
                               if committed else "Save failed and a temporary image file needs server cleanup. Contact an administrator.")
                    logger.exception("Obsolete or temporary entity image cleanup required: %s", path)
        return warning


def commit_entity_image(db, row, owner: AwsumPathResolver, identifier: str | int, *,
                        category: str, image_data: bytes | None = None,
                        remove_image: bool = False, delete: bool = False,
                        reference_field: str = "c_image_path") -> str | None:
    change = None
    try:
        if image_data is not None or remove_image or (delete and getattr(row, reference_field)):
            if image_data is not None:
                image_data = images.process_image(image_data)
            change = CanonicalImageChange(owner, identifier, getattr(row, reference_field), image_data, category=category)
            change.apply()
            setattr(row, reference_field, change.reference)
        if delete:
            db.delete(row)
        db.commit()
    except Exception as exc:
        try:
            db.rollback()
        finally:
            if change:
                try:
                    change.rollback()
                except OSError as restore_error:
                    raise HTTPException(500, "Image recovery failed. Contact an administrator before retrying.") from restore_error
                warning = change.finish()
                if warning:
                    exc.add_note(warning)
        raise
    return change.finish(committed=True) if change else None
