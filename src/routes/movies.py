from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status, BackgroundTasks
from sqlalchemy import or_, func, and_
from sqlalchemy.exc import IntegrityError

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload, joinedload

from config.dependencies import get_current_user_id, get_accounts_email_notificator
from database import User, UserGroupEnum
from database.models import OrderItem
from notifications import EmailSenderInterface
from database import get_db
from database.models.movies import (
    Movie,
    Genre,
    Director,
    Star, Certification, Favorite, Like, Dislike, Comment, AnswerComment, Rating,
)

from schemas.movies import (
    MovieListItemSchema,
    MovieListResponseSchema, MovieDetailSchema, MovieCreateSchema, MovieUpdateSchema, CommentSchema,
)

router = APIRouter()


@router.get(
    "/",
    response_model=MovieListResponseSchema,
    summary="Get a paginated list of movies",
    description=(
            "This endpoint retrieves a paginated list of movies from the database. "
            "Clients can specify the `page` number and the number of items per page using `per_page`. "
            "The response includes details about the movies, total pages, and total items, "
            "along with links to the previous and next pages if applicable."
    ),
    responses={
        404: {
            "description": "No movies found.",
            "content": {
                "application/json": {"example": {"detail": "No movies found."}}
            },
        }
    },
)
async def get_movie_list(
        page: int = Query(1, ge=1, description="Page number (1-based index)"),
        per_page: int = Query(10, ge=1, le=20, description="Number of items per page"),
        year: Optional[int] = Query(None, description="Filter by year"),
        min_imdb: Optional[float] = Query(None, description="Filter by min_imdb"),
        max_imdb: Optional[float] = Query(None, description="Filter by max_imdb"),
        genre: Optional[str] = Query(None, description="Filter by genre name"),
        director: Optional[str] = Query(None, description="Filter by director name"),
        star: Optional[str] = Query(None, description="Filter by star name"),
        search: Optional[str] = Query(None, description="Search by title, description, actor or director"),
        sort_by: Optional[str] = Query(None, description="Sort by 'price', 'year', 'votes' (desc)"),
        sort: Optional[str] = Query(None, description="Sort as 'field:dir', e.g. 'id:desc'"),
        db: AsyncSession = Depends(get_db),
) -> MovieListResponseSchema:
    offset = (page - 1) * per_page

    query = (
        select(Movie)
        .options(
            selectinload(Movie.genres),
            selectinload(Movie.directors),
            selectinload(Movie.stars),
        )
    )

    if year is not None:
        query = query.filter(Movie.year == year)
    if min_imdb is not None:
        query = query.filter(Movie.imdb >= min_imdb)
    if max_imdb is not None:
        query = query.filter(Movie.imdb <= max_imdb)
    if director:
        query = query.join(Movie.directors).filter(Director.name.ilike(f"%{director}%"))
    if star:
        query = query.join(Movie.stars).filter(Star.name.ilike(f"%{star}%"))
    if genre:
        query = query.join(Movie.genres).filter(Genre.name.ilike(f"%{genre}%"))
    if search:
        query = (
            query.outerjoin(Movie.directors)
            .outerjoin(Movie.stars)
            .filter(
                or_(
                    Movie.name.ilike(f"%{search}%"),
                    Movie.description.ilike(f"%{search}%"),
                    Director.name.ilike(f"%{search}%"),
                    Star.name.ilike(f"%{search}%"),
                )
            )
        )

    applied_sort = False
    if sort:
        field, _, direction = sort.partition(":")
        field = field.strip().lower()
        direction = (direction or "asc").strip().lower()
        cols = {
            "id": Movie.id,
            "price": Movie.price,
            "year": Movie.year,
            "votes": Movie.votes,
            "imdb": Movie.imdb,
        }
        if field in cols:
            col = cols[field]
            query = query.order_by(col.desc() if direction == "desc" else col.asc())
            applied_sort = True

    if not applied_sort:
        sort_map = {"price": Movie.price, "year": Movie.year, "votes": Movie.votes}
        if sort_by in sort_map:
            query = query.order_by(sort_map[sort_by].desc())
        else:
            order_by = getattr(Movie, "default_order_by", lambda: None)()
            if order_by:
                query = query.order_by(*order_by)
            else:
                query = query.order_by(Movie.id.asc())

    count_query = query.with_only_columns(func.count(func.distinct(Movie.id))).order_by(None)
    total_items = await db.scalar(count_query) or 0

    if total_items == 0:
        raise HTTPException(status_code=404, detail="No movies found.")

    total_pages = (total_items + per_page - 1) // per_page

    if page > total_pages:
        raise HTTPException(status_code=404, detail="No movies found.")

    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    movies = result.scalars().unique().all()

    prev_page = f"/api/v1/movies/?page={page - 1}&per_page={per_page}" if page > 1 else None
    next_page = f"/api/v1/movies/?page={page + 1}&per_page={per_page}" if page < total_pages else None

    return MovieListResponseSchema(
        items=[MovieListItemSchema.model_validate(m) for m in movies],
        prev_page=prev_page,
        next_page=next_page,
        total_pages=total_pages,
        total_items=total_items,
    )


@router.post(
    "/",
    response_model=MovieDetailSchema,
    summary="Add a new movie",
    description=(
            "This endpoint allows clients to add a new movie to the database. "
            "It accepts details such as name, date, genres, actors, languages, and "
            "other attributes. The associated country, genres, actors, and languages "
            "will be created or linked automatically."
    ),
    responses={
        201: {
            "description": "Movie created successfully.",
        },
        400: {
            "description": "Invalid input.",
            "content": {
                "application/json": {"example": {"detail": "Invalid input data."}}
            },
        },
    },
    status_code=201,
)
async def create_movie(
        movie_data: MovieCreateSchema,
        user_id: int = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
) -> MovieDetailSchema:
    user = await db.scalar(
        select(User).options(selectinload(User.group)).where(User.id == user_id)
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.group.name not in (UserGroupEnum.MODERATOR, UserGroupEnum.ADMIN):
        raise HTTPException(
            status_code=403,
            detail="You do not have access to perform this action.",
        )

    existing = await db.scalar(
        select(Movie).where(Movie.name == movie_data.name, Movie.year == movie_data.year)
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A movie with the name '{movie_data.name}' and release year '{movie_data.year}' already exists.",
        )

    cert = await db.scalar(
        select(Certification).where(Certification.id == movie_data.certification_id)
    )
    if not cert:
        raise HTTPException(status_code=400, detail="Invalid certification_id.")

    async def load_list(model, ids: list[int], label: str):
        if not ids:
            return []
        res = await db.execute(select(model).where(model.id.in_(ids)))
        objs = res.scalars().all()
        if len(objs) != len(set(ids)):
            raise HTTPException(status_code=400, detail=f"One or more {label} are invalid.")
        return objs

    genres = await load_list(Genre, movie_data.genre_ids, "genre_ids")
    stars = await load_list(Star, movie_data.star_ids, "star_ids")
    directors = await load_list(Director, movie_data.director_ids, "director_ids")

    movie = Movie(
        uuid=movie_data.uuid,
        name=movie_data.name,
        year=movie_data.year,
        time=movie_data.time,
        imdb=movie_data.imdb,
        votes=movie_data.votes,
        meta_score=movie_data.meta_score,
        gross=movie_data.gross,
        description=movie_data.description,
        price=movie_data.price,
        certification=cert,
        genres=genres,
        stars=stars,
        directors=directors,
    )
    db.add(movie)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Invalid input data.")

    movie = await db.scalar(
        select(Movie)
        .options(
            selectinload(Movie.certification),
            selectinload(Movie.genres),
            selectinload(Movie.stars),
            selectinload(Movie.directors),
            selectinload(Movie.comments),
        )
        .where(Movie.id == movie.id)
    )
    return MovieDetailSchema.model_validate(movie)


@router.get(
    "/favorites/",
    response_model=MovieListResponseSchema,
    summary="Get a paginated list of favorite movies",
    description=(
            "This endpoint retrieves a paginated list of favorite movies from the database. "
            "Clients can specify the `page` number and the number of items per page using `per_page`. "
            "The response includes details about the movies, total pages, and total items, "
            "along with links to the previous and next pages if applicable."
    ),
    responses={
        404: {
            "description": "No favorite movies found.",
            "content": {
                "application/json": {"example": {"detail": "No favorite movies found."}}
            },
        }
    },
)
async def get_favorite_movies(
        page: int = Query(1, ge=1, description="Page number (1-based index)"),
        per_page: int = Query(10, ge=1, le=20, description="Number of items per page"),
        year: int | None = Query(None, description="Filter by year"),
        min_imdb: float | None = Query(None, description="Filter by min_imdb"),
        max_imdb: float | None = Query(None, description="Filter by max_imdb"),
        genre: str | None = Query(None, description="Filter by genre name"),
        director: str | None = Query(None, description="Filter by director name"),
        star: str | None = Query(None, description="Filter by star name"),
        search: str | None = Query(
            None, description="Search by title, description, actor or director"
        ),
        sort_by: str | None = Query(None, description="Sort by 'price', 'year', 'votes'"),
        db: AsyncSession = Depends(get_db),
) -> MovieListResponseSchema:
    """
    Fetch a paginated list of favorite movies from the database.

    This function retrieves a paginated list of favorite movies, allowing the client to specify
    the page number and the number of items per page. It calculates the total pages
    and provides links to the previous and next pages when applicable.
    """
    offset = (page - 1) * per_page
    stmt = (
        select(Movie)
        .join(Favorite)
        .options(
            joinedload(Movie.genres),
            joinedload(Movie.directors),
            joinedload(Movie.stars),
        )
    )

    if year:
        stmt = stmt.where(Movie.year == year)
    if min_imdb:
        stmt = stmt.where(Movie.imdb >= min_imdb)
    if max_imdb:
        stmt = stmt.where(Movie.imdb <= max_imdb)
    if director:
        stmt = stmt.join(Movie.directors).where(Director.name.ilike(f"%{director}%"))
    if star:
        stmt = stmt.join(Movie.stars).where(Star.name.ilike(f"%{star}%"))
    if genre:
        stmt = stmt.join(Movie.genres).where(Genre.name.ilike(f"%{genre}%"))
    if search:
        stmt = (
            stmt.outerjoin(Movie.directors)
            .outerjoin(Movie.stars)
            .where(
                or_(
                    Movie.name.ilike(f"%{search}%"),
                    Movie.description.ilike(f"%{search}%"),
                    Director.name.ilike(f"%{search}%"),
                    Star.name.ilike(f"%{search}%"),
                )
            )
        )

    sort_fields = {
        "price": Movie.price,
        "year": Movie.year,
        "votes": Movie.votes,
    }
    if sort_by in sort_fields:
        stmt = stmt.order_by(sort_fields[sort_by].desc())

    count_stmt = select(func.count()).select_from(stmt.subquery())
    count_result = await db.execute(count_stmt)
    total_items = count_result.scalar_one()

    result = await db.execute(stmt.offset(offset).limit(per_page))
    movies = result.unique().scalars().all()
    if not movies:
        raise HTTPException(status_code=404, detail="No favorite movies found.")

    movie_list = [MovieListItemSchema.model_validate(movie) for movie in movies]
    total_pages = (total_items + per_page - 1) // per_page

    return MovieListResponseSchema(
        movies=movie_list,
        prev_page=(
            f"/movies/favorites/?page={page - 1}&per_page={per_page}"
            if page > 1
            else None
        ),
        next_page=(
            f"/movies/favorites/?page={page + 1}&per_page={per_page}"
            if page < total_pages
            else None
        ),
        total_pages=total_pages,
        total_items=total_items,
    )


@router.post(
    "/favorite/",
    description="Add movie to favorites list.",
)
async def add_favorite(
        movie_id: int,
        user_id: int = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
):
    stmt = select(Movie).where(Movie.id == movie_id)
    result = await db.execute(stmt)
    existing_movie = result.scalar_one_or_none()
    if not existing_movie:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Movie with the given ID was not found.",
        )

    stmt = select(Favorite).where(
        and_(Favorite.user_id == user_id, Favorite.movie_id == movie_id)
    )
    result = await db.execute(stmt)
    existing_favorite = result.scalar_one_or_none()
    if existing_favorite:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Movie already in favorites"
        )

    favorite = Favorite(user_id=user_id, movie_id=movie_id)
    db.add(favorite)
    await db.commit()

    return {"detail": f"Movie {existing_movie.name} added to favorites"}


