FROM python:3.13-slim

# Unbuffered stdout/stderr so management-command progress streams in
# real time when piped (`docker compose exec ... | tee ...` etc.). The
# default block buffering when stdout isn't a TTY would otherwise hold
# every line until the process ends.
ENV PYTHONUNBUFFERED=1

RUN pip install uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

COPY . .

EXPOSE 8000
CMD ["uv", "run", "python", "manage.py", "runserver", "0.0.0.0:8000"]
