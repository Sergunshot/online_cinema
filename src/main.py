from fastapi import FastAPI

from routes import accounts_router, profile_router, movies_router, cart_router


app = FastAPI(
    title="Online cinema",
    description="Online Cinema project based on FastAPI and SQLAlchemy",
)

app.include_router(accounts_router, prefix=f"/accounts", tags=["accounts"])
app.include_router(profile_router, prefix=f"/profile", tags=["profile"])
app.include_router(movies_router, prefix=f"/movies", tags=["movies"])
app.include_router(cart_router, prefix=f"/cart", tags=["cart"])
