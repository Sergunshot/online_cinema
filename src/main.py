from fastapi import FastAPI

from routes import (
    accounts_router,
    profile_router,
    movies_router, cart_router,
    orders_router,
    payments_router
)


app = FastAPI(
    title="Online cinema",
    description="Online Cinema project based on FastAPI and SQLAlchemy",
)
API_V1 = "/api/v1"

app.include_router(accounts_router, prefix=f"{API_V1}/accounts", tags=["accounts"])
app.include_router(profile_router, prefix=f"{API_V1}/profiles", tags=["profiles"])
app.include_router(movies_router, prefix=f"{API_V1}/movies", tags=["movies"])
app.include_router(cart_router, prefix=f"{API_V1}/cart", tags=["cart"])
app.include_router(orders_router, prefix=f"{API_V1}/orders", tags=["orders"])
app.include_router(payments_router, prefix=f"{API_V1}/payments", tags=["payments"])
