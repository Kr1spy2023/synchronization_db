"""Панель синхронизации БД: Flask + Jinja2 поверх модуля db_sync."""

import os
import secrets
import threading
import time

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

from db_sync import DbSync, SyncError, ValidationError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROWS_ON_PAGE = 50


def create_app(local_url=None, remote_url=None, secret_key=None, history_path=None, driver=None):
    app = Flask(__name__)
    app.secret_key = secret_key or os.environ.get("PANEL_SECRET", "dev-secret-change-me")
    local_url = local_url or os.environ.get(
        "LOCAL_URL", f"sqlite:///{os.path.join(BASE_DIR, 'local.db')}"
    )
    remote_url = remote_url or os.environ.get(
        "REMOTE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'remote.db')}"
    )
    history_path = history_path or os.environ.get(
        "SYNC_HISTORY", os.path.join(BASE_DIR, "sync_history.jsonl")
    )
    # Один объект синхронизации на всё приложение; соединения не потокобезопасны, поэтому замок.
    sync = DbSync.from_urls(local_url, remote_url, history_path=history_path)
    lock = threading.RLock()
    app.extensions["sync"] = sync

    @app.before_request
    def csrf_protect():
        if request.method == "POST":
            token = session.get("csrf")
            if not token or request.form.get("csrf") != token:
                abort(400, "Неверный CSRF-токен")

    @app.context_processor
    def inject_globals():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return {
            "csrf_token": session["csrf"],
            "local_name": sync.local.describe(),
            "remote_name": sync.remote.describe(),
        }

    @app.template_filter("ts")
    def ts_filter(seconds):
        return time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(seconds))

    @app.errorhandler(400)
    def on_bad_request(err):
        return render_template("error.html", code=400, message=err.description), 400

    @app.errorhandler(404)
    def on_not_found(err):
        return (
            render_template("error.html", code=404, message="Страница или таблица не найдена"),
            404,
        )

    def read_options(form):
        """Параметры плана из формы: направление, таблицы, удаление лишних строк."""
        direction = form.get("direction", "")
        if direction not in ("push", "pull"):
            abort(400, "Направление: push или pull")
        tables = form.getlist("tables") or None
        return direction, tables, form.get("delete_missing") == "1"

    @app.get("/")
    def index():
        with lock:
            try:
                status = sync.status()
            except SyncError as err:
                flash(str(err), "error")
                status = []
        return render_template("index.html", status=status)

    @app.post("/plan")
    def plan_view():
        direction, tables, delete_missing = read_options(request.form)
        with lock:
            try:
                plan = sync.plan(direction, tables, delete_missing=delete_missing)
            except ValidationError as err:
                flash(str(err), "error")
                return redirect(url_for("index"))
        return render_template(
            "plan.html",
            plan=plan,
            tables=tables or [],
            delete_missing=delete_missing,
            previews=[t.preview() for t in plan.tables],
        )

    @app.post("/apply")
    def apply_view():
        direction, tables, delete_missing = read_options(request.form)
        with lock:
            plan = sync.plan(direction, tables, delete_missing=delete_missing)
            if plan.signature != request.form.get("signature"):
                flash("Данные изменились после построения плана. Постройте план заново.", "error")
                return redirect(url_for("index"))
            if request.form.get("confirm") != "1":
                flash("Подтвердите запись: поставьте галочку под планом.", "error")
                return redirect(url_for("index"))
            result = sync.apply(plan, confirm=True)
        return render_template("result.html", result=result, plan=plan)

    @app.get("/history")
    def history():
        return render_template("history.html", items=DbSync.read_history_file(history_path, 30))

    @app.get("/table/<side>/<name>")
    def table_view(side, name):
        if side not in ("local", "remote"):
            abort(404)
        adapter = sync.local if side == "local" else sync.remote
        with lock:
            if name not in adapter.tables():
                abort(404)
            columns = adapter.columns(name)
            names = [c.name for c in columns]
            order = [
                c.name for c in sorted((c for c in columns if c.pk), key=lambda c: c.pk)
            ] or names[:1]
            rows = adapter.rows(name, names, order)[:ROWS_ON_PAGE]
        return render_template("table.html", side=side, name=name, columns=names, rows=rows)

    return app


if __name__ == "__main__":
    create_app().run(port=5000, debug=False)
