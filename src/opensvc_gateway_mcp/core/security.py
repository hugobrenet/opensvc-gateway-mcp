from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials


basic_auth = HTTPBasic(auto_error=False)


def require_basic_credentials(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic_auth)],
) -> HTTPBasicCredentials:
    if credentials is None or not credentials.username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Basic Auth credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials
