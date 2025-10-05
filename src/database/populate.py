import asyncio
import math
import uuid

import pandas as pd
from sqlalchemy import insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from tqdm import tqdm

from config.settings import TestingSettings
from database import get_db_contextmanager
from database.models.accounts import UserGroupEnum, UserGroup
from database.models.movies import Certification, Director, Genre, Movie, Star

CHUNK_SIZE = 1000


class CSVDatabaseSeeder:
    def __init__(self, csv_file_path: str, db_session: AsyncSession) -> None:
        self._csv_file_path = csv_file_path
        self._db_session = db_session

    async def is_db_populated(self) -> bool:
        result = await self._db_session.execute(select(Movie).limit(1))
        first_movie = result.scalars().first()
        return first_movie is not None

    async def _seed_movies_from_csv(self) -> None:
        """
        Seeds movies from CSV file.
        """
        import pandas as pd

        data = pd.read_csv(self._csv_file_path)

        data.columns = [c.strip().lower() for c in data.columns]
        if "description" not in data.columns and "descriptions" in data.columns:
            data = data.rename(columns={"descriptions": "description"})

        for col in ["name", "description", "certification", "genres", "directors", "stars"]:
            if col not in data.columns:
                data[col] = ""
            else:
                data[col] = data[col].fillna("")

        for col, default in {"year": 0, "time": 0, "imdb": 0.0, "votes": 0, "price": 0.0}.items():
            if col in data.columns:
                data[col] = pd.to_numeric(data[col], errors="coerce").fillna(default)

        for col in ["meta_score", "gross"]:
            if col in data.columns:
                data[col] = pd.to_numeric(data[col], errors="coerce")

        # Stars
        all_stars: set[str] = set()
        for stars_str in data["stars"].dropna():
            all_stars.update(star.strip() for star in str(stars_str).split(",") if star.strip())
        stars = {star: Star(name=star) for star in all_stars}
        self._db_session.add_all(stars.values())
        await self._db_session.flush()

        # Certifications
        cert_names = [str(c).strip() for c in data["certification"].dropna().unique() if str(c).strip()]
        certifications = {cert: Certification(name=cert) for cert in cert_names}
        self._db_session.add_all(certifications.values())
        await self._db_session.flush()

        # Genres
        all_genres: set[str] = set()
        for genres_str in data["genres"].dropna():
            all_genres.update(genre.strip() for genre in str(genres_str).split(",") if genre.strip())
        genres = {g: Genre(name=g) for g in all_genres}
        self._db_session.add_all(genres.values())
        await self._db_session.flush()

        # Directors
        all_directors: set[str] = set()
        for directors_str in data["directors"].dropna():
            all_directors.update(d.strip() for d in str(directors_str).split(",") if d.strip())
        directors = {d: Director(name=d) for d in all_directors}
        self._db_session.add_all(directors.values())
        await self._db_session.flush()

        for _, row in data.iterrows():
            cert_key = str(row["certification"]).strip()

            if cert_key and cert_key not in certifications:
                new_cert = Certification(name=cert_key)
                self._db_session.add(new_cert)
                await self._db_session.flush()
                certifications[cert_key] = new_cert

            movie = Movie(
                uuid=str(uuid.uuid4()),
                name=str(row["name"]),
                year=int(row["year"] or 0),
                time=int(row["time"] or 0),
                imdb=float(row["imdb"] or 0.0),
                votes=int(row["votes"] or 0),
                meta_score=(float(row["meta_score"]) if pd.notna(row["meta_score"]) else None),
                gross=(float(row["gross"]) if pd.notna(row["gross"]) else None),
                description=str(row["description"]),
                price=float(row["price"] or 0.0),
                certification_id=(certifications[cert_key].id if cert_key in certifications else None),
                genres=[genres[g.strip()] for g in str(row["genres"]).split(",") if g.strip()],
                directors=[directors[d.strip()] for d in str(row["directors"]).split(",") if d.strip()],
                stars=[stars[s.strip()] for s in str(row["stars"]).split(",") if s.strip()],
            )

            self._db_session.add(movie)

        await self._db_session.flush()
        print("Movies seeded successfully.")

    def _preprocess_csv(self) -> pd.DataFrame:
        data = pd.read_csv(self._csv_file_path)

        # Ensure all required columns are present
        required_columns = [
            "name",
            "year",
            "time",
            "imdb",
            "votes",
            "meta_score",
            "gross",
            "description",
            "price",
            "certification",
            "genres",
            "directors",
            "stars",
        ]

        for col in required_columns:
            if col not in data.columns:
                raise ValueError(f"Required column '{col}' is missing in the CSV file")

        # Clean up data
        data["name"] = data["name"].astype(str)
        data["year"] = data["year"].astype(int)
        data["time"] = data["time"].astype(int)
        data["imdb"] = data["imdb"].astype(float)
        data["votes"] = data["votes"].astype(int)
        data["meta_score"] = data["meta_score"].astype(float)
        data["gross"] = data["gross"].astype(float)
        data["description"] = data["description"].astype(str)
        data["price"] = data["price"].astype(float)
        data["certification"] = data["certification"].astype(str)
        data["genres"] = data["genres"].astype(str)
        data["directors"] = data["directors"].astype(str)
        data["stars"] = data["stars"].astype(str)

        # Clean up genres, directors and stars
        data["genres"] = data["genres"].apply(lambda x: ",".join(sorted(set(g.strip() for g in x.split(",")))))
        data["directors"] = data["directors"].apply(lambda x: ",".join(sorted(set(d.strip() for d in x.split(",")))))
        data["stars"] = data["stars"].apply(lambda x: ",".join(sorted(set(s.strip() for s in x.split(",")))))

        print("Preprocessing CSV file...")
        data.to_csv(self._csv_file_path, index=False)
        print(f"CSV file saved to {self._csv_file_path}")
        return data

    async def _seed_user_groups(self) -> None:
        """
        Seeds user groups from enums.
        """
        # Get group names from enum
        group_names = [group.value for group in UserGroupEnum]

        # Use _get_or_create_bulk to handle existing groups
        await self._get_or_create_bulk(UserGroup, group_names, "name")
        await self._db_session.flush()
        print("User groups seeded successfully.")

    async def _get_or_create_bulk(self, model, items: list[str], unique_field: str) -> dict[str, object]:
        existing_dict: dict[str, object] = {}

        if items:
            for i in range(0, len(items), CHUNK_SIZE):
                chunk_str: list[str] = items[i: i + CHUNK_SIZE]
                result = await self._db_session.execute(
                    select(model).where(getattr(model, unique_field).in_(chunk_str))
                )
                existing_in_chunk = result.scalars().all()
                for obj in existing_in_chunk:
                    key = getattr(obj, unique_field)
                    existing_dict[key] = obj

        new_items: list[str] = [item for item in items if item not in existing_dict]
        new_records: list[dict[str, str]] = [{unique_field: item} for item in new_items]

        if new_records:
            for i in range(0, len(new_records), CHUNK_SIZE):
                chunk_dict: list[dict[str, str]] = new_records[i: i + CHUNK_SIZE]
                await self._db_session.execute(insert(model).values(chunk_dict))
                await self._db_session.flush()

            for i in range(0, len(new_items), CHUNK_SIZE):
                chunk_str_new: list[str] = new_items[i: i + CHUNK_SIZE]
                result_new = await self._db_session.execute(
                    select(model).where(getattr(model, unique_field).in_(chunk_str_new))
                )
                inserted_in_chunk = result_new.scalars().all()
                for obj in inserted_in_chunk:
                    key = getattr(obj, unique_field)
                    existing_dict[key] = obj

        return existing_dict

    async def _bulk_insert(self, table, data_list: list[dict[str, int]]) -> None:
        total_records = len(data_list)
        if total_records == 0:
            return

        num_chunks = math.ceil(total_records / CHUNK_SIZE)
        table_name = getattr(table, "__tablename__", str(table))

        for chunk_index in tqdm(range(num_chunks), desc=f"Inserting into {table_name}"):
            start = chunk_index * CHUNK_SIZE
            end = start + CHUNK_SIZE
            chunk = data_list[start:end]
            if chunk:
                await self._db_session.execute(insert(table).values(chunk))

        await self._db_session.flush()

    async def _prepare_reference_data(self, data: pd.DataFrame) -> dict[str, object]:
        stars = {star.strip() for stars_ in data["stars"].dropna() for star in stars_.split(",") if star.strip()}
        star_map = await self._get_or_create_bulk(Star, list(stars), "name")
        return star_map

    def _prepare_associations(
            self,
            data: pd.DataFrame,
            movie_ids: list[int],
            star_map: dict[str, Star],
    ) -> list[dict[str, int]]:
        movie_stars_data: list[dict[str, int]] = []

        for i, (_, row) in enumerate(tqdm(data.iterrows(), total=data.shape[0], desc="Processing associations")):
            movie_id = movie_ids[i]
            for star_name in row["stars"].split(","):
                star_name_clean = star_name.strip()
                if star_name_clean:
                    star = star_map[star_name_clean]
                    movie_stars_data.append({"movie_id": movie_id, "star_id": star.id})

        return movie_stars_data

    def _prepare_movies_data(self, data: pd.DataFrame) -> list[dict[str, int | str]]:
        movies_data: list[dict[str, int | str]] = []
        for _, row in data.iterrows():
            movies_data.append(
                {
                    "names": str(row["names"]),
                    "date_x": str(row["date_x"]),
                    "country": str(row["country"]),
                    "orig_lang": str(row["orig_lang"]),
                    "status": str(row["status"]),
                }
            )
        return movies_data

    async def seed(self) -> None:
        try:
            if self._db_session.in_transaction():
                print("Rolling back existing transaction.")
                await self._db_session.rollback()

            await self._seed_user_groups()
            await self._seed_movies_from_csv()
            await self._db_session.commit()
            print("Seeding completed.")

        except SQLAlchemyError as e:
            print(f"An error occurred: {e}")
            raise
        except Exception as e:
            print(f"Unexpected error: {e}")
            raise


async def main() -> None:
    settings = TestingSettings()
    async with get_db_contextmanager() as db_session:
        seeder = CSVDatabaseSeeder(settings.PATH_TO_MOVIES_CSV, db_session)
        if not await seeder.is_db_populated():
            try:
                await seeder.seed()
                print("Database seeding completed successfully.")
            except Exception as e:
                print(f"Failed to seed the database: {e}")
        else:
            print("Database is already populated. Skipping seeding.")


if __name__ == "__main__":
    asyncio.run(main())
