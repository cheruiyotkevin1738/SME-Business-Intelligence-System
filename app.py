import csv
import io
import math
from datetime import date
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, make_response, session
)
from werkzeug.security import generate_password_hash, check_password_hash

from database import initialize_database, get_connection


app = Flask(__name__)
app.secret_key = "sme_business_intelligence_project_change_this_secret"
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

initialize_database()

PRODUCTS_PER_PAGE = 10

EXPENSE_CATEGORIES = [
    "Rent",
    "Transport",
    "Salaries",
    "Utilities",
    "Marketing",
    "Stock Purchase",
    "Maintenance",
    "Other"
]


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access the system.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped_view


def current_user_id():
    return session.get("user_id")


def create_csv_response(filename, headers, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)

    response = make_response("\ufeff" + output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{filename}"'
    )
    return response


def valid_date(value):
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user_id():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not full_name or not email or not password:
            flash("Please fill in all required fields.", "error")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash("Your password must contain at least 8 characters.", "error")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("The passwords do not match.", "error")
            return redirect(url_for("register"))

        try:
            with get_connection() as conn:
                existing_users = conn.execute(
                    "SELECT COUNT(*) FROM users"
                ).fetchone()[0]

                cursor = conn.execute(
                    """
                    INSERT INTO users (full_name, email, password_hash)
                    VALUES (?, ?, ?)
                    """,
                    (full_name, email, generate_password_hash(password))
                )

                new_user_id = cursor.lastrowid

                if existing_users == 0:
                    for table in ["products", "sales", "suppliers", "expenses"]:
                        conn.execute(
                            f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL",
                            (new_user_id,)
                        )

            flash("Account created. You can now log in.", "success")
            return redirect(url_for("login"))

        except Exception:
            flash("That email may already be registered.", "error")
            return redirect(url_for("register"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user_id():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        with get_connection() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["full_name"]
            flash("Welcome back!", "success")
            return redirect(url_for("dashboard"))

        flash("Incorrect email or password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    user_id = current_user_id()

    with get_connection() as conn:
        product_count = conn.execute(
            "SELECT COUNT(*) FROM products WHERE user_id = ?",
            (user_id,)
        ).fetchone()[0]

        total_revenue = conn.execute(
            """
            SELECT COALESCE(SUM(quantity * unit_price), 0)
            FROM sales WHERE user_id = ?
            """,
            (user_id,)
        ).fetchone()[0]

        total_expenses = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id = ?",
            (user_id,)
        ).fetchone()[0]

        low_stock_count = conn.execute(
            """
            SELECT COUNT(*) FROM products
            WHERE user_id = ? AND quantity > 0
            AND quantity <= reorder_level
            """,
            (user_id,)
        ).fetchone()[0]

    return render_template(
        "dashboard.html",
        product_count=product_count,
        total_revenue=total_revenue,
        total_expenses=total_expenses,
        low_stock_count=low_stock_count
    )


@app.route("/products")
@login_required
def products():
    user_id = current_user_id()
    search = request.args.get("search", "").strip()
    category = request.args.get("category", "").strip()
    stock_status = request.args.get("stock", "").strip().lower()

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1

    conditions = ["user_id = ?"]
    parameters = [user_id]

    if search:
        conditions.append("(name LIKE ? OR category LIKE ?)")
        search_term = f"%{search}%"
        parameters.extend([search_term, search_term])

    if category:
        conditions.append("category = ?")
        parameters.append(category)

    if stock_status == "in_stock":
        conditions.append("quantity > reorder_level")
    elif stock_status == "low_stock":
        conditions.append("quantity > 0 AND quantity <= reorder_level")
    elif stock_status == "out_of_stock":
        conditions.append("quantity <= 0")

    where_clause = " AND ".join(conditions)

    with get_connection() as conn:
        categories = conn.execute(
            """
            SELECT DISTINCT category FROM products
            WHERE user_id = ? AND category IS NOT NULL AND TRIM(category) != ''
            ORDER BY category
            """,
            (user_id,)
        ).fetchall()

        low_stock_products = conn.execute(
            """
            SELECT * FROM products
            WHERE user_id = ? AND quantity > 0
            AND quantity <= reorder_level
            ORDER BY quantity ASC
            """,
            (user_id,)
        ).fetchall()

        out_of_stock_products = conn.execute(
            """
            SELECT * FROM products
            WHERE user_id = ? AND quantity <= 0
            ORDER BY name
            """,
            (user_id,)
        ).fetchall()

        total_products = conn.execute(
            f"SELECT COUNT(*) FROM products WHERE {where_clause}",
            parameters
        ).fetchone()[0]

        total_pages = max(1, math.ceil(total_products / PRODUCTS_PER_PAGE))
        page = min(page, total_pages)
        offset = (page - 1) * PRODUCTS_PER_PAGE

        product_list = conn.execute(
            f"""
            SELECT * FROM products
            WHERE {where_clause}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            parameters + [PRODUCTS_PER_PAGE, offset]
        ).fetchall()

        total_inventory_count = conn.execute(
            "SELECT COUNT(*) FROM products WHERE user_id = ?",
            (user_id,)
        ).fetchone()[0]

    return render_template(
        "products.html",
        products=product_list,
        low_stock_products=low_stock_products,
        out_of_stock_products=out_of_stock_products,
        low_stock_count=len(low_stock_products),
        out_of_stock_count=len(out_of_stock_products),
        categories=categories,
        search=search,
        selected_category=category,
        selected_stock=stock_status,
        page=page,
        total_pages=total_pages,
        total_products=total_products,
        total_inventory_count=total_inventory_count
    )


@app.route("/products/add", methods=["POST"])
@login_required
def add_product():
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()

    try:
        buying_price = float(request.form.get("buying_price", 0))
        selling_price = float(request.form.get("selling_price", 0))
        quantity = int(request.form.get("quantity", 0))
        reorder_level = int(request.form.get("reorder_level", 5))

        if (
            not name or buying_price < 0 or selling_price < 0
            or quantity < 0 or reorder_level < 0
        ):
            raise ValueError

        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO products
                (name, category, buying_price, selling_price,
                 quantity, reorder_level, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name, category, buying_price, selling_price,
                    quantity, reorder_level, current_user_id()
                )
            )

        flash("Product added successfully.", "success")

    except (ValueError, TypeError):
        flash("Please enter valid product details.", "error")

    return redirect(url_for("products"))


@app.route("/products/import", methods=["POST"])
@login_required
def import_products():
    uploaded_file = request.files.get("csv_file")

    if not uploaded_file or uploaded_file.filename == "":
        flash("Please select a CSV file to import.", "error")
        return redirect(url_for("products"))

    if not uploaded_file.filename.lower().endswith(".csv"):
        flash("Please upload a .csv file.", "error")
        return redirect(url_for("products"))

    try:
        content = uploaded_file.stream.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))

        if not reader.fieldnames:
            flash("The CSV file is empty or has no header row.", "error")
            return redirect(url_for("products"))

        reader.fieldnames = [
            (field or "").strip().lower() for field in reader.fieldnames
        ]

        required_columns = {
            "name", "buying_price", "selling_price", "quantity"
        }

        if not required_columns.issubset(set(reader.fieldnames)):
            flash(
                "CSV needs: name, buying_price, selling_price, quantity. "
                "category and reorder_level are optional.",
                "error"
            )
            return redirect(url_for("products"))

        imported = 0
        skipped = 0
        errors = 0
        seen_names = set()

        with get_connection() as conn:
            existing_names = {
                row["name"].strip().casefold()
                for row in conn.execute(
                    "SELECT name FROM products WHERE user_id = ?",
                    (current_user_id(),)
                ).fetchall()
            }

            for row_number, row in enumerate(reader, start=2):
                if row_number > 5001:
                    flash("Import limit is 5,000 rows per file.", "error")
                    break

                try:
                    name = (row.get("name") or "").strip()
                    category = (row.get("category") or "").strip()

                    buying_price = float(row.get("buying_price", ""))
                    selling_price = float(row.get("selling_price", ""))
                    quantity = int(row.get("quantity", ""))

                    reorder_value = (row.get("reorder_level") or "").strip()
                    reorder_level = int(reorder_value) if reorder_value else 5

                    if (
                        not name or buying_price < 0 or selling_price < 0
                        or quantity < 0 or reorder_level < 0
                    ):
                        raise ValueError

                    normalized_name = name.casefold()

                    if (
                        normalized_name in existing_names
                        or normalized_name in seen_names
                    ):
                        skipped += 1
                        continue

                    conn.execute(
                        """
                        INSERT INTO products
                        (name, category, buying_price, selling_price,
                         quantity, reorder_level, user_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            name, category, buying_price, selling_price,
                            quantity, reorder_level, current_user_id()
                        )
                    )

                    seen_names.add(normalized_name)
                    imported += 1

                except (ValueError, TypeError, AttributeError):
                    errors += 1

        flash(
            f"Import complete: {imported} added, {skipped} duplicates skipped, "
            f"{errors} invalid rows skipped.",
            "success" if imported else "error"
        )

    except UnicodeDecodeError:
        flash("Save the file as UTF-8 CSV and try again.", "error")
    except Exception:
        flash("The CSV file could not be imported. Check its format.", "error")

    return redirect(url_for("products"))


@app.route("/products/delete/<int:product_id>", methods=["POST"])
@login_required
def delete_product(product_id):
    with get_connection() as conn:
        product = conn.execute(
            "SELECT id FROM products WHERE id = ? AND user_id = ?",
            (product_id, current_user_id())
        ).fetchone()

        if product is None:
            flash("Product not found.", "error")
        else:
            sales_count = conn.execute(
                """
                SELECT COUNT(*) FROM sales
                WHERE product_id = ? AND user_id = ?
                """,
                (product_id, current_user_id())
            ).fetchone()[0]

            if sales_count:
                flash(
                    "This product has sales records and cannot be deleted.",
                    "error"
                )
            else:
                conn.execute(
                    "DELETE FROM products WHERE id = ? AND user_id = ?",
                    (product_id, current_user_id())
                )
                flash("Product deleted successfully.", "success")

    return redirect(url_for("products"))


@app.route("/sales", methods=["GET", "POST"])
@login_required
def sales():
    user_id = current_user_id()

    if request.method == "POST":
        try:
            product_id = int(request.form.get("product_id"))
            quantity = int(request.form.get("quantity"))

            if quantity <= 0:
                raise ValueError

            with get_connection() as conn:
                product = conn.execute(
                    "SELECT * FROM products WHERE id = ? AND user_id = ?",
                    (product_id, user_id)
                ).fetchone()

                if product is None:
                    flash("Product not found.", "error")
                elif product["quantity"] < quantity:
                    flash("Not enough stock available.", "error")
                else:
                    conn.execute(
                        """
                        INSERT INTO sales
                        (product_id, quantity, unit_price, cost_price, user_id)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            product_id,
                            quantity,
                            product["selling_price"],
                            product["buying_price"],
                            user_id
                        )
                    )

                    conn.execute(
                        """
                        UPDATE products SET quantity = quantity - ?
                        WHERE id = ? AND user_id = ?
                        """,
                        (quantity, product_id, user_id)
                    )

                    flash("Sale recorded successfully.", "success")

        except (ValueError, TypeError):
            flash("Please enter valid sale details.", "error")

        return redirect(url_for("sales"))

    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    start_value = valid_date(start_date) if start_date else None
    end_value = valid_date(end_date) if end_date else None

    if start_date and not start_value:
        flash("The start date is invalid.", "error")
        start_date = ""

    if end_date and not end_value:
        flash("The end date is invalid.", "error")
        end_date = ""

    if start_value and end_value and start_value > end_value:
        flash("Start date cannot be later than end date.", "error")
        start_date = ""
        end_date = ""
        start_value = None
        end_value = None

    conditions = ["sales.user_id = ?"]
    parameters = [user_id]

    if start_date:
        conditions.append("date(sales.sale_date) >= date(?)")
        parameters.append(start_date)

    if end_date:
        conditions.append("date(sales.sale_date) <= date(?)")
        parameters.append(end_date)

    where_clause = " AND ".join(conditions)

    with get_connection() as conn:
        product_list = conn.execute(
            "SELECT * FROM products WHERE user_id = ? ORDER BY name",
            (user_id,)
        ).fetchall()

        sales_records = conn.execute(
            f"""
            SELECT sales.id, products.name AS product_name,
                   sales.quantity, sales.unit_price,
                   sales.quantity * sales.unit_price AS total,
                   sales.sale_date
            FROM sales
            JOIN products ON sales.product_id = products.id
            WHERE {where_clause}
            ORDER BY sales.id DESC
            """,
            parameters
        ).fetchall()

        summary = conn.execute(
            f"""
            SELECT
                COALESCE(SUM(sales.quantity * sales.unit_price), 0) AS revenue,
                COALESCE(SUM(sales.quantity), 0) AS units_sold,
                COUNT(*) AS transactions
            FROM sales
            WHERE {where_clause}
            """,
            parameters
        ).fetchone()

        top_products = conn.execute(
            f"""
            SELECT products.name,
                   SUM(sales.quantity) AS units_sold,
                   SUM(sales.quantity * sales.unit_price) AS revenue
            FROM sales
            JOIN products ON sales.product_id = products.id
            WHERE {where_clause}
            GROUP BY products.id
            ORDER BY units_sold DESC
            LIMIT 5
            """,
            parameters
        ).fetchall()

    return render_template(
        "sales.html",
        products=product_list,
        sales=sales_records,
        total_revenue=summary["revenue"],
        units_sold=summary["units_sold"],
        transaction_count=summary["transactions"],
        top_products=top_products,
        start_date=start_date,
        end_date=end_date
    )


@app.route("/suppliers", methods=["GET", "POST"])
@login_required
def suppliers():
    user_id = current_user_id()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()

        if name:
            with get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO suppliers (name, phone, email, user_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (name, phone, email, user_id)
                )
            flash("Supplier added successfully.", "success")
        else:
            flash("Supplier name is required.", "error")

        return redirect(url_for("suppliers"))

    with get_connection() as conn:
        supplier_list = conn.execute(
            "SELECT * FROM suppliers WHERE user_id = ? ORDER BY id DESC",
            (user_id,)
        ).fetchall()

    return render_template("suppliers.html", suppliers=supplier_list)


@app.route("/suppliers/delete/<int:supplier_id>", methods=["POST"])
@login_required
def delete_supplier(supplier_id):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM suppliers WHERE id = ? AND user_id = ?",
            (supplier_id, current_user_id())
        )

    flash("Supplier deleted successfully.", "success")
    return redirect(url_for("suppliers"))


@app.route("/expenses", methods=["GET", "POST"])
@login_required
def expenses():
    user_id = current_user_id()

    if request.method == "POST":
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "Other").strip()

        try:
            amount = float(request.form.get("amount", 0))

            if (
                not description
                or amount <= 0
                or category not in EXPENSE_CATEGORIES
            ):
                raise ValueError

            with get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO expenses
                    (description, amount, category, user_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (description, amount, category, user_id)
                )

            flash("Expense recorded successfully.", "success")

        except (ValueError, TypeError):
            flash("Please enter a valid description, category and amount.", "error")

        return redirect(url_for("expenses"))

    with get_connection() as conn:
        expense_records = conn.execute(
            """
            SELECT * FROM expenses
            WHERE user_id = ?
            ORDER BY expense_date DESC, id DESC
            """,
            (user_id,)
        ).fetchall()

        total_expenses = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id = ?",
            (user_id,)
        ).fetchone()[0]

        category_totals = conn.execute(
            """
            SELECT category, SUM(amount) AS total
            FROM expenses
            WHERE user_id = ?
            GROUP BY category
            ORDER BY total DESC
            """,
            (user_id,)
        ).fetchall()

    return render_template(
        "expenses.html",
        expenses=expense_records,
        total_expenses=total_expenses,
        expense_categories=EXPENSE_CATEGORIES,
        category_totals=category_totals
    )


@app.route("/expenses/delete/<int:expense_id>", methods=["POST"])
@login_required
def delete_expense(expense_id):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM expenses WHERE id = ? AND user_id = ?",
            (expense_id, current_user_id())
        )

    flash("Expense deleted successfully.", "success")
    return redirect(url_for("expenses"))


@app.route("/analytics")
@login_required
def analytics():
    user_id = current_user_id()

    with get_connection() as conn:
        summary = conn.execute(
            """
            SELECT
                COALESCE(SUM(quantity * unit_price), 0) AS revenue,
                COALESCE(SUM(quantity * cost_price), 0) AS cost_of_goods,
                COALESCE(SUM(quantity), 0) AS units_sold
            FROM sales
            WHERE user_id = ?
            """,
            (user_id,)
        ).fetchone()

        revenue = summary["revenue"]
        cogs = summary["cost_of_goods"]
        units_sold = summary["units_sold"]
        gross_profit = revenue - cogs

        total_expenses = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id = ?",
            (user_id,)
        ).fetchone()[0]

        net_profit = gross_profit - total_expenses

        gross_margin = (
            (gross_profit / revenue) * 100 if revenue else 0
        )

        net_margin = (
            (net_profit / revenue) * 100 if revenue else 0
        )

        top_products_rows = conn.execute(
            """
            SELECT
                products.name,
                SUM(sales.quantity) AS units_sold,
                SUM(sales.quantity * sales.unit_price) AS revenue,
                SUM(sales.quantity * sales.cost_price) AS cost,
                SUM(sales.quantity * (sales.unit_price - sales.cost_price))
                    AS profit
            FROM sales
            JOIN products ON sales.product_id = products.id
            WHERE sales.user_id = ?
            GROUP BY products.id
            ORDER BY units_sold DESC
            LIMIT 5
            """,
            (user_id,)
        ).fetchall()

        product_profitability_rows = conn.execute(
            """
            SELECT
                products.name,
                SUM(sales.quantity) AS units_sold,
                SUM(sales.quantity * sales.unit_price) AS revenue,
                SUM(sales.quantity * sales.cost_price) AS cost,
                SUM(sales.quantity * (sales.unit_price - sales.cost_price))
                    AS profit
            FROM sales
            JOIN products ON sales.product_id = products.id
            WHERE sales.user_id = ?
            GROUP BY products.id
            ORDER BY profit DESC
            """,
            (user_id,)
        ).fetchall()

        monthly_sales_rows = conn.execute(
            """
            SELECT
                strftime('%Y-%m', sale_date) AS month,
                SUM(quantity * unit_price) AS revenue,
                SUM(quantity * cost_price) AS cost,
                SUM(quantity * (unit_price - cost_price)) AS gross_profit
            FROM sales
            WHERE user_id = ?
            GROUP BY strftime('%Y-%m', sale_date)
            ORDER BY month
            """,
            (user_id,)
        ).fetchall()

        monthly_expenses = conn.execute(
            """
            SELECT
                strftime('%Y-%m', expense_date) AS month,
                SUM(amount) AS expenses
            FROM expenses
            WHERE user_id = ?
            GROUP BY strftime('%Y-%m', expense_date)
            ORDER BY month
            """,
            (user_id,)
        ).fetchall()

        daily_sales = conn.execute(
            """
            SELECT
                date(sale_date) AS day,
                SUM(quantity * unit_price) AS revenue,
                SUM(quantity * cost_price) AS cost,
                SUM(quantity * (unit_price - cost_price)) AS gross_profit
            FROM sales
            WHERE user_id = ?
            GROUP BY date(sale_date)
            ORDER BY day DESC
            LIMIT 30
            """,
            (user_id,)
        ).fetchall()

        expense_breakdown_rows = conn.execute(
            """
            SELECT category, SUM(amount) AS total
            FROM expenses
            WHERE user_id = ?
            GROUP BY category
            ORDER BY total DESC
            """,
            (user_id,)
        ).fetchall()

        daily_expenses = conn.execute(
            """
            SELECT date(expense_date) AS day, SUM(amount) AS expenses
            FROM expenses
            WHERE user_id = ?
            GROUP BY date(expense_date)
            """,
            (user_id,)
        ).fetchall()

    # Convert SQLite rows into normal dictionaries.
    # This makes them safe for Jinja's tojson filter.
    top_products = [dict(row) for row in top_products_rows]
    product_profitability = [
        dict(row) for row in product_profitability_rows
    ]
    monthly_sales = [dict(row) for row in monthly_sales_rows]
    expense_breakdown = [
        dict(row) for row in expense_breakdown_rows
    ]

    # Combine monthly sales and expenses.
    expense_by_month = {
        row["month"]: row["expenses"] for row in monthly_expenses
    }

    financial_months = []

    for row in monthly_sales:
        month_expense = expense_by_month.get(row["month"], 0) or 0

        financial_months.append({
            "month": row["month"],
            "revenue": row["revenue"] or 0,
            "cost": row["cost"] or 0,
            "expenses": month_expense,
            "gross_profit": row["gross_profit"] or 0,
            "net_profit": (row["gross_profit"] or 0) - month_expense
        })

    existing_months = {
        row["month"] for row in financial_months
    }

    for month, expense_amount in expense_by_month.items():
        if month not in existing_months:
            financial_months.append({
                "month": month,
                "revenue": 0,
                "cost": 0,
                "expenses": expense_amount or 0,
                "gross_profit": 0,
                "net_profit": -(expense_amount or 0)
            })

    financial_months.sort(key=lambda item: item["month"])

    # Combine daily sales and expenses.
    expense_by_day = {
        row["day"]: row["expenses"] for row in daily_expenses
    }

    daily_profit_records = []

    for row in daily_sales:
        day_expense = expense_by_day.get(row["day"], 0) or 0

        daily_profit_records.append({
            "date": row["day"],
            "revenue": row["revenue"] or 0,
            "cost": row["cost"] or 0,
            "gross_profit": row["gross_profit"] or 0,
            "expenses": day_expense,
            "net_profit": (row["gross_profit"] or 0) - day_expense
        })

    existing_days = {
        row["date"] for row in daily_profit_records
    }

    for day, expense_amount in expense_by_day.items():
        if day not in existing_days:
            daily_profit_records.append({
                "date": day,
                "revenue": 0,
                "cost": 0,
                "gross_profit": 0,
                "expenses": expense_amount or 0,
                "net_profit": -(expense_amount or 0)
            })

    daily_profit_records.sort(
        key=lambda item: item["date"],
        reverse=True
    )
    daily_profit_records = daily_profit_records[:30]

    return render_template(
        "analytics.html",
        revenue=revenue,
        units_sold=units_sold,
        cogs=cogs,
        cost_of_goods=cogs,
        gross_profit=gross_profit,
        total_expenses=total_expenses,
        net_profit=net_profit,
        gross_margin=gross_margin,
        net_margin=net_margin,
        top_products=top_products,
        product_profitability=product_profitability,
        monthly_sales=monthly_sales,
        financial_months=financial_months,
        daily_profit_records=daily_profit_records,
        expense_breakdown=expense_breakdown
    )




# ---------------------------------------------------------
# PHASE 10: SALES FORECASTING
# ---------------------------------------------------------
@app.route("/forecast")
@login_required
def forecast():
    user_id = current_user_id()

    with get_connection() as conn:
        monthly_rows = conn.execute(
            """
            SELECT
                strftime('%Y-%m', sale_date) AS month,
                COALESCE(SUM(quantity * unit_price), 0) AS revenue
            FROM sales
            WHERE user_id = ?
            GROUP BY strftime('%Y-%m', sale_date)
            ORDER BY month
            """,
            (user_id,)
        ).fetchall()

    actual_months = [dict(row) for row in monthly_rows]
    forecast_months = []
    insight = "Record sales over multiple months to generate a meaningful forecast."

    if len(actual_months) >= 2:
        # Linear regression on sequential monthly observations.
        revenues = [float(row["revenue"] or 0) for row in actual_months]
        x_values = list(range(len(revenues)))
        x_mean = sum(x_values) / len(x_values)
        y_mean = sum(revenues) / len(revenues)

        denominator = sum((x - x_mean) ** 2 for x in x_values)
        slope = (
            sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, revenues))
            / denominator
            if denominator else 0
        )
        intercept = y_mean - slope * x_mean

        # Determine the next calendar month from the latest recorded month.
        latest_month = actual_months[-1]["month"]
        latest_year, latest_number = map(int, latest_month.split("-"))

        for step in range(1, 4):
            month_number = latest_number + step
            year = latest_year
            while month_number > 12:
                month_number -= 12
                year += 1

            forecast_index = len(revenues) - 1 + step
            predicted_revenue = max(0, intercept + slope * forecast_index)
            forecast_months.append({
                "month": f"{year:04d}-{month_number:02d}",
                "revenue": round(predicted_revenue, 2)
            })

        first_revenue = revenues[0]
        last_revenue = revenues[-1]
        if slope > 0:
            insight = "Your historical revenue trend is upward. The forecast suggests continued growth, though actual results may differ."
        elif slope < 0:
            insight = "Your historical revenue trend is downward. Review sales activity, pricing, and product demand."
        else:
            insight = "Your historical revenue trend is relatively stable. Focus on maintaining consistent sales."

    elif len(actual_months) == 1:
        insight = "Only one month of sales is available. Record sales in more months to enable trend-based forecasting."

    return render_template(
        "forecast.html",
        actual_months=actual_months,
        forecast_months=forecast_months,
        insight=insight
    )


def build_custom_report(report_type, user_id, start_date="", end_date=""):
    conditions = []
    parameters = [user_id]

    if report_type == "sales":
        conditions.append("sales.user_id = ?")

        if start_date:
            conditions.append("date(sales.sale_date) >= date(?)")
            parameters.append(start_date)

        if end_date:
            conditions.append("date(sales.sale_date) <= date(?)")
            parameters.append(end_date)

        with get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    sales.id,
                    products.name,
                    sales.quantity,
                    sales.unit_price,
                    sales.quantity * sales.unit_price AS revenue,
                    sales.cost_price,
                    sales.quantity * sales.cost_price AS cost,
                    sales.sale_date
                FROM sales
                JOIN products ON sales.product_id = products.id
                WHERE {" AND ".join(conditions)}
                ORDER BY sales.sale_date DESC
                """,
                parameters
            ).fetchall()

        headers = [
            "Sale ID", "Product", "Quantity", "Selling Price",
            "Revenue", "Cost Price", "Total Cost", "Sale Date"
        ]

        return "Sales Report", headers, [tuple(row) for row in rows]

    elif report_type == "expenses":
        conditions.append("user_id = ?")

        if start_date:
            conditions.append("date(expense_date) >= date(?)")
            parameters.append(start_date)

        if end_date:
            conditions.append("date(expense_date) <= date(?)")
            parameters.append(end_date)

        with get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT id, description, category, amount, expense_date
                FROM expenses
                WHERE {" AND ".join(conditions)}
                ORDER BY expense_date DESC
                """,
                parameters
            ).fetchall()

        headers = [
            "Expense ID", "Description", "Category", "Amount", "Date"
        ]

        return "Expense Report", headers, [tuple(row) for row in rows]

    elif report_type == "inventory":
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    id, name, category, buying_price, selling_price,
                    quantity, reorder_level,
                    quantity * buying_price AS stock_value
                FROM products
                WHERE user_id = ?
                ORDER BY name
                """,
                (user_id,)
            ).fetchall()

        headers = [
            "Product ID", "Product", "Category", "Buying Price",
            "Selling Price", "Quantity", "Reorder Level", "Stock Value"
        ]

        return "Inventory Report", headers, [tuple(row) for row in rows]

    elif report_type == "product_performance":
        conditions.append("sales.user_id = ?")

        if start_date:
            conditions.append("date(sales.sale_date) >= date(?)")
            parameters.append(start_date)

        if end_date:
            conditions.append("date(sales.sale_date) <= date(?)")
            parameters.append(end_date)

        with get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    products.name,
                    SUM(sales.quantity) AS units_sold,
                    SUM(sales.quantity * sales.unit_price) AS revenue,
                    SUM(sales.quantity * sales.cost_price) AS cost,
                    SUM(
                        sales.quantity * (sales.unit_price - sales.cost_price)
                    ) AS gross_profit
                FROM sales
                JOIN products ON sales.product_id = products.id
                WHERE {" AND ".join(conditions)}
                GROUP BY products.id, products.name
                ORDER BY gross_profit DESC
                """,
                parameters
            ).fetchall()

        headers = [
            "Product", "Units Sold", "Revenue", "Cost", "Gross Profit"
        ]

        return (
            "Product Performance Report",
            headers,
            [tuple(row) for row in rows]
        )

    elif report_type == "profit_loss":
        sales_conditions = ["user_id = ?"]
        expense_conditions = ["user_id = ?"]

        sales_parameters = [user_id]
        expense_parameters = [user_id]

        if start_date:
            sales_conditions.append("date(sale_date) >= date(?)")
            expense_conditions.append("date(expense_date) >= date(?)")
            sales_parameters.append(start_date)
            expense_parameters.append(start_date)

        if end_date:
            sales_conditions.append("date(sale_date) <= date(?)")
            expense_conditions.append("date(expense_date) <= date(?)")
            sales_parameters.append(end_date)
            expense_parameters.append(end_date)

        with get_connection() as conn:
            sales_summary = conn.execute(
                f"""
                SELECT
                    COALESCE(SUM(quantity * unit_price), 0) AS revenue,
                    COALESCE(SUM(quantity * cost_price), 0) AS cost
                FROM sales
                WHERE {" AND ".join(sales_conditions)}
                """,
                sales_parameters
            ).fetchone()

            total_expenses = conn.execute(
                f"""
                SELECT COALESCE(SUM(amount), 0)
                FROM expenses
                WHERE {" AND ".join(expense_conditions)}
                """,
                expense_parameters
            ).fetchone()[0]

        revenue = sales_summary["revenue"]
        cost = sales_summary["cost"]
        gross_profit = revenue - cost
        net_profit = gross_profit - total_expenses

        gross_margin = (gross_profit / revenue * 100) if revenue else 0
        net_margin = (net_profit / revenue * 100) if revenue else 0

        headers = ["Metric", "Amount"]

        rows = [
            ("Total Revenue", round(revenue, 2)),
            ("Cost of Goods Sold", round(cost, 2)),
            ("Gross Profit", round(gross_profit, 2)),
            ("Gross Profit Margin (%)", round(gross_margin, 2)),
            ("Total Expenses", round(total_expenses, 2)),
            ("Net Profit", round(net_profit, 2)),
            ("Net Profit Margin (%)", round(net_margin, 2))
        ]

        return "Profit and Loss Report", headers, rows

    return "Report", [], []


@app.route("/reports")
@login_required
def reports():
    report_type = request.args.get("type", "sales").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    allowed_reports = [
        "sales",
        "profit_loss",
        "expenses",
        "inventory",
        "product_performance"
    ]

    if report_type not in allowed_reports:
        report_type = "sales"

    start_value = valid_date(start_date) if start_date else None
    end_value = valid_date(end_date) if end_date else None

    if (start_date and not start_value) or (end_date and not end_value):
        flash("Please enter valid dates.", "error")
        return redirect(url_for("reports", type=report_type))

    if start_value and end_value and start_value > end_value:
        flash("Start date cannot be later than end date.", "error")
        return redirect(url_for("reports", type=report_type))

    title, headers, rows = build_custom_report(
        report_type,
        current_user_id(),
        start_date,
        end_date
    )

    return render_template(
        "reports.html",
        title=title,
        report_type=report_type,
        headers=headers,
        rows=rows,
        start_date=start_date,
        end_date=end_date
    )


@app.route("/reports/export")
@login_required
def export_custom_report():
    report_type = request.args.get("type", "sales").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    allowed_reports = [
        "sales",
        "profit_loss",
        "expenses",
        "inventory",
        "product_performance"
    ]

    if report_type not in allowed_reports:
        flash("Invalid report type.", "error")
        return redirect(url_for("reports"))

    start_value = valid_date(start_date) if start_date else None
    end_value = valid_date(end_date) if end_date else None

    if (start_date and not start_value) or (end_date and not end_value):
        flash("Please enter valid dates.", "error")
        return redirect(url_for("reports", type=report_type))

    if start_value and end_value and start_value > end_value:
        flash("Start date cannot be later than end date.", "error")
        return redirect(url_for("reports", type=report_type))

    title, headers, rows = build_custom_report(
        report_type,
        current_user_id(),
        start_date,
        end_date
    )

    filename = report_type + "_report.csv"

    return create_csv_response(filename, headers, rows)

@app.route("/export/products")
@login_required
def export_products():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, name, category, buying_price,
                   selling_price, quantity, reorder_level
            FROM products
            WHERE user_id = ?
            ORDER BY id
            """,
            (current_user_id(),)
        ).fetchall()

    return create_csv_response(
        "product_inventory.csv",
        [
            "Product ID", "Product Name", "Category",
            "Buying Price", "Selling Price", "Quantity", "Reorder Level"
        ],
        [tuple(row) for row in rows]
    )


