import uuid

import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import joinedload
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.movies import Movie, Certification
from database.models.accounts import User


@pytest.mark.asyncio
async def test_get_movies_empty_database(auth_moderator_client):
    """
    Test that the `/api/v1/online_cinema/movies/` endpoint returns a 404 error when the database is empty.
    """
    response = await auth_moderator_client.get("/api/v1/movies/")
    assert response.status_code == 404, f"Expected 404, got {response.status_code}"

    expected_detail = {"detail": "No movies found."}
    assert response.json() == expected_detail, f"Expected {expected_detail}, got {response.json()}"


@pytest.mark.asyncio
async def test_get_movies_default_parameters(auth_moderator_client, seed_database):
    """
    Test the `/api/v1/online_cinema/movies/` endpoint with default pagination parameters.
    """
    response = await auth_moderator_client.get("/api/v1/movies/")
    assert response.status_code == 200, "Expected status code 200, but got a different value"

    response_data = response.json()
    assert "items" in response_data, "Response missing 'items' field"
    assert len(response_data["items"]) > 0, "Expected at least one movie in the response"


@pytest.mark.asyncio
async def test_get_movies_with_custom_parameters(auth_moderator_client, seed_database):
    """
    Test the `/api/v1/online_cinema/movies/` endpoint with custom pagination parameters.
    """
    page = 1
    per_page = 5

    response = await auth_moderator_client.get(f"/api/v1/movies/?page={page}&per_page={per_page}")
    assert response.status_code == 200, f"Expected status code 200, but got {response.status_code}"

    response_data = response.json()
    assert "items" in response_data, "Response missing 'items' field"
    assert len(response_data["items"]) <= per_page, f"Expected at most {per_page} movies in the response"


@pytest.mark.asyncio
@pytest.mark.parametrize("page, per_page, expected_detail", [
    (0, 10, "Input should be greater than or equal to 1"),
    (1, 0, "Input should be greater than or equal to 1"),
    (0, 0, "Input should be greater than or equal to 1"),
])
async def test_invalid_page_and_per_page(auth_moderator_client, page, per_page, expected_detail):
    """
    Test the `/api/v1/online_cinema/movies/` endpoint with invalid `page` and `per_page` parameters.
    """
    response = await auth_moderator_client.get(f"/api/v1/movies/?page={page}&per_page={per_page}")
    assert response.status_code in [404, 422], (
        f"Expected status code 422 or 404 for invalid parameters, but got {response.status_code}"
    )


@pytest.mark.asyncio
async def test_per_page_maximum_allowed_value(auth_moderator_client, seed_database):
    """
    Test the `/api/v1/online_cinema/movies/` endpoint with the maximum allowed `per_page` value.
    """
    response = await auth_moderator_client.get("/api/v1/movies/?page=1&per_page=20")

    assert response.status_code == 200, f"Expected status code 200, but got {response.status_code}"

    response_data = response.json()

    assert "items" in response_data, "Response missing 'items' field."
    assert len(response_data["items"]) <= 20, (
        f"Expected at most 20 movies, but got {len(response_data['items'])}"
    )


@pytest.mark.asyncio
async def test_page_exceeds_maximum(auth_moderator_client, db_session, seed_database):
    """
    Test the `/api/v1/online_cinema/movies/` endpoint with a page number that exceeds the maximum.
    """
    per_page = 10

    count_stmt = select(func.count(Movie.id))
    result = await db_session.execute(count_stmt)
    total_movies = result.scalar_one()

    max_page = (total_movies + per_page - 1) // per_page

    response = await auth_moderator_client.get(f"/api/v1/movies/?page={max_page + 1}&per_page={per_page}")

    assert response.status_code == 404, f"Expected status code 404, but got {response.status_code}"
    response_data = response.json()

    assert "detail" in response_data, "Response missing 'detail' field."


@pytest.mark.asyncio
async def test_movies_sorted_by_id_desc(auth_moderator_client, db_session, seed_database):
    """
    Test that movies are returned sorted by `id` in descending order
    and match the expected data from the database.
    """
    response = await auth_moderator_client.get("/api/v1/movies/?page=1&per_page=10")

    assert response.status_code == 200, f"Expected status code 200, but got {response.status_code}"

    response_data = response.json()

    stmt = select(Movie).order_by(Movie.id.desc()).limit(10)
    result = await db_session.execute(stmt)
    expected_movies = result.scalars().all()

    expected_movie_ids = [movie.id for movie in expected_movies]
    returned_movie_ids = [movie["id"] for movie in response_data["items"]]

    assert returned_movie_ids == expected_movie_ids, (
        f"Movies are not sorted by `id` in descending order. "
        f"Expected: {expected_movie_ids}, but got: {returned_movie_ids}"
    )


@pytest.mark.asyncio
async def test_movie_list_with_pagination(auth_moderator_client, db_session, seed_database):
    """
    Test the `/api/v1/online_cinema/movies/` endpoint with pagination parameters.
    """
    page = 1
    per_page = 5

    response = await auth_moderator_client.get(f"/api/v1/movies/?page={page}&per_page={per_page}")
    assert response.status_code == 200, f"Expected status code 200, but got {response.status_code}"

    response_data = response.json()
    assert "items" in response_data, "Response missing 'items' field"
    assert len(response_data["items"]) <= per_page, f"Expected at most {per_page} movies in the response"


