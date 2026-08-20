# providers/storage/

File/blob storage adapters (local disk for the hackathon; potentially
object storage later). No vendor selected. Backend upload handling in
Phase 0 uses `backend/app/security/filenames.py` for path safety only —
it does not persist files to a real storage backend yet.
