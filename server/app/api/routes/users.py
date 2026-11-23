"""User management and guest session routes."""

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session
from app.db.postgres import get_db
from app.schemas.chat import GuestUserResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["Users"])


@router.post("/guest", response_model=GuestUserResponse)
def create_guest_user(db: Session = Depends(get_db)) -> GuestUserResponse:
    """Create a guest user account without requiring credentials.

    Returns the generated UUID user_id for client session persistence.
    """
    try:
        stmt = sa_text("""
            INSERT INTO users (auth_provider, role, consent_logging)
            VALUES ('guest', 'user', FALSE)
            RETURNING user_id, auth_provider, created_at
        """)
        row = db.execute(stmt).mappings().fetchone()
        db.commit()

        return GuestUserResponse(
            user_id=row["user_id"],
            auth_provider=str(row["auth_provider"]),
            created_at=row["created_at"],
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to create guest user: {e}")
        raise HTTPException(status_code=500, detail="Could not create guest user account.")