@router.delete(
    "/favorite/",
    description="Remove movie from favorites list.",
)
async def remove_favorite(
        movie_id: int,
        user_id: int = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
):
    stmt = select(Movie).where(Movie.id == movie_id)
    result = await db.execute(stmt)
    existing_movie = result.scalar_one_or_none()
    if not existing_movie:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Movie with the given ID was not found.",
        )

    stmt = select(Favorite).where(
        and_(Favorite.user_id == user_id, Favorite.movie_id == movie_id)
    )
    result = await db.execute(stmt)
    favorite = result.scalar_one_or_none()
    if not favorite:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Movie not in favorites"
        )

    await db.delete(favorite)
    await db.commit()

    return {
        "detail": f"Movie {existing_movie.name} with id: {movie_id} removed from favorites"
    }


@router.get(
    "/genres/",
    summary="Get list of genres",
    description="This endpoint retrieves a list of genres with the count of movies in each.",
    responses={
        404: {
            "description": "No genres found.",
            "content": {
                "application/json": {"example": {"detail": "No genres found."}}
            },
        }
    },
)
async def get_genres(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Genre, func.count(Movie.id).label("movie_count"))
        .join(Movie.genres)
        .group_by(Genre.id)
    )
    result = await db.execute(stmt)
    genres_with_movie_count = result.all()
    if not genres_with_movie_count:
        raise HTTPException(status_code=404, detail="No genres found.")

    return [
        {"name": genre.name, "movie_count": movie_count}
        for genre, movie_count in genres_with_movie_count
    ]


