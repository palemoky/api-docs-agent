"""A tiny FastAPI service used to demonstrate the agent end to end.

Change a route or a model here, commit, and run:

    api-docs-agent check --repo . --dry-run --show-diff
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr

app = FastAPI(title="Demo Service", version="1.0.0")


class User(BaseModel):
    id: int
    name: str
    email: EmailStr


class CreateUserRequest(BaseModel):
    name: str
    email: EmailStr


_USERS: dict[int, User] = {}


@app.get("/users", response_model=list[User])
def list_users() -> list[User]:
    """Return every user."""
    return list(_USERS.values())


@app.get("/users/{user_id}", response_model=User)
def get_user(user_id: int) -> User:
    """Return a single user by id."""
    if user_id not in _USERS:
        raise HTTPException(status_code=404, detail="User not found")
    return _USERS[user_id]


@app.post("/users", response_model=User, status_code=201)
def create_user(payload: CreateUserRequest) -> User:
    """Create a user."""
    user = User(id=len(_USERS) + 1, name=payload.name, email=payload.email)
    _USERS[user.id] = user
    return user