@app.route("/export/sales")
@login_required
def export_sales():
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    start_value = valid_date(start_date) if start_date else None
    end_value = valid_date(end_date) if end_date else None

    if (start_date and not start_value) or (end_date and not end_value):
        flash("Invalid date filter. Please try again.", "error")
        return redirect(url_for("sales"))

    if start_value and end_value and start_value > end_value:
        flash("Start date cannot be later than end date.", "error")
        return redirect(url_for("sales"))

    conditions = ["sales.user_id = ?"]
    parameters = [current_user_id()]

    if start_date:
        conditions.append("date(sales.sale_date) >= date(?)")
        parameters.append(start_date)

    if end_date:
        conditions.append("date(sales.sale_date) <= date(?)")
        parameters.append(end_date)

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT sales.id, products.name, sales.quantity,
                   sales.unit_price, sales.cost_price,
                   sales.quantity * sales.unit_price,
                   sales.quantity * sales.cost_price,
                   sales.sale_date
            FROM sales
            JOIN products ON sales.product_id = products.id
            WHERE {" AND ".join(conditions)}
            ORDER BY sales.id
            """,
            parameters
        ).fetchall()

    return create_csv_response(
        "sales_report.csv",
        [
            "Sale ID", "Product Name", "Quantity", "Selling Price",
            "Cost Price", "Total Revenue", "Total Cost", "Sale Date"
        ],
        [tuple(row) for row in rows]
    )


@app.route("/export/expenses")
@login_required
def export_expenses():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, description, category, amount, expense_date
            FROM expenses
            WHERE user_id = ?
            ORDER BY id
            """,
            (current_user_id(),)
        ).fetchall()

    return create_csv_response(
        "expenses_report.csv",
        ["Expense ID", "Description", "Category", "Amount", "Date"],
        [tuple(row) for row in rows]
    )


