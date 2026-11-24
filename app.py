import csv
import io
import math
import os
import secrets
import sqlite3
from datetime import date
from pathlib import Path

from flask import Flask, Response, current_app, flash, g, redirect, render_template, request, url_for


BASE_DIR = Path(__file__).resolve().parent
LOW_STOCK_LIMIT = 5


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        DATABASE=str(Path(app.instance_path) / "estoque.db"),
    )
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    app.add_template_filter(
        lambda value: f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "currency",
    )
    init_db(app)

    @app.get("/")
    def dashboard():
        db = get_db()
        totals = db.execute(
            """
            SELECT COUNT(*) AS produtos,
                   COALESCE(SUM(quantidade * preco), 0) AS valor_estoque,
                   COALESCE(SUM(quantidade), 0) AS unidades
            FROM produtos
            """
        ).fetchone()
        low_stock = db.execute(
            "SELECT * FROM produtos WHERE quantidade <= ? ORDER BY quantidade, nome",
            (LOW_STOCK_LIMIT,),
        ).fetchall()
        recent = db.execute(
            """
            SELECT m.*, p.codigo, p.nome AS produto_nome
            FROM movimentacoes m
            JOIN produtos p ON p.id = m.produto_id
            ORDER BY m.data DESC, m.id DESC
            LIMIT 8
            """
        ).fetchall()
        return render_template(
            "dashboard.html",
            totals=totals,
            low_stock=low_stock,
            recent=recent,
            low_stock_limit=LOW_STOCK_LIMIT,
        )

    @app.route("/produtos", methods=["GET", "POST"])
    def products():
        db = get_db()
        if request.method == "POST":
            codigo = request.form.get("codigo", "").strip()
            nome = request.form.get("nome", "").strip()
            categoria = request.form.get("categoria", "").strip()
            preco = parse_price(request.form.get("preco", ""))
            if not all((codigo, nome, categoria)) or preco is None or preco < 0:
                flash("Preencha todos os campos e informe um preço válido, igual ou maior que zero.", "error")
            else:
                try:
                    db.execute(
                        "INSERT INTO produtos (codigo, nome, categoria, preco) VALUES (?, ?, ?, ?)",
                        (codigo, nome, categoria, preco),
                    )
                    db.commit()
                    flash("Produto cadastrado com sucesso.", "success")
                    return redirect(url_for("products"))
                except sqlite3.IntegrityError as error:
                    db.rollback()
                    if "UNIQUE constraint failed: produtos.codigo" in str(error):
                        flash("Já existe um produto com esse código.", "error")
                    else:
                        raise

        category = request.args.get("categoria", "").strip()
        search = request.args.get("busca", "").strip()
        categories = db.execute(
            "SELECT DISTINCT categoria FROM produtos ORDER BY categoria"
        ).fetchall()
        query = "SELECT * FROM produtos WHERE 1 = 1"
        parameters = []
        if category:
            query += " AND categoria = ?"
            parameters.append(category)
        if search:
            query += " AND (codigo LIKE ? OR nome LIKE ?)"
            pattern = f"%{search}%"
            parameters.extend((pattern, pattern))
        query += " ORDER BY nome"
        product_rows = db.execute(query, parameters).fetchall()
        return render_template(
            "products.html",
            products=product_rows,
            categories=categories,
            selected_category=category,
            search=search,
        )

    @app.route("/movimentacoes", methods=["GET", "POST"])
    def movements():
        db = get_db()
        if request.method == "POST":
            product_id = request.form.get("produto_id", type=int)
            kind = request.form.get("tipo", "")
            quantity = request.form.get("quantidade", type=int)
            movement_date = request.form.get("data", "").strip()
            if kind not in ("entrada", "saida") or not product_id or not quantity or quantity < 1:
                flash("Selecione um produto, um tipo de movimentação e uma quantidade inteira maior que zero.", "error")
            elif movement_date and not valid_date(movement_date):
                flash("Informe uma data válida.", "error")
            else:
                try:
                    if movement_date:
                        db.execute(
                            "INSERT INTO movimentacoes (tipo, quantidade, produto_id, data) VALUES (?, ?, ?, ?)",
                            (kind, quantity, product_id, movement_date),
                        )
                    else:
                        db.execute(
                            "INSERT INTO movimentacoes (tipo, quantidade, produto_id) VALUES (?, ?, ?)",
                            (kind, quantity, product_id),
                        )
                    db.commit()
                    flash("Movimentação registrada com sucesso.", "success")
                    return redirect(url_for("movements"))
                except sqlite3.IntegrityError as error:
                    db.rollback()
                    message = str(error)
                    if "Estoque insuficiente" in message:
                        flash(message, "error")
                    elif "FOREIGN KEY constraint failed" in message:
                        flash("O produto selecionado não existe.", "error")
                    else:
                        raise

        product_rows = db.execute(
            "SELECT id, codigo, nome, quantidade FROM produtos ORDER BY nome"
        ).fetchall()
        movement_rows = db.execute(
            """
            SELECT m.*, p.codigo, p.nome AS produto_nome
            FROM movimentacoes m
            JOIN produtos p ON p.id = m.produto_id
            ORDER BY m.data DESC, m.id DESC
            """
        ).fetchall()
        return render_template(
            "movements.html", products=product_rows, movements=movement_rows
        )

    @app.get("/relatorios")
    def reports():
        db = get_db()
        categories = db.execute(
            """
            SELECT categoria, SUM(quantidade) AS unidades,
                   SUM(quantidade * preco) AS valor
            FROM produtos
            GROUP BY categoria
            ORDER BY valor DESC, categoria
            """
        ).fetchall()
        max_value = max((row["unidades"] for row in categories), default=0)
        return render_template(
            "reports.html",
            categories=categories,
            max_value=max_value,
            totals=db.execute(
                """
                SELECT COUNT(*) AS produtos,
                       COALESCE(SUM(quantidade * preco), 0) AS valor_estoque,
                       COALESCE(SUM(quantidade), 0) AS unidades
                FROM produtos
                """
            ).fetchone(),
        )

    @app.get("/relatorios/estoque.csv")
    def export_csv():
        category = request.args.get("categoria", "").strip()
        query = "SELECT codigo, nome, categoria, preco, quantidade FROM produtos"
        parameters = []
        if category:
            query += " WHERE categoria = ?"
            parameters.append(category)
        query += " ORDER BY nome"
        rows = get_db().execute(query, parameters).fetchall()

        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(("Código", "Nome", "Categoria", "Preço", "Quantidade", "Valor em estoque"))
        for row in rows:
            writer.writerow(
                (
                    row["codigo"],
                    row["nome"],
                    row["categoria"],
                    f'{row["preco"]:.2f}',
                    row["quantidade"],
                    f'{row["preco"] * row["quantidade"]:.2f}',
                )
            )
        return Response(
            output.getvalue(),
            content_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=relatorio_estoque.csv"},
        )

    @app.teardown_appcontext
    def close_db(_error=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    return app


def parse_price(value):
    try:
        price = float(value.strip().replace(",", "."))
        return price if math.isfinite(price) and price >= 0 else None
    except (AttributeError, ValueError):
        return None


def valid_date(value):
    try:
        date.fromisoformat(value)
        return len(value) == 10
    except ValueError:
        return False


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def init_db(app):
    with sqlite3.connect(app.config["DATABASE"]) as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript((BASE_DIR / "schema.sql").read_text(encoding="utf-8"))


create_app_instance = create_app()


if __name__ == "__main__":
    create_app_instance.run(debug=False)