@router.get(
    "/genres/{genre_id}/",
    summary="Get genre details by genre name",
    description="This endpoint retrieves a genre with all related movies.",
    responses={
        404: {
            "description": "No genres found.",
            "content": {
                "application/json": {"example": {"detail": "No genres found."}}
            },
        }
    },
)
async def get_movies_by_genre(
        genre_id: str,
        db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Genre)
        .options(selectinload(Genre.movies))
        .where(Genre.name.ilike(genre_id))
    )
    result = await db.execute(stmt)
    genre = result.scalar_one_or_none()
    if not genre:
        raise HTTPException(status_code=404, detail="Genre not found")

    return genre.movies


@router.get("/search/", response_model=list[MovieListItemSchema])
async def search_movies(
        search: str = Query(..., min_length=1, description="Search by title"),
        db: AsyncSession = Depends(get_db),
):
    q = (
        select(Movie)
        .options(selectinload(Movie.genres))
        .where(Movie.name.ilike(f"%{search}%"))
        .order_by(Movie.id.desc())
    )
    result = await db.execute(q)
    movies = result.scalars().unique().all()
    return [MovieListItemSchema.model_validate(m) for m in movies]


@router.get(
    "/{movie_id}/",
    response_model=MovieDetailSchema,
    summary="Get movie details by ID",
    description=(
            "Fetch detailed information about a specific movie by its unique ID. "
            "This endpoint retrieves all available details for the movie, such as "
            "its name, genre, crew, budget, and revenue. If the movie with the given "
            "ID is not found, a 404 error will be returned."
    ),
    responses={
        404: {
            "description": "Movie not found.",
            "content": {
                "application/json": {
                    "example": {"detail": "Movie with the given ID was not found."}
                }
            },
        }
    },
)
async def get_movie_by_id(
        movie_id: int,
        db: AsyncSession = Depends(get_db),
) -> MovieDetailSchema:
    """
    Retrieve detailed information about a specific movie by its ID.

    This function fetches detailed information about a movie identified by its unique ID.
    If the movie does not exist, a 404 error is returned.
    """
    stmt = (
        select(Movie)
        .options(
            joinedload(Movie.genres),
            joinedload(Movie.directors),
            joinedload(Movie.stars),
            joinedload(Movie.certification),
            joinedload(Movie.comments),
            joinedload(Movie.comments).options(selectinload(Comment.answers)),
        )
        .where(Movie.id == movie_id)
    )
    result = await db.execute(stmt)
    movie = result.unique().scalar_one_or_none()
    if not movie:
        raise HTTPException(
            status_code=404, detail="Movie with the given ID was not found."
        )

    return MovieDetailSchema.model_validate(movie)