@app.route("/export/analytics")
@login_required
def export_analytics():
    user_id = current_user_id()

    with get_connection() as conn:
        summary = conn.execute(
            """
            SELECT
                COALESCE(SUM(quantity * unit_price), 0) AS revenue,
                COALESCE(SUM(quantity * cost_price), 0) AS cost,
                COALESCE(SUM(quantity), 0) AS units
            FROM sales
            WHERE user_id = ?
            """,
            (user_id,)
        ).fetchone()

        revenue = summary["revenue"]
        cost_of_goods = summary["cost"]
        units_sold = summary["units"]
        gross_profit = revenue - cost_of_goods

        total_expenses = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id = ?",
            (user_id,)
        ).fetchone()[0]

        top_products = conn.execute(
            """
            SELECT
                products.name,
                SUM(sales.quantity),
                SUM(sales.quantity * sales.unit_price),
                SUM(sales.quantity * sales.cost_price),
                SUM(sales.quantity * (sales.unit_price - sales.cost_price))
            FROM sales
            JOIN products ON sales.product_id = products.id
            WHERE sales.user_id = ?
            GROUP BY products.id
            ORDER BY SUM(sales.quantity) DESC
            LIMIT 5
            """,
            (user_id,)
        ).fetchall()

        monthly_sales = conn.execute(
            """
            SELECT
                strftime('%Y-%m', sale_date),
                SUM(quantity * unit_price),
                SUM(quantity * cost_price),
                SUM(quantity * (unit_price - cost_price))
            FROM sales
            WHERE user_id = ?
            GROUP BY strftime('%Y-%m', sale_date)
            ORDER BY strftime('%Y-%m', sale_date)
            """,
            (user_id,)
        ).fetchall()

        expense_breakdown = conn.execute(
            """
            SELECT category, SUM(amount)
            FROM expenses
            WHERE user_id = ?
            GROUP BY category
            ORDER BY SUM(amount) DESC
            """,
            (user_id,)
        ).fetchall()

    net_profit = gross_profit - total_expenses
    gross_margin = (gross_profit / revenue * 100) if revenue else 0
    net_margin = (net_profit / revenue * 100) if revenue else 0

    report_rows = [
        ["BUSINESS FINANCIAL PERFORMANCE", ""],
        ["Metric", "Value"],
        ["Total Revenue", revenue],
        ["Units Sold", units_sold],
        ["Cost of Goods Sold", cost_of_goods],
        ["Gross Profit", gross_profit],
        ["Gross Profit Margin (%)", gross_margin],
        ["Total Expenses", total_expenses],
        ["Net Profit", net_profit],
        ["Net Profit Margin (%)", net_margin],
        ["", ""],
        ["EXPENSE BREAKDOWN", ""],
        ["Category", "Amount"]
    ]

    for row in expense_breakdown:
        report_rows.append(list(row))

    report_rows.extend([
        ["", ""],
        ["TOP PRODUCTS", ""],
        ["Product", "Units Sold", "Revenue", "Cost", "Gross Profit"]
    ])

    for row in top_products:
        report_rows.append(list(row))

    report_rows.extend([
        ["", ""],
        ["MONTHLY SALES", ""],
        ["Month", "Revenue", "Cost", "Gross Profit"]
    ])

    for row in monthly_sales:
        report_rows.append(list(row))

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerows(report_rows)

    response = make_response("\ufeff" + output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = (
        'attachment; filename="business_financial_report.csv"'
    )
    return response
if __name__ == "__main__":
    app.run(debug=True)