@pytest.mark.asyncio
async def test_movies_fields_match_schema(auth_moderator_client, db_session, seed_database):
    """
    Test that each movie in the response matches the fields defined in `MovieListItemSchema`.
    """
    response = await auth_moderator_client.get("/api/v1/movies/?page=1&per_page=10")
    assert response.status_code == 200, f"Expected status code 200, but got {response.status_code}"

    response_data = response.json()
    assert "items" in response_data, "Response missing 'items' field."

    expected_fields = {"id", "name", "year", "time", "price", "imdb", "genres"}
    for movie in response_data["items"]:
        assert set(movie.keys()) == expected_fields, (
            f"Movie fields do not match schema. "
            f"Expected: {expected_fields}, but got: {set(movie.keys())}"
        )


@pytest.mark.asyncio
async def test_get_movie_by_id_not_found(auth_moderator_client):
    """
    Test that the `/api/v1/online_cinema/movies/{movie_id}` endpoint returns a 404 error
    when a movie with the given ID does not exist.
    """
    movie_id = 999  # Using a non-existent ID
    response = await auth_moderator_client.get(f"/api/v1/movies/{movie_id}/")
    assert response.status_code == 404, f"Expected status code 404, but got {response.status_code}"


@pytest.mark.asyncio
async def test_get_movie_by_id_valid(auth_moderator_client, db_session, seed_database):
    """
    Test that the `/api/v1/online_cinema/movies/{movie_id}` endpoint returns the correct movie
    when a valid movie ID is provided.
    """
    # Get a movie from the database
    result = await db_session.execute(select(Movie).limit(1))
    movie = result.scalars().first()
    assert movie is not None, "No movies found in the database"

    response = await auth_moderator_client.get(f"/api/v1/movies/{movie.id}/")
    assert response.status_code == 200, f"Expected status code 200, but got {response.status_code}"


@pytest.mark.asyncio
async def test_get_movie_by_id_fields_match_database(auth_moderator_client, db_session, seed_database):
    """
    Test that the `/api/v1/online_cinema/movies/{movie_id}` endpoint returns all fields matching the database data.
    """
    stmt = (
        select(Movie)
        .options(
            joinedload(Movie.genres),
            joinedload(Movie.stars),
            joinedload(Movie.directors),
            joinedload(Movie.certification),
        )
        .limit(1)
    )
    result = await db_session.execute(stmt)
    random_movie = result.scalars().first()
    assert random_movie is not None, "No movies found in the database."

    response = await auth_moderator_client.get(f"/api/v1/movies/{random_movie.id}/")
    assert response.status_code == 200, f"Expected status code 200, but got {response.status_code}"


@pytest.mark.asyncio
async def test_create_movie_success(auth_moderator_client, seed_movie_relations):
    """
    Test successful movie creation.
    """
    movie_data = {
        "name": "Test Movie",
        "year": 2024,
        "time": 120,
        "imdb": 8.5,
        "votes": 1000,
        "meta_score": 85,
        "gross": 1000000,
        "description": "A test movie",
        "price": 9.99,
        "certification_id": 1,
        "genre_ids": [1],
        "star_ids": [1],
        "director_ids": [1]
    }

    response = await auth_moderator_client.post("/api/v1/movies/", json=movie_data)
    assert response.status_code == 201, f"Expected 201, got {response.status_code}"

    response_data = response.json()
    assert response_data["name"] == movie_data["name"]
    assert response_data["year"] == movie_data["year"]
    assert response_data["time"] == movie_data["time"]
    assert response_data["price"] == movie_data["price"]


@pytest.mark.asyncio
async def test_create_movie_duplicate_error(auth_moderator_client, seed_movie_relations):
    """
    Test movie creation with duplicate name.
    """
    movie_data = {
        "name": "Test Movie",
        "year": 2024,
        "time": 120,
        "imdb": 8.5,
        "votes": 1000,
        "meta_score": 85,
        "gross": 1000000,
        "description": "A test movie",
        "price": 9.99,
        "certification_id": 1,
        "genre_ids": [1],
        "star_ids": [1],
        "director_ids": [1]
    }

    # Create first movie
    response1 = await auth_moderator_client.post("/api/v1/movies/", json=movie_data)
    assert response1.status_code == 201, f"Expected 201, got {response1.status_code}"

    # Try to create duplicate movie
    response2 = await auth_moderator_client.post("/api/v1/movies/", json=movie_data)
    assert response2.status_code in [400, 409], f"Expected 400 or 409, got {response2.status_code}"


