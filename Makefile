# Common tasks. Node 24 is needed for the frontend (see frontend/.nvmrc).

.PHONY: install build-ui run dev-api dev-ui test lint up down logs

install:        ## install backend and frontend dependencies
	cd backend && uv sync
	cd frontend && npm ci

build-ui:       ## build the Angular app into frontend/dist
	cd frontend && npm run build

run: build-ui   ## run everything natively on http://localhost:8000 (fastest on a Mac)
	cd backend && uv run clipper ui

dev-api:        ## API and worker only, for frontend development
	cd backend && uv run clipper ui --no-browser

dev-ui:         ## Angular dev server with live reload on http://localhost:4200
	cd frontend && npm start

test:           ## backend tests
	cd backend && uv run pytest -q

lint:           ## backend lint
	cd backend && uv run ruff check clipper tests

up:             ## start the Docker stack on http://localhost:8000
	docker compose up -d --build

down:           ## stop the Docker stack
	docker compose down

logs:           ## follow the worker's log
	docker compose logs -f worker
