from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])
# NOTE: This file is intentionally empty.
# Logout logic was moved to src/app/modules/auth/router.py.
# This router skeleton is kept for backward compatibility with any imports.