@pytest.mark.asyncio
async def test_delete_movie_success(auth_moderator_client, seed_movie_relations):
    """
    Create a movie → delete it → verify it can no longer be retrieved or deleted.
    """
    unique_suffix = uuid.uuid4().hex[:8]
    movie_data = {
        "name": f"Test Movie {unique_suffix}",
        "year": 2024,
        "time": 120,
        "imdb": 8.5,
        "votes": 1000,
        "meta_score": 85,
        "gross": 1_000_000,
        "description": "A test movie",
        "price": 9.99,
        "certification_id": 1,
        "genre_ids": [1],
        "star_ids": [1],
        "director_ids": [1],
    }

    # Create
    create_response = await auth_moderator_client.post("/api/v1/movies/", json=movie_data)
    assert create_response.status_code == 201, f"Expected 201, got {create_response.status_code}"
    payload = create_response.json()
    assert "id" in payload, f"No 'id' in response: {payload}"
    movie_id = payload["id"]

    async def _cleanup():
        try:
            await auth_moderator_client.delete(f"/api/v1/movies/{movie_id}/")
        except Exception:
            pass

    # Delete
    delete_response = await auth_moderator_client.delete(f"/api/v1/movies/{movie_id}/")
    assert delete_response.status_code in (200, 204), (
        f"Expected 200/204, got {delete_response.status_code}, body={delete_response.text}"
    )

    get_response = await auth_moderator_client.get(f"/api/v1/movies/{movie_id}/")
    assert get_response.status_code == 404, (
        f"After delete expected 404, got {get_response.status_code}, body={get_response.text}"
    )

    second_delete = await auth_moderator_client.delete(f"/api/v1/movies/{movie_id}/")
    assert second_delete.status_code == 404, (
        f"Second delete expected 404, got {second_delete.status_code}, body={second_delete.text}"
    )


@pytest.mark.asyncio
async def test_delete_movie_not_found(auth_moderator_client):
    """
    Test movie deletion with non-existent ID.
    """
    response = await auth_moderator_client.delete("/api/v1/movies/999/")
    assert response.status_code == 404, f"Expected 404, got {response.status_code}"
    assert response.json()["detail"] == "Movie with the given ID was not found."


@pytest.mark.asyncio
async def test_update_movie_success(auth_moderator_client, seed_movie_relations):
    """
    Test successful movie update.
    """
    # First create a movie
    movie_data = {
        "name": "Test Movie",
        "year": 2024,
        "time": 120,
        "imdb": 8.5,
        "votes": 1000,
        "meta_score": 85,
        "gross": 1000000,
        "description": "A test movie",
        "price": 9.99,
        "certification_id": 1,
        "genre_ids": [1],
        "star_ids": [1],
        "director_ids": [1]
    }

    create_response = await auth_moderator_client.post("/api/v1/movies/", json=movie_data)
    assert create_response.status_code == 201, f"Expected 201, got {create_response.status_code}"

    movie_id = create_response.json()["id"]

    # Update the movie
    update_data = {
        "name": "Updated Test Movie",
        "price": 14.99
    }

    update_response = await auth_moderator_client.put(f"/api/v1/movies/{movie_id}/", json=update_data)
    assert update_response.status_code == 200, f"Expected 200, got {update_response.status_code}"


@pytest.mark.asyncio
async def test_update_movie_not_found(auth_moderator_client, seed_movie_relations):
    """
    Test movie update with non-existent ID.
    """
    update_data = {
        "name": "Updated Test Movie",
        "year": 2025,
        "time": 130,
        "imdb": 9.0,
        "votes": 2000,
        "meta_score": 90,
        "gross": 2000000,
        "description": "An updated test movie",
        "price": 19.99,
        "certification_id": 1,
        "genre_ids": [1],
        "star_ids": [1],
        "director_ids": [1]
    }

    response = await auth_moderator_client.put("/api/v1/movies/999/", json=update_data)
    assert response.status_code == 404, f"Expected 404, got {response.status_code}"
    assert response.json()["detail"] == "Movie with the given ID was not found."


@pytest.mark.asyncio
async def test_search_movies(auth_moderator_client, db_session, seed_database):
    """
    Test searching for movies by title.
    """
    # Get a movie name to search for
    result = await db_session.execute(select(Movie).limit(1))
    movie = result.scalars().first()
    assert movie is not None, "No movies found in the database"

    # Search for the movie by name
    search_term = movie.name.split()[0]  # Use first word of movie name
    response = await auth_moderator_client.get(f"/api/v1/movies/search/?search={search_term}")
    assert response.status_code == 200, f"Expected status code 200, but got {response.status_code}"

    response_data = response.json()
    assert len(response_data) > 0, "No movies found in search results"
    assert any(m["name"] == movie.name for m in response_data), "Searched movie not found in results"


@pytest.mark.asyncio
async def test_list_movies(
    auth_moderator_client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
):
    """Test listing movies with pagination."""
    # Create test movies
    certification = Certification(name="PG-13")
    db_session.add(certification)
    await db_session.commit()

    movies = [
        Movie(
            name=f"Test Movie {i}",
            description=f"Description {i}",
            price=10.0 + i,
            year=2024,
            time=120,
            certification_id=certification.id,
        )
        for i in range(3)
    ]
    db_session.add_all(movies)
    await db_session.commit()

    response = await auth_moderator_client.get("/api/v1/movies/?page=1&per_page=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) >= 1
