FROM python:3.10-slim

# Setting environment variables for Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=off

# Installing dependencies
RUN apt update

# Install Poetry
RUN python -m pip install --upgrade pip && \
    pip install poetry

# Copy dependency files
COPY ./poetry.lock /usr/src/poetry/poetry.lock
COPY ./pyproject.toml /usr/src/poetry/pyproject.toml

# Copy pytest config file
COPY ./pytest.ini /usr/src/config/pytest.ini

# Configure Poetry to avoid creating a virtual environment
RUN poetry config virtualenvs.create false

# Selecting a working directory
WORKDIR /usr/src/poetry

# Install dependencies with Poetry
RUN poetry lock
RUN poetry install --no-root --only main

# Selecting a working directory
WORKDIR /usr/src/fastapi

# Copy the source code
COPY ./src .
