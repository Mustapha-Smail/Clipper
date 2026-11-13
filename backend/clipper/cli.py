"""Command line.

  clipper ui          migrate, then run the API and a worker together (native use)
  clipper api         the API only          (one container in Docker)
  clipper worker      the worker only       (another container in Docker)
  clipper db upgrade  apply database migrations
  clipper run <src>   process one video in the foreground, without the UI
"""

import subprocess
import sys
from typing import Annotated

import typer
from dotenv import load_dotenv

from . import config, db

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="Turn long videos into captioned vertical clips.")
db_app = typer.Typer(no_args_is_help=True, help="Database maintenance.")
app.add_typer(db_app, name="db")


@app.callback()
def main() -> None:
    load_dotenv(config.REPO / ".env")


def _serve(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run("clipper.api:app", host=host, port=port, log_config=None, access_log=False)


@app.command()
def api(host: Annotated[str, typer.Option(help="Address to listen on")] = "127.0.0.1",
        port: Annotated[int, typer.Option(help="Port to listen on")] = 8000):
    """Run the HTTP API."""
    _serve(host, port)


@app.command()
def worker():
    """Run the job worker."""
    from .worker import main as worker_main

    worker_main()


@app.command()
def ui(host: Annotated[str, typer.Option(help="Address to listen on")] = "127.0.0.1",
       port: Annotated[int, typer.Option(help="Port to listen on")] = 8000,
       browser: Annotated[bool, typer.Option(help="Open the browser on start")] = True):
    """Migrate the database, then run the API and a worker together."""
    import threading
    import webbrowser

    db.upgrade()
    child = subprocess.Popen([sys.executable, "-m", "clipper.worker"])
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}"
    typer.echo(f"Clipper: {url}   (API docs: {url}/api/docs)")
    if browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    try:
        _serve(host, port)
    finally:
        child.terminate()
        child.wait(timeout=15)


@db_app.command("upgrade")
def db_upgrade():
    """Apply all pending migrations."""
    db.upgrade()
    typer.echo("Database is up to date.")


@db_app.command("revision")
def db_revision(message: Annotated[str, typer.Option("-m", help="What the migration does")]):
    """Developer tool: write a migration from the model changes."""
    db.upgrade()
    db.make_revision(message)


@db_app.command("import-legacy")
def db_import_legacy():
    """Add videos processed by older versions (JSON files in the data folder) to the database."""
    from .legacy import import_legacy

    db.upgrade()
    for line in import_legacy() or ["Nothing to import."]:
        typer.echo(line)


@app.command()
def run(src: Annotated[str, typer.Argument(help="Video URL or local file path")],
        n: Annotated[int | None, typer.Option(help="How many clips to make")] = None,
        min_len: Annotated[int | None, typer.Option(help="Shortest clip, seconds")] = None,
        max_len: Annotated[int | None, typer.Option(help="Longest clip, seconds")] = None,
        brief: Annotated[str | None, typer.Option(help="Path to a campaign brief text file")] = None):
    """Process one video in the foreground: fetch, transcribe, select, render, check."""
    from pathlib import Path

    from . import pipeline
    from . import settings as settings_mod
    from .ffutil import ToolError
    from .jobs import ConsoleReporter

    db.upgrade()
    rep = ConsoleReporter()
    try:
        settings = settings_mod.load()
        brief_text = Path(brief).read_text() if brief else None
        rep.stage("download")
        video_id = pipeline.ingest(src, settings, rep, brief=brief_text)
        rep.stage("transcribe")
        pipeline.transcribe(video_id, settings, rep)
        rep.stage("subtitles")
        pipeline.detect_subtitles(video_id, rep)
        rep.stage("select")
        pipeline.select(video_id, settings, rep, n, min_len, max_len)
        rep.stage("render")
        clip_ids = pipeline.render(video_id, settings, rep)
        if settings.auto_compliance:
            rep.stage("check")
            pipeline.check(video_id, settings, rep, clip_ids, skip_without_brief=True)
    except (ToolError, FileNotFoundError, RuntimeError, ValueError) as err:
        typer.secho(f"Error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from err
    typer.echo(f"Done. Clips are in {settings_mod.clips_dir(settings) / video_id}")


@app.command()
def scout():
    """Read public campaign listings now, in the foreground, and add them to Campaigns."""
    from . import scout as scout_mod
    from . import settings as settings_mod
    from .jobs import ConsoleReporter

    db.upgrade()
    rep = ConsoleReporter()
    rep.stage("scout")
    try:
        result = scout_mod.run(settings_mod.load(), rep)
    except RuntimeError as err:
        typer.secho(f"Error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from err
    typer.echo(f"{scout_mod.outcome(result)}. {result['new']} new campaigns, {result['read']} listings read.")