@router.put(
    "/{movie_id}/",
    summary="Update a movie by ID",
    description="This endpoint updates a specific movie by its unique ID.",
    status_code=200
)
async def update_movie(
        movie_id: int,
        movie_data: MovieUpdateSchema,
        user_id: int = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
):
    user = await db.scalar(select(User).options(selectinload(User.group)).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.group.name not in (UserGroupEnum.MODERATOR, UserGroupEnum.ADMIN):
        raise HTTPException(status_code=403, detail="You do not have access to perform this action.")

    movie = await db.scalar(select(Movie).where(Movie.id == movie_id))
    if not movie:
        raise HTTPException(status_code=404, detail="Movie with the given ID was not found.")

    data = movie_data.model_dump(exclude_unset=True)

    if "certification_id" in data:
        cert = await db.scalar(select(Certification).where(Certification.id == data.pop("certification_id")))
        if not cert:
            raise HTTPException(status_code=400, detail="Invalid certification_id.")
        movie.certification = cert

    async def set_m2m(attr: str, model, ids: list[int]):
        res = await db.execute(select(model).where(model.id.in_(ids)))
        objs = res.scalars().all()
        if len(objs) != len(set(ids)):
            raise HTTPException(status_code=400, detail=f"One or more {attr} are invalid.")
        setattr(movie, attr, objs)

    if "genre_ids" in data:
        await set_m2m("genres", Genre, data.pop("genre_ids"))
    if "star_ids" in data:
        await set_m2m("stars", Star, data.pop("star_ids"))
    if "director_ids" in data:
        await set_m2m("directors", Director, data.pop("director_ids"))

    for k, v in data.items():
        setattr(movie, k, v)

    try:
        await db.commit()
        await db.refresh(movie)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Invalid input data.")

    return {"detail": f"Movie '{movie.name}' updated successfully."}


@router.delete(
    "/{movie_id}/",
    summary="Delete a movie by ID",
    description="This endpoint deletes a specific movie by its unique ID.",
    status_code=200
)
async def delete_movie(
        movie_id: int,
        user_id: int = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
):
    """
    Delete a specific movie by its ID.

    This function deletes a movie identified by its unique ID.
    If the movie does not exist, a 404 error is raised.
    """
    stmt_user = select(User).options(selectinload(User.group)).where(User.id == user_id)
    result_user = await db.execute(stmt_user)
    user = result_user.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.group.name not in (UserGroupEnum.MODERATOR, UserGroupEnum.ADMIN):
        raise HTTPException(
            status_code=403, detail="You do not have access to perform this action."
        )

    stmt_movie = select(Movie).where(Movie.id == movie_id)
    result_movie = await db.execute(stmt_movie)
    movie = result_movie.scalar_one_or_none()
    if not movie:
        raise HTTPException(
            status_code=404, detail="Movie with the given ID was not found."
        )

    result = await db.execute(select(OrderItem).filter(OrderItem.movie_id == movie_id))
    order_items_count = len(result.scalars().all())

    if order_items_count > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete movie, it has been purchased by at least one user.",
        )

    await db.delete(movie)
    await db.commit()

    return {"detail": f"Movie {movie.name} deleted successfully."}


@router.post(
    "/{movie_id}/like",
    description="Likes a movie by ID",
)
async def like_movie(
        movie_id: int,
        user_id: User = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
):
    stmt_movie = select(Movie).where(Movie.id == movie_id)
    result_movie = await db.execute(stmt_movie)
    movie = result_movie.scalar_one_or_none()
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    stmt_like = select(Like).where(Like.movie_id == movie_id, Like.user_id == user_id)
    result_like = await db.execute(stmt_like)
    existing_like = result_like.scalar_one_or_none()
    if existing_like:
        raise HTTPException(status_code=400, detail="Movie already liked by this user")

    new_like = Like(movie_id=movie_id, user_id=user_id)
    db.add(new_like)
    await db.commit()
    await db.refresh(new_like)

    return {"message": "Movie liked", "like_id": new_like.id}


@router.post(
    "/{movie_id}/dislike",
    description="Dislikes a movie by ID",
)
async def dislike_movie(
        movie_id: int,
        user_id: User = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
):
    stmt_movie = select(Movie).where(Movie.id == movie_id)
    result_movie = await db.execute(stmt_movie)
    movie = result_movie.scalar_one_or_none()
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    stmt_dislike = select(Dislike).where(
        Dislike.movie_id == movie_id, Dislike.user_id == user_id
    )
    result_dislike = await db.execute(stmt_dislike)
    existing_dislike = result_dislike.scalar_one_or_none()
    if existing_dislike:
        raise HTTPException(status_code=400, detail="Movie already disliked")

    new_dislike = Dislike(movie_id=movie_id, user_id=user_id)
    db.add(new_dislike)
    await db.commit()
    await db.refresh(new_dislike)

    return {"message": "Movie disliked", "dislike_id": new_dislike.id}


@router.post(
    "/{movie_id}/comments",
    description="Get the comments for a specific movie by ID",
    response_model=List[CommentSchema],
)
async def create_comment(
        movie_id: int,
        comment_text: str,
        user_id: int = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Movie).filter(Movie.id == movie_id))
    movie = result.scalars().first()
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    new_comment = Comment(user_id=user_id, movie_id=movie_id, comment=comment_text)
    db.add(new_comment)
    await db.commit()
    await db.refresh(new_comment)

    return {
        "message": f"Comment created with movie id: {movie_id}",
        "comment_id": new_comment.id,
    }


@router.get(
    "/{movie_id}/comments/",
    description="Get the comments for a specific movie by ID",
)
async def get_comments(movie_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Comment)
        .options(selectinload(Comment.answers))
        .filter_by(movie_id=movie_id)
    )
    comments = result.scalars().all()
    if not comments:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No comments found."
        )

    return comments


@router.post(
    "/comments/{comment_id}/answer",
    description="Add a answer for a specific comment",
)
async def reply_to_comment(
        comment_id: int,
        answer_text: str,
        background_tasks: BackgroundTasks,
        user_id: int = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
        email_sender: EmailSenderInterface = Depends(get_accounts_email_notificator),
):
    result = await db.execute(select(Comment).filter(Comment.id == comment_id))
    comment = result.scalars().first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    answer = AnswerComment(user_id=user_id, comment_id=comment_id, text=answer_text)
    db.add(answer)
    await db.commit()
    await db.refresh(answer)

    result = await db.execute(select(Comment).filter_by(id=answer.comment_id))
    comment = result.scalars().first()

    result = await db.execute(select(User).filter_by(id=comment.user_id))
    user = result.scalars().first()
    user_email = user.email if user else None
    if user_email:
        background_tasks.add_task(
            email_sender.send_comment_answer,
            user_email,
            f"New Reply to Your Comment: {answer_text}",
        )

    return {"message": "Reply created", "reply_id": answer.id}


@router.put(
    "/{movie_id}/rate",
    summary="Rate a movie by its ID",
    description="Rate movies on a 10-point scale.",
    responses={
        400: {
            "description": "Bad Request - The provided refresh token is invalid or expired.",
            "content": {
                "application/json": {"example": {"detail": "Token has expired."}}
            },
        },
        401: {
            "description": "Unauthorized - Refresh token not found.",
            "content": {
                "application/json": {"example": {"detail": "Refresh token not found."}}
            },
        },
        404: {
            "description": "Not Found - The movie does not exist.",
            "content": {
                "application/json": {"example": {"detail": "Movie not found."}}
            },
        },
    },
)
async def rate_movie(
        movie_id: int,
        rating: int = Query(ge=0, le=10),
        db: AsyncSession = Depends(get_db),
        user_id: int = Depends(get_current_user_id),
):
    result = await db.execute(select(Movie).filter(Movie.id == movie_id))
    movie = result.scalars().first()
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    new_rating = Rating(user_id=user_id, movie_id=movie_id, rating=rating)
    db.add(new_rating)
    await db.commit()

    result = await db.execute(select(Rating).filter(Rating.movie_id == movie_id))
    ratings = result.scalars().all()
    if not ratings:
        return 0.0

    total_rating = sum(r.rating for r in ratings)
    average_rating = total_rating / len(ratings)
    movie.votes = len(ratings)
    await db.commit()

    return {"average_rating": average_rating}